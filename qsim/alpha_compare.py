"""Strict comparison on the union of alpha keys; NaNs never supply evidence."""
import numpy as np
import pandas as pd

KEYS = ['timestamp', 'instrument', 'alpha_name']


def number(value):
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if np.isfinite(value) else ('Infinity' if value > 0 else '-Infinity')


def compare_alpha(reference, test, atol=1e-10, rtol=1e-8, max_samples=5):
    result = dict(status='ERROR', total_points=0, valid_points=0, missing_records=0,
                  duplicate_records=0, inconsistent_points=0, both_nan_points=0,
                  max_absolute_error=None, max_relative_error=None, first_difference=None, samples=[],
                  relative_error_definition='abs(test-reference)/abs(reference); 0/0=0, nonzero/0=Infinity')
    frames = []
    for name, frame in [('reference', reference), ('test', test)]:
        if not set(KEYS + ['value']) <= set(frame):
            result['reason'] = f'{name} output schema missing required columns'
            return result
        frame = frame.copy()
        if frame[KEYS].isna().any().any():
            result.update(status='FAIL', reason=f'{name} contains null keys')
            return result
        frame['timestamp'] = pd.to_datetime(frame.timestamp, utc=True)
        duplicated = frame.duplicated(KEYS, keep=False)
        result['duplicate_records'] += int(duplicated.sum())
        if duplicated.any() and result['first_difference'] is None:
            result['first_difference'] = dict(frame.loc[duplicated, KEYS].iloc[0], kind='duplicate', side=name)
        frames.append(frame.drop_duplicates(KEYS).set_index(KEYS)['value'])
    left, right = frames
    keys = left.index.union(right.index).sort_values()
    missing = ~keys.isin(left.index) | ~keys.isin(right.index)
    a = pd.to_numeric(left.reindex(keys), errors='raise').to_numpy(float)
    b = pd.to_numeric(right.reindex(keys), errors='raise').to_numpy(float)
    finite = np.isfinite(a) & np.isfinite(b) & ~missing
    both_nan = np.isnan(a) & np.isnan(b) & ~missing
    error = np.full(len(keys), np.nan)
    with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
        error[finite] = np.abs(b[finite] - a[finite])
        mismatch = finite & (error > atol + rtol * np.abs(a))
        # Any infinity (even matching infinities) or one-sided NaN is a failure.
        mismatch |= ~finite & ~both_nan & ~missing
        relative = error[finite] / np.abs(a[finite])
        relative[(a[finite] == 0) & (error[finite] == 0)] = 0.
    result.update(total_points=len(keys), valid_points=int(finite.sum()), missing_records=int(missing.sum()),
                  inconsistent_points=int(mismatch.sum()), both_nan_points=int(both_nan.sum()),
                  max_absolute_error=number(np.max(error[finite])) if finite.any() else None,
                  max_relative_error=number(np.max(relative)) if finite.any() else None)
    for i in np.flatnonzero(missing | mismatch)[:max_samples]:
        sample = dict(zip(KEYS, keys[i]))
        sample.update(kind='missing' if missing[i] else 'value', reference=number(a[i]), test=number(b[i]),
                      reference_present=bool(keys[i] in left.index), test_present=bool(keys[i] in right.index))
        result['samples'].append(sample)
        if result['first_difference'] is None:
            result['first_difference'] = sample
    if result['duplicate_records'] or missing.any() or mismatch.any():
        result.update(status='FAIL', reason='Duplicate/missing keys or incompatible alpha values')
    elif not finite.any():
        result.update(status='INSUFFICIENT_DATA', reason='No finite paired alpha values; matching NaNs are not evidence')
    else:
        result.update(status='PASS', reason=None)
    return result
