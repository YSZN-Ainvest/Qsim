from collections import deque
from copy import deepcopy
import numpy as np

from .pit import PITTable
from .operations import process


class FactorModel:
    """compute() sees only past completed bars and tables available at decision time."""
    history_kind = 'finite'
    depends_on_account = False

    def __init__(self, config, symbols, input_policy=None):
        self.config, self.symbols = config, symbols
        self.specs = config['factors']
        self.lookback = max([f.get('lookback',1) for f in self.specs])
        self.history = deque(maxlen=self.lookback+1)
        self.tables = {}
        for f in self.specs:
            if f['kind'] == 'table':
                col = f.get('column',f['name'])
                self.tables[f['name']] = PITTable(f['path'],[col],symbols,config['backtest']['end'],input_policy)
        o = config['operations']
        cols = list(dict.fromkeys(o['neutralize_styles']+o['neutralize_industries']))
        self.exposures = PITTable(o['exposures_path'],cols,symbols,config['backtest']['end'],input_policy) if cols and o['exposures_path'] else None
        if cols and self.exposures is None:
            raise ValueError('Set operations.exposures_path to a historical exposure table')

    @property
    def minimum_history(self):
        return max([f['lookback']+1 for f in self.specs if f['kind'] != 'table' and f.get('weight',1) != 0] or [0])

    def state_dict(self):
        return deepcopy({'history': list(self.history), 'symbols': self.symbols,
                         'tables': {k: v.state_dict() for k,v in self.tables.items()},
                         'exposures': self.exposures.state_dict() if self.exposures is not None else None})

    def load_state_dict(self, state):
        if state['symbols'] != self.symbols or set(state['tables']) != set(self.tables):
            raise ValueError('Factor checkpoint schema mismatch')
        if len(state['history']) > self.history.maxlen:
            raise ValueError('Factor checkpoint history exceeds lookback')
        self.history.clear()
        for row in state['history']:
            if np.asarray(row).shape != (len(self.symbols),):
                raise ValueError('Factor checkpoint security shape mismatch')
            self.history.append(np.array(row,copy=True))
        for name, table in self.tables.items():
            table.load_state_dict(state['tables'][name])
        if self.exposures is not None:
            self.exposures.load_state_dict(state['exposures'])

    def observe(self, completed_close):
        self.history.append(np.array(completed_close,copy=True))

    def compute(self, when, eligible):
        n = len(self.symbols)
        past = np.array(self.history) if self.history else np.empty((0,n))
        exposures = self.exposures.at(when) if self.exposures else None
        raw_factors = []
        weights = []
        for f in self.specs:
            if float(f.get('weight',1)) == 0:
                continue
            x = np.full(n,np.nan)
            k = f.get('lookback',1)
            if f['kind'] == 'table':
                x = self.tables[f['name']].at(when)[f.get('column',f['name'])].to_numpy(dtype=float,na_value=np.nan)
            elif len(past) >= k+1:
                block = past[-k-1:]
                valid = np.isfinite(block).all(axis=0) & (block>0).all(axis=0)
                if f['kind'] in ('momentum','reversal'):
                    x[valid] = (block[-1,valid]/block[0,valid]-1) * (-1 if f['kind']=='reversal' else 1)
                else:
                    x[valid] = -np.std(block[1:,valid]/block[:-1,valid]-1,axis=0)
            raw_factors.append(x)
            weights.append(float(f.get('weight',1)))
        common = eligible & np.isfinite(raw_factors).all(axis=0)
        transformed = [process(x,common,self.config['operations'],exposures)*w
                       for x,w in zip(raw_factors,weights)]
        # Every selected nonzero-weight factor must be present. No changing weights on missing data.
        score = np.sum(transformed,axis=0)
        return score

    def reference_price(self):
        return self.history[-1].copy() if self.history else np.full(len(self.symbols),np.nan)
