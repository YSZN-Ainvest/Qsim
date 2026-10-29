"""Optional causality/start-stability/checkpoint checks of the actual factor score."""
from copy import deepcopy
from datetime import datetime
import gc
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import time

import numpy as np
import pandas as pd

from .alpha_compare import compare_alpha
from .alpha_replay import AlphaReplay, InsufficientData, Unsupported, code_version, fingerprint
from .config import load_config
from .factors import FactorModel
from .input_policy import InputPolicy, file_hash
from .livetest_config import options, parse_checks, timestamp

LIMITATIONS = [
    'Alpha means combined_score before grouping. Returns, labels, accounts and portfolio-weight fallback are not compared.',
    'Market bar fields become available at bar_end; daily price fields at 15:00. PIT sources use available_at (Shanghai timezone).',
    'Calendar/security axis are fixed control metadata. Cached universe/suspension/ST without publication times are assumed available at 08:00; historical publication correctness is unverified.',
    'Interventions precede local pivots and factor preprocessing, but cannot certify upstream cache/adjustment generation or model training that produced external table values.',
    'Only data accessed through MarketData/PITTable are covered; arbitrary external services, hidden I/O, custom RNG generators and account-dependent models need dedicated adapters.',
    'PASS verifies computation consistency within this scope, not predictive power or profitability.',
]


def choose(values, count):
    values = list(values)
    if not values:
        return []
    indices = np.linspace(0, len(values)-1, min(count,len(values)), dtype=int)
    return [values[i] for i in sorted(set(indices))]


def aggregate(cases):
    if not cases:
        return 'INSUFFICIENT_DATA'
    for status in ('ERROR', 'FAIL', 'UNSUPPORTED', 'INSUFFICIENT_DATA'):
        if any(c['status'] == status for c in cases):
            return status
    return 'PASS'


def exception_case(error, **metadata):
    status = 'INSUFFICIENT_DATA' if isinstance(error, InsufficientData) else 'UNSUPPORTED' if isinstance(error, Unsupported) else 'ERROR'
    return dict(status=status, reason=f'{type(error).__name__}: {error}', total_points=0, valid_points=0,
                missing_records=0, duplicate_records=0, inconsistent_points=0, both_nan_points=0,
                max_absolute_error=None, max_relative_error=None, first_difference=None, samples=[], **metadata)


def summarize_cases(cases, elapsed):
    result = dict(status=aggregate(cases), cases=cases, elapsed_seconds=elapsed)
    for key in ('total_points','valid_points','missing_records','duplicate_records','inconsistent_points','both_nan_points'):
        result[key] = sum(c.get(key,0) for c in cases)
    for key in ('max_absolute_error','max_relative_error'):
        values = [c[key] for c in cases if c.get(key) is not None]
        result[key] = 'Infinity' if 'Infinity' in values else max(values) if values else None
    result['first_difference'] = next((c['first_difference'] for c in cases if c.get('first_difference')),None)
    result['reason'] = None if result['status']=='PASS' else '; '.join(dict.fromkeys(c['reason'] for c in cases if c.get('reason')))
    return result


def json_value(value):
    if isinstance(value, (pd.Timestamp, Path)):
        return str(value)
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f'Cannot encode {type(value)}')


def run_livetest(config, checks='all', *, report_path=None, model_class=FactorModel, checkpoint_process=True):
    """Run serial, isolated alpha replays. Explicit opt-in only; does not place trades.

    model_class and checkpoint_process=False support deterministic fault-injection
    tests. The production CLI uses FactorModel and a fresh checkpoint subprocess.
    """
    started = time.monotonic()
    config = load_config(config)
    selected = parse_checks(checks)
    settings = options(config.get('livetest'))
    config = deepcopy(config)
    if settings['warmup_days'] is not None:
        config['data']['warmup_days'] = settings['warmup_days']
    if report_path is None:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        report_path = Path(settings['report_directory']) / f'livetest_{stamp}' / 'report.json'
    report_path = Path(report_path)
    if report_path.exists():
        raise FileExistsError(f'Refusing to overwrite livetest report: {report_path}')
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Keep checkpoint/worker artifacts separate even for named JSON reports.
    work = report_path.parent / (report_path.stem + '_state')
    work.mkdir(exist_ok=False)
    report = dict(schema_version=1, status='ERROR', requested=selected, config=config,
        settings=settings, seed=settings['seed'], code_version=code_version(), config_sha256=fingerprint(config),
        tolerance_reason='Float64 scores: atol=1e-10 and rtol=1e-8 allow rounding noise, not material signal changes; user overrides are recorded.',
        comparison_rule='abs(test-reference) <= atol + rtol*abs(reference)',
        nan_policy='Both NaN: skipped, not valid evidence. One NaN or any infinity: FAIL. No finite pairs: INSUFFICIENT_DATA.',
        alpha_names=['combined_score'], coverage_limitations=LIMITATIONS, checks={}, report_path=str(report_path))
    parent_python, parent_numpy = random.getstate(), np.random.get_state()
    inputs = {}
    def runtime(cfg, policy=None):
        return AlphaReplay(cfg, policy=policy, model_class=model_class, seed=settings['seed'], max_records=settings['max_records'])
    def collect(instance):
        for path, digest in instance.policy.files.items():
            if path in inputs and inputs[path] != digest:
                raise ValueError(f'Input version changed between runs: {path}')
            inputs[path] = digest
    def comparison(a, b, **meta):
        result = compare_alpha(a, b, settings['atol'], settings['rtol'], settings['max_samples'])
        result.update(meta)
        return result
    try:
        baseline_run = runtime(config)
        report['baseline'] = baseline_run.describe()
        baseline = baseline_run.run()
        collect(baseline_run)
        events = baseline_run.events
        history_kind = baseline_run.model.history_kind
        run_dates = baseline_run.market.run_dates
        del baseline_run
        available = sorted(baseline.loc[np.isfinite(baseline.value), 'timestamp'].unique())
        # c needs a subsequent evaluation. f requires a nonempty future input region.
        candidates = available[:-1]
        points = [timestamp(x) for x in settings['times']] if settings['times'] else choose(candidates, settings['samples'])
        report['selected_times'] = [str(x) for x in points]
        report['selection_rule'] = 'Explicit times, or evenly spaced finite-alpha decision times excluding the final evaluation; deterministic calendar order.'
        for check in selected:
            check_started = time.monotonic()
            cases = []
            try:
                if not available:
                    raise InsufficientData('Baseline contains no finite alpha outputs')
                if check in ('f', 'c') and not points:
                    raise InsufficientData('Need multiple evaluation events with a future/suffix interval')
                if check in ('f', 'c') and not settings['times'] and len(points)<2:
                    raise InsufficientData('Automatic selection needs at least two distinct check times with a suffix')
                if check == 'f':
                    for point in points:
                        if point not in available:
                            cases.append(exception_case(InsufficientData('Requested time is not a finite-alpha decision'), target=str(point)))
                            continue
                        for mode in ('truncate', 'perturb'):
                            case_started = time.monotonic()
                            try:
                                policy = InputPolicy(mode, point, settings['seed'])
                                other_run = runtime(config, policy)
                                other = other_run.run(until=point)
                                collect(other_run)
                                if not policy.changed_rows:
                                    raise InsufficientData('No input rows later than target were removed/perturbed')
                                expected = baseline.loc[baseline.timestamp <= point]
                                cases.append(comparison(expected, other, target=str(point), variant=mode,
                                    intervention_rows=policy.changed_rows, run=other_run.describe(),
                                    interval=[str(expected.timestamp.min()), str(point)],
                                    elapsed_seconds=time.monotonic()-case_started))
                                del other_run, other
                            except Exception as error:
                                cases.append(exception_case(error, target=str(point), variant=mode, elapsed_seconds=time.monotonic()-case_started))
                elif check == 'p':
                    if history_kind not in ('finite', 'recursive'):
                        raise Unsupported('Model declares all-history/start-date dependence; start invariance is not applicable')
                    if history_kind == 'recursive' and settings['warmup_days'] is None:
                        raise Unsupported('Recursive models require an explicit livetest.warmup_days and recorded tolerance')
                    starts = settings['starts'] or choose(run_dates, 3)
                    if len(starts) < 3 or not set(starts) <= set(run_dates):
                        raise InsufficientData('Need at least three distinct starts on trading days in the evaluation range')
                    starts = sorted(starts)
                    # Keep a meaningful suffix instead of defaulting to the final day.
                    if not settings['starts']:
                        starts = choose(run_dates[:max(3, len(run_dates)//2+1)], 3)
                    report['selected_starts'] = starts
                    common = timestamp(starts[-1])
                    anchor = None
                    for start in starts:
                        case_started = time.monotonic()
                        cfg = deepcopy(config)
                        cfg['backtest']['start'] = start
                        other_run = runtime(cfg)
                        other = other_run.run()
                        collect(other_run)
                        other = other.loc[other.timestamp >= common]
                        if anchor is None:
                            anchor = other
                            report['past_reference'] = other_run.describe()
                        else:
                            cases.append(comparison(anchor, other, start=start, reference_start=starts[0],
                                run=other_run.describe(), interval=[str(common), str(baseline.timestamp.max())],
                                elapsed_seconds=time.monotonic()-case_started))
                        del other_run
                else:
                    if checkpoint_process and model_class is not FactorModel:
                        raise Unsupported('Custom model requires its own isolated worker adapter; use fresh-instance mode only in tests')
                    for i, point in enumerate(points):
                        case_started = time.monotonic()
                        try:
                            expected = baseline.loc[baseline.timestamp > point]
                            if point not in available or expected.empty:
                                raise InsufficientData('Checkpoint must be a finite-alpha decision with later evaluations')
                            prefix = runtime(config)
                            prefix.run(until=point)
                            collect(prefix)
                            checkpoint = prefix.save_checkpoint(work/f'checkpoint_{i}.pkl')
                            next_event = prefix.cursor
                            remaining_events = len(prefix.events)-next_event
                            del prefix
                            gc.collect()
                            if checkpoint_process:
                                output, meta_path = work/f'restored_{i}.parquet', work/f'worker_{i}.json'
                                job = dict(config=config, seed=settings['seed'], max_records=settings['max_records'],
                                           checkpoint=checkpoint['path'], output=str(output), metadata=str(meta_path))
                                job_path = work/f'job_{i}.json'
                                job_path.write_text(json.dumps(job))
                                env = os.environ.copy()
                                env['PYTHONPATH'] = os.pathsep.join([str(Path(__file__).resolve().parent.parent)] + sys.path)
                                for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','ARROW_IO_THREADS'):
                                    env[key] = '1'
                                with (work/f'worker_{i}.log').open('w') as log:
                                    subprocess.run([sys.executable, '-m', 'qsim.alpha_replay', str(job_path)],
                                        env=env, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=settings['worker_timeout'])
                                other = pd.read_parquet(output)
                                meta = json.loads(meta_path.read_text())
                                if meta['pid'] == os.getpid():
                                    raise ValueError('Checkpoint worker did not create an independent process')
                                for path, digest in meta['inputs'].items():
                                    if path in inputs and inputs[path] != digest:
                                        raise ValueError(f'Restored worker input version changed: {path}')
                                    inputs[path] = digest
                            else:
                                restored = runtime(config)
                                restored.load_checkpoint(checkpoint['path'])
                                first_event = restored.cursor
                                other = restored.run()
                                collect(restored)
                                meta = dict(pid=os.getpid(), first_event=first_event,
                                            processed_events=restored.processed_events, last_cursor=restored.cursor)
                                del restored
                            if meta['first_event'] != next_event or meta['processed_events'] != remaining_events or meta['last_cursor'] != len(events):
                                raise ValueError('Resume repeated or skipped events')
                            cases.append(comparison(expected, other, target=str(point), checkpoint=checkpoint,
                                isolation='subprocess' if checkpoint_process else 'fresh_instance', worker=meta,
                                interval=[str(expected.timestamp.min()), str(expected.timestamp.max())],
                                elapsed_seconds=time.monotonic()-case_started))
                            del other
                        except Exception as error:
                            cases.append(exception_case(error, target=str(point), elapsed_seconds=time.monotonic()-case_started))
            except Exception as error:
                cases.append(exception_case(error))
            report['checks'][check] = summarize_cases(cases,time.monotonic()-check_started)
            print(f"livetest {check}: {report['checks'][check]['status']} ({len(cases)} comparisons)", flush=True)
        report['status'] = aggregate(list(report['checks'].values()))
    except Exception as error:
        report['checks'] = {c: summarize_cases([exception_case(error)],time.monotonic()-started) for c in selected}
        report['status'] = aggregate(list(report['checks'].values()))
    finally:
        random.setstate(parent_python)
        np.random.set_state(parent_numpy)
        report['data_version'] = inputs
        report['elapsed_seconds'] = time.monotonic()-started
        report['exit_code'] = 0 if report['status'] == 'PASS' else 1
        with report_path.open('x') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, default=json_value, allow_nan=False)
        print(f"livetest overall: {report['status']}; report: {report_path}", flush=True)
    return report
