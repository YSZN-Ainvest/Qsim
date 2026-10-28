from collections import deque
import numpy as np

from .pit import PITTable
from .operations import process


class FactorModel:
    """compute() sees only past completed bars and tables available at decision time."""
    def __init__(self, config, symbols):
        self.config, self.symbols = config, symbols
        self.specs = config['factors']
        self.lookback = max([f.get('lookback',1) for f in self.specs])
        self.history = deque(maxlen=self.lookback+1)
        self.tables = {}
        for f in self.specs:
            if f['kind'] == 'table':
                col = f.get('column',f['name'])
                self.tables[f['name']] = PITTable(f['path'],[col],symbols,config['backtest']['end'])
        o = config['operations']
        cols = list(dict.fromkeys(o['neutralize_styles']+o['neutralize_industries']))
        self.exposures = PITTable(o['exposures_path'],cols,symbols,config['backtest']['end']) if cols and o['exposures_path'] else None
        if cols and self.exposures is None:
            raise ValueError('Set operations.exposures_path to a historical exposure table')

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
