"""Resumable alpha-only event replay using the production loaders and FactorModel."""
import hashlib
import json
from pathlib import Path
import pickle
import random

import numpy as np
import pandas as pd

from .alpha_compare import KEYS
from .alpha_events import decision_time, eligible_at, signal_plan
from .config import load_config
from .data import MarketData
from .factors import FactorModel
from .input_policy import InputPolicy, file_hash
from .pit import PITTable


class InsufficientData(ValueError):
    pass


class Unsupported(ValueError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def code_version():
    return {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))}


class AlphaReplay:
    def __init__(self, config, policy=None, model_class=FactorModel, seed=0, max_records=1000000):
        self.config = load_config(config)
        self.policy = policy or InputPolicy()
        self.market = MarketData(self.config, input_policy=self.policy)
        random.seed(seed)
        np.random.seed(seed)
        self.model = model_class(self.config, self.market.symbols, input_policy=self.policy)
        if getattr(self.model, 'depends_on_account', True):
            raise Unsupported('Account-dependent alpha requires a checkpointable account adapter')
        o = self.config['operations']
        end = self.config['backtest']['end']
        self.st = PITTable(o['st_path'], ['is_st'], self.market.symbols, end, self.policy) if o['st_path'] else None
        u = self.config['universe']
        self.universe = PITTable(u['path'], ['in_universe'], self.market.symbols, end, self.policy) if u['kind'] == 'table' else None
        self.events = []
        self.warmup_bars = 0
        warmup = set(self.market.warmup_dates)
        for date in self.market.warmup_dates + self.market.run_dates:
            cal = self.market.day_calendar(date)
            plan = {} if date in warmup else signal_plan(cal, self.market.freq, self.config)[1]
            for j, row in cal.iterrows():
                self.events.append(dict(date=date, bar=j, when=decision_time(date,row,self.market.freq),
                                        end=row.bar_end, evaluate=j in plan))
            if date in warmup:
                self.warmup_bars += len(cal)
        if len(self.market.warmup_dates) < self.config['data']['warmup_days']:
            raise InsufficientData('Calendar does not supply the declared warmup trading days')
        if self.warmup_bars < self.model.minimum_history:
            raise InsufficientData(f'Need at least {self.model.minimum_history} observed bars; have {self.warmup_bars}')
        if sum(e['evaluate'] for e in self.events) * len(self.market.symbols) > max_records:
            raise Unsupported('Replay exceeds livetest.max_records; shorten dates/universe/grid or explicitly raise the budget')
        self.cursor = 0
        self.processed_events = 0

    def describe(self):
        return dict(start=self.config['backtest']['start'], run_dates=self.market.run_dates,
                    warmup_dates=self.market.warmup_dates, warmup_bars=self.warmup_bars,
                    minimum_history=self.model.minimum_history, history_kind=self.model.history_kind,
                    next_event=self.cursor, total_events=len(self.events))

    def run(self, until=None):
        rows = []
        loaded_date, day = None, None
        while self.cursor < len(self.events):
            event = self.events[self.cursor]
            if until is not None and event['when'] > until:
                break
            if loaded_date != event['date']:
                day = self.market.load_day(event['date'])
                loaded_date = event['date']
            if event['evaluate']:
                eligible, _ = eligible_at(day, event['when'], self.config, self.st, self.universe)
                values = self.model.compute(event['when'], eligible)
                if np.asarray(values).shape != (len(self.market.symbols),):
                    raise ValueError('Alpha output shape differs from security axis')
                rows.append(pd.DataFrame({'timestamp': event['when'], 'instrument': self.market.symbols,
                                          'alpha_name': 'combined_score', 'value': values}))
            self.model.observe(day.bar(event['bar'])['close_adj'])
            self.cursor += 1
            self.processed_events += 1
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=KEYS + ['value'])

    def save_checkpoint(self, path):
        """Trusted local checkpoint, after compute+observe of cursor-1, before cursor.

        Pickle contains pandas/numpy state: never load checkpoints from untrusted sources.
        Stores state, not replayed outputs; restore never recomputes the prefix.
        """
        if self.policy.mode != 'full':
            raise Unsupported('Checkpoint requires unmodified input')
        if not self.cursor:
            raise ValueError('No processed event to checkpoint')
        state = dict(schema=1, config=fingerprint(self.config), code=code_version(),
                     model_type=type(self.model).__module__ + '.' + type(self.model).__qualname__,
                     symbols=self.market.symbols, cursor=self.cursor, events=fingerprint(self.events),
                     boundary='after_compute_and_observe', last_event=self.events[self.cursor-1],
                     model=self.model.state_dict(),
                     st=self.st.state_dict() if self.st is not None else None,
                     universe=self.universe.state_dict() if self.universe is not None else None,
                     python_rng=random.getstate(), numpy_rng=np.random.get_state(), inputs=self.policy.files.copy())
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            pickle.dump(state, stream, protocol=5)
        return dict(path=str(path), sha256=file_hash(path), next_event=self.cursor,
                    checkpoint_time=str(self.events[self.cursor-1]['end']), boundary=state['boundary'])

    def load_checkpoint(self, path):
        with Path(path).open('rb') as stream:
            state = pickle.load(stream)  # Trusted, locally generated state only; see save_checkpoint.
        expected_type = type(self.model).__module__ + '.' + type(self.model).__qualname__
        if (state['schema'] != 1 or state['config'] != fingerprint(self.config) or
            state['code'] != code_version() or state['symbols'] != self.market.symbols or
            state['model_type'] != expected_type or state['events'] != fingerprint(self.events)):
            raise ValueError('Checkpoint code/config/model/event version mismatch')
        if state['boundary'] != 'after_compute_and_observe' or not 0 < state['cursor'] <= len(self.events):
            raise ValueError('Invalid checkpoint event boundary')
        if state['last_event'] != self.events[state['cursor']-1]:
            raise ValueError('Checkpoint event cursor mismatch')
        for path, digest in state['inputs'].items():
            if file_hash(path) != digest:
                raise ValueError(f'Checkpoint input changed: {path}')
        self.model.load_state_dict(state['model'])
        if self.st is not None:
            self.st.load_state_dict(state['st'])
        if self.universe is not None:
            self.universe.load_state_dict(state['universe'])
        self.cursor = state['cursor']
        self.policy.files.update(state['inputs'])
        random.setstate(state['python_rng'])
        np.random.set_state(state['numpy_rng'])


def checkpoint_worker(job_path):
    import os
    import pyarrow as pa
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    job = json.loads(Path(job_path).read_text())
    runtime = AlphaReplay(job['config'], seed=job['seed'], max_records=job['max_records'])
    runtime.load_checkpoint(job['checkpoint'])
    first = runtime.cursor
    result = runtime.run()
    result.to_parquet(job['output'], index=False)
    Path(job['metadata']).write_text(json.dumps(dict(pid=os.getpid(), first_event=first,
        processed_events=runtime.processed_events, last_cursor=runtime.cursor, inputs=runtime.policy.files), indent=2))


if __name__ == '__main__':
    import sys
    checkpoint_worker(sys.argv[1])
