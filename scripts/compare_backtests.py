"""Compare every persisted logical table and execution counters across two runs."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa


def compare(reference, candidate):
    reference, candidate = Path(reference), Path(candidate)
    for root in (reference, candidate):
        if not (root/'COMPLETE').is_file():
            raise ValueError(f'Incomplete run: {root}')
    configs = [json.loads((root/'config.json').read_text()) for root in (reference, candidate)]
    for cfg in configs:
        for name in ('name', 'directory'):
            cfg['output'].pop(name, None)
        cfg['data'].pop('cache_directory', None)
    if configs[0] != configs[1]:
        raise ValueError('Numerical configurations differ')
    manifests = [json.loads((root/'manifest.json').read_text())['tables'] for root in (reference, candidate)]
    if manifests[0].keys() != manifests[1].keys():
        raise AssertionError('Output table sets differ')
    records = []
    for name in manifests[0]:
        if manifests[0][name]['rows'] != manifests[1][name]['rows']:
            raise AssertionError(f'{name}: output row count changed')
        a, b = [pd.read_parquet(root/manifests[i][name]['path']) for i, root in enumerate((reference, candidate))]
        if set(a.columns) != set(b.columns):
            raise AssertionError(f'{name}: column sets differ')
        b = b[a.columns]
        keys = [k for k in a if not pd.api.types.is_numeric_dtype(a[k])]
        if keys:
            a = a.sort_values(keys, kind='stable').reset_index(drop=True)
            b = b.sort_values(keys, kind='stable').reset_index(drop=True)
        else:
            a, b = a.reset_index(drop=True), b.reset_index(drop=True)
        pd.testing.assert_frame_equal(a, b, check_dtype=False, check_exact=False, rtol=1e-10, atol=1e-8)
        numeric = a.select_dtypes(include='number').columns
        maximum = 0.
        for key in numeric:
            x, y = a[key].to_numpy(float), b[key].to_numpy(float)
            finite = np.isfinite(x) & np.isfinite(y)
            if finite.any():
                maximum = max(maximum, float(np.max(np.abs(x[finite]-y[finite]))))
        records.append(dict(table=name, rows=len(a), max_abs_error=maximum))
        del a, b
    audits = [json.loads((root/'audit.json').read_text()) for root in (reference, candidate)]
    for key in ('execution', 'forecast_right_censored', 'no_signal_bars', 'st_unknown_security_bars',
                'skipped_delay_signals', 'portfolio_count', 'days', 'bars', 'symbols'):
        if audits[0].get(key) != audits[1].get(key):
            raise AssertionError(f'Audit differs: {key}')
    return records


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference')
    parser.add_argument('candidate')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    target = Path(args.output)
    if target.exists():
        raise FileExistsError(target)
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    try:
        report = dict(status='PASS', tables=compare(args.reference, args.candidate))
    except Exception as error:
        report = dict(status='FAIL', error=str(error))
    target.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report['status'] == 'PASS' else 1)
