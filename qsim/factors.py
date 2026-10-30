"""Shared-input factor DAG with deterministic, persistent thread pools."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import numpy as np

from .expressions import Expression,dependency_levels
from .operations import NeutralizationContext,process
from .pit_store import PITRegistry
from .resources import allowed_workers


class FactorModel:
    history_kind = 'finite'
    depends_on_account = False

    def __init__(self,config,symbols,input_policy=None):
        self.config,self.symbols=config,list(symbols)
        self.specs=config['factors']
        self.by_name={f['name']:f for f in self.specs}
        self.levels,self.expressions=dependency_levels(self.specs)
        self.combination=Expression(config['combine']['expression']) if config['combine']['expression'] else None
        self.lookback=max([f.get('lookback',1) for f in self.specs])
        self.history=deque(maxlen=self.lookback+1)
        self.registry=PITRegistry(config,symbols,input_policy)
        self.tables={f['name']:self.registry.get(f['path']) for f in self.specs if f['kind']=='table'}
        o=config['operations']
        self.exposures=self.registry.get(o['exposures_path']) if o['neutralize_styles'] or o['neutralize_industries'] else None
        if (o['neutralize_styles'] or o['neutralize_industries']) and self.exposures is None:
            raise ValueError('Set operations.exposures_path to a historical exposure table')
        self.effective_pools,self.resource_budget=allowed_workers(config['compute']['pools']) if config['compute']['pools']>1 else (1,{})
        self.pool=ThreadPoolExecutor(max_workers=self.effective_pools,thread_name_prefix='qsim-factor') if self.effective_pools>1 else None
        self.last_raw={};self.last_processed={};self.last_score=None

    @property
    def minimum_history(self):
        return max([f['lookback']+1 for f in self.specs if f['kind'] not in ('table','expression')] or [0])

    def observe(self,completed_close):
        self.history.append(np.array(completed_close,copy=True))

    def _map(self,function,items):
        return list(self.pool.map(function,items)) if self.pool else [function(item) for item in items]

    def compute(self,when,eligible):
        n=len(self.symbols)
        past=np.array(self.history) if self.history else np.empty((0,n))
        # Advance each physical source once, before parallel numerical work.
        snapshots={path:source.at(when) for path,source in self.registry.sources.items()}
        from pathlib import Path
        exposures=snapshots[str(Path(self.config['operations']['exposures_path']).resolve())] if self.exposures is not None else None
        raw={}
        def calculate(name):
            f=self.by_name[name];kind=f['kind']
            if kind=='expression':
                known={key:np.where(eligible,raw[key],np.nan) for key in self.expressions[name].names}
                return self.expressions[name].evaluate(known,n)
            if kind=='table':
                frame=snapshots[str(Path(f['path']).resolve())]
                return frame[f.get('column',name)].to_numpy(dtype=float,na_value=np.nan)
            value=np.full(n,np.nan);k=f['lookback']
            if len(past)>=k+1:
                block=past[-k-1:]
                valid=np.isfinite(block).all(axis=0)&(block>0).all(axis=0)
                if kind in ('momentum','reversal'):
                    value[valid]=(block[-1,valid]/block[0,valid]-1)*(-1 if kind=='reversal' else 1)
                else:
                    value[valid]=-np.std(block[1:,valid]/block[:-1,valid]-1,axis=0)
            return value
        for level in self.levels:
            aliases={};unique={}
            for name in level:
                spec=self.by_name[name]
                if spec['kind'] in ('momentum','reversal','volatility'): key=(spec['kind'],spec['lookback'])
                elif spec['kind']=='table': key=('table',str(Path(spec['path']).resolve()),spec.get('column',name))
                else: key=('expression',self.expressions[name].text)
                aliases[name]=key;unique.setdefault(key,name)
            computed=dict(zip(unique,self._map(calculate,list(unique.values()))))
            raw.update({name:computed[key] for name,key in aliases.items()})
        active=list(self.combination.names) if self.combination else [f['name'] for f in self.specs if float(f.get('weight',1))!=0]
        common=np.asarray(eligible,dtype=bool).copy()
        for name in active: common &= np.isfinite(raw[name])
        names=[f['name'] for f in self.specs]
        options=self.config['operations']
        # Scope is deliberately one decision, after advancing the PIT snapshot.
        # No future snapshot, mutable cache or lock enters the checkpoint.
        neutralization=(NeutralizationContext(exposures[options['neutralize_styles']],
                            exposures[options['neutralize_industries']])
                        if exposures is not None and len(names)>1 else None)
        transformed=self._map(lambda name:process(raw[name],common,options,exposures,
                              neutralization=neutralization),names)
        processed=dict(zip(names,transformed))
        values=raw if self.config['combine']['stage']=='raw' else processed
        if self.combination:
            score=self.combination.evaluate(values,n)
        else:
            score=np.sum([values[f['name']]*float(f.get('weight',1)) for f in self.specs if float(f.get('weight',1))!=0],axis=0)
        score=np.where(common & np.isfinite(score),score,np.nan)
        if common.sum()<self.config['operations']['min_cross_section']: score[:]=np.nan
        self.last_raw,self.last_processed,self.last_score=raw,processed,score
        return score

    def reference_price(self):
        return self.history[-1].copy() if self.history else np.full(len(self.symbols),np.nan)

    def state_dict(self):
        return deepcopy(dict(history=list(self.history),symbols=self.symbols,
            tables={path:source.state_dict() for path,source in self.registry.sources.items()}))

    def load_state_dict(self,state):
        if state['symbols']!=self.symbols or set(state['tables'])!=set(self.registry.sources):
            raise ValueError('Factor checkpoint schema mismatch')
        if len(state['history'])>self.history.maxlen: raise ValueError('Factor checkpoint history exceeds lookback')
        self.history.clear()
        for row in state['history']:
            if np.asarray(row).shape!=(len(self.symbols),): raise ValueError('Factor checkpoint security shape mismatch')
            self.history.append(np.array(row,copy=True))
        for path,source in self.registry.sources.items(): source.load_state_dict(state['tables'][path])

    def close(self):
        if getattr(self,'pool',None) is not None:
            self.pool.shutdown(wait=True,cancel_futures=True);self.pool=None
        if getattr(self,'registry',None) is not None: self.registry.close()

    def __del__(self):
        self.close()
