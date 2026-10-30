"""Profile a bounded real backtest without changing its numerical implementation.

Run from an installed checkout. --directory must be a new artifact directory.
Factor/library threads are fixed to one so phase timings do not overlap.
"""
import argparse
from collections import defaultdict
from contextlib import ExitStack
import cProfile
from functools import wraps
import json
from pathlib import Path
import pstats
import sys
import time
import traceback
from unittest.mock import patch

# Match the production CLI's NumPy/pandas-before-Arrow import order. Some Conda
# installations otherwise resolve a system libstdc++ incompatible with plotting.
from qsim.config import load_config
from qsim.runner import run
from qsim.run_store import resources
import numpy as np
import pandas as pd
import pyarrow as pa
import qsim


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--start')
    parser.add_argument('--end')
    parser.add_argument('--frequency')
    parser.add_argument('--warmup-days', type=int)
    parser.add_argument('--mode')
    parser.add_argument('--cache-directory')
    parser.add_argument('--memory-mb', type=int, default=2048)
    parser.add_argument('--profile', action='store_true')
    args = parser.parse_args()
    directory = Path(args.directory).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    cfg = load_config(args.config)
    for key in ('start', 'end', 'frequency', 'mode'):
        if getattr(args, key) is not None:
            cfg['backtest'][key] = getattr(args, key)
    if args.warmup_days is not None:
        cfg['data']['warmup_days'] = args.warmup_days
    if args.cache_directory is not None:
        cfg['data']['cache_directory'] = str(Path(args.cache_directory).resolve())
    cfg['compute'].update(pools=1, memory_per_job_mb=args.memory_mb)
    cfg['output'].update(directory=str(directory/'runs'), name='profile')
    cfg = load_config(cfg)
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    from qsim import factors, stream_runner, report
    from qsim.data import MarketData
    from qsim.engine import Ledger
    from qsim.target_engine import TargetLedger
    from qsim.stats import StatsEvery, StatsInterval
    from qsim.online_metrics import OnlineMetrics
    from qsim.run_store import RunStore
    stages = defaultdict(lambda: dict(wall=0., cpu=0., calls=0))
    inclusive = defaultdict(lambda: dict(wall=0., cpu=0., calls=0))
    active = []

    def timed(name, function):
        @wraps(function)
        def wrapper(*a, **kw):
            wall, cpu = time.perf_counter(), time.process_time()
            frame = [0., 0.]
            active.append(frame)
            try:
                return function(*a, **kw)
            finally:
                elapsed, used = time.perf_counter()-wall, time.process_time()-cpu
                active.pop()
                stages[name]['wall'] += elapsed-frame[0]
                stages[name]['cpu'] += used-frame[1]
                stages[name]['calls'] += 1
                inclusive[name]['wall'] += elapsed
                inclusive[name]['cpu'] += used
                inclusive[name]['calls'] += 1
                if active:
                    active[-1][0] += elapsed
                    active[-1][1] += used
        return wrapper

    hooks = [(MarketData, 'load_day', 'data_loading'),
             (factors.FactorModel, 'compute', 'factor_computation'),
             (factors, 'process', 'operations'),
             (stream_runner, 'groups_from_score', 'portfolio_construction'),
             (stream_runner, 'portfolio_weights', 'portfolio_construction'),
             (TargetLedger, 'rebalance', 'account_simulation'),
             (Ledger, 'step', 'account_simulation'), (Ledger, 'end_day', 'account_simulation'),
             (stream_runner, 'daily_curve', 'statistics'),
             (StatsEvery, 'observe', 'statistics'), (StatsEvery, 'tables', 'statistics'),
             (StatsInterval, 'observe', 'statistics'), (StatsInterval, 'tables', 'statistics'),
             (OnlineMetrics, 'add_daily', 'statistics'),
             (OnlineMetrics, 'add_diagnostic', 'statistics'), (OnlineMetrics, 'tables', 'statistics'),
             (RunStore, 'add', 'output_write'), (RunStore, 'finish', 'output_write'),
             (RunStore, 'progress', 'progress_logging'),
             (report, 'plot_results', 'plotting')]
    if hasattr(RunStore, 'add_chunks'):
        hooks.append((RunStore, 'add_chunks', 'output_write'))
    if hasattr(stream_runner, 'daily_curve_by_slot'):
        hooks.append((stream_runner, 'daily_curve_by_slot', 'statistics'))
    profiler = cProfile.Profile() if args.profile else None
    before = resources()
    result = None
    error = None
    with ExitStack() as stack:
        for owner, attr, name in hooks:
            stack.enter_context(patch.object(owner, attr, timed(name, getattr(owner, attr))))
        wall, cpu = time.perf_counter(), time.process_time()
        if profiler:
            profiler.enable()
        try:
            result = run(cfg)
        except Exception:
            error = traceback.format_exc()
        finally:
            if profiler:
                profiler.disable()
                profiler.dump_stats(directory/'calls.pstats')
        elapsed, cpu_elapsed = time.perf_counter()-wall, time.process_time()-cpu
    stages['unattributed'] = dict(wall=elapsed-sum(x['wall'] for x in stages.values()),
                                  cpu=cpu_elapsed-sum(x['cpu'] for x in stages.values()), calls=0)
    top = []
    if profiler:
        stats = pstats.Stats(profiler)
        for (file, line, name), (primitive, calls, own, cumulative, _) in sorted(
                stats.stats.items(), key=lambda x: x[1][3], reverse=True)[:100]:
            top.append(dict(file=file, line=line, function=name, primitive_calls=primitive,
                            calls=calls, self_seconds=own, cumulative_seconds=cumulative))
    payload = dict(status='ERROR' if error else 'PASS', error=error,
                   config=cfg, profile_enabled=bool(profiler), wall_seconds=elapsed,
                   cpu_seconds=cpu_elapsed, stages=dict(stages), inclusive_stages=dict(inclusive),
                   versions=dict(python=sys.version, numpy=np.__version__, pandas=pd.__version__,
                                 pyarrow=pa.__version__, qsim_root=str(Path(qsim.__file__).parent)),
                   resources_before=before, resources_after=resources(), top_cumulative=top,
                   output_directory=str(result['output_directory']) if result else None,
                   note='cProfile overhead is included when enabled; compare speed using unprofiled fresh processes. '
                        'Stages subtract nested hooks and reconcile to total wall time; inclusive stages overlap.')
    (directory/'profile.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({k:payload[k] for k in ('status','profile_enabled','wall_seconds','cpu_seconds','stages','output_directory')}, indent=2))
    if error:
        print(error, file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
