from copy import deepcopy
import math

import pandas as pd


DEFAULTS = dict(times=[], starts=[], warmup_days=None, samples=3, seed=0,
                atol=1e-10, rtol=1e-8, max_samples=5, max_records=1000000,
                worker_timeout=300, report_directory='/mnt/nas/qinml_cache/qsim/result/livetest')


def timestamp(value):
    value = pd.Timestamp(value)
    if pd.isna(value):
        raise ValueError('Invalid livetest timestamp')
    return value.tz_localize('Asia/Shanghai') if value.tzinfo is None else value.tz_convert('Asia/Shanghai')


def parse_checks(value):
    if value == 'all':
        return ['f', 'p', 'c']
    items = value.split(',') if isinstance(value, str) else list(value)
    items = [x.strip() for x in items]
    if not items or any(x not in ('f', 'p', 'c') for x in items):
        raise ValueError('livetest must be f, p, c, a comma-separated combination, or all')
    return list(dict.fromkeys(items))


def options(value=None):
    value = value or {}
    if set(value) - set(DEFAULTS):
        raise ValueError(f'Unknown livetest options: {set(value)-set(DEFAULTS)}')
    result = deepcopy(DEFAULTS)
    result.update(value)
    for key in ('times', 'starts'):
        if not isinstance(result[key], list):
            raise ValueError(f'livetest.{key} must be a list')
        result[key] = [str(timestamp(x)) if key == 'times' else str(timestamp(x).date()) for x in result[key]]
        if len(set(result[key])) != len(result[key]):
            raise ValueError(f'Duplicate livetest.{key}')
    for key in ('samples', 'max_samples', 'max_records', 'worker_timeout'):
        if type(result[key]) is not int or result[key] < 1:
            raise ValueError(f'livetest.{key} must be a positive integer')
    if result['samples'] < 2:
        raise ValueError('livetest.samples must be >= 2 for automatic multiple-point checks')
    if type(result['seed']) is not int or not 0 <= result['seed'] < 2**32:
        raise ValueError('livetest.seed must be an integer in [0, 2**32)')
    if result['warmup_days'] is not None and (type(result['warmup_days']) is not int or result['warmup_days'] < 0):
        raise ValueError('livetest.warmup_days must be >= 0')
    for key in ('atol', 'rtol'):
        if isinstance(result[key], bool) or not math.isfinite(result[key]) or result[key] < 0:
            raise ValueError(f'livetest.{key} must be finite and nonnegative')
    if not isinstance(result['report_directory'], str) or not result['report_directory']:
        raise ValueError('livetest.report_directory must be a path')
    return result
