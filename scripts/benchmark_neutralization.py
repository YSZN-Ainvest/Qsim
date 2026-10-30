"""Deterministic per-decision Operations benchmark, not a whole backtest.

Run with native library threads limited and an external output directory.
The unchanged process() path is the per-factor reference. Every candidate
batch creates a fresh context; preparation is included in its timing.
"""
import argparse
import json
from pathlib import Path
import resource
import statistics
import sys
import time

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qsim.operations import NeutralizationContext, groups_from_score, process


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(319)
    n = 5000
    style_names = [f'style{i}' for i in range(5)]
    exposures = pd.DataFrame(rng.normal(size=(n, 5)), columns=style_names)
    exposures['industry'] = np.arange(n) % 30
    exposures.loc[0, 'style0'] = np.nan
    eligible = np.arange(n) % 19 != 0
    options = dict(min_cross_section=30, winsor_mad=3., standardize=True,
                   neutralize_styles=style_names, neutralize_industries=['industry'])
    records = []
    with threadpool_limits(limits=1):
        for masks in (1, 4):
            for count in (1, 20, 100):
                values = rng.normal(size=(count, n))
                for i, y in enumerate(values):
                    y[10:10+i % masks] = np.nan

                def execute(shared):
                    start = time.perf_counter()
                    context = (NeutralizationContext(exposures[style_names], exposures[['industry']])
                               if shared and count > 1 else None)
                    result = [process(y, eligible, options, exposures, neutralization=context) for y in values]
                    return time.perf_counter()-start, result

                # Prime both paths; never retain contexts across decisions.
                execute(False)
                execute(True)
                timings = {'reference': [], 'shared': []}
                max_error = 0.
                for repeat in range(3):
                    results = {}
                    for shared in ((False, True) if repeat % 2 == 0 else (True, False)):
                        elapsed, result = execute(shared)
                        key = 'shared' if shared else 'reference'
                        timings[key].append(elapsed)
                        results[key] = result
                    for a, b in zip(results['reference'], results['shared']):
                        np.testing.assert_array_equal(a, b)
                        np.testing.assert_array_equal(groups_from_score(a, 10), groups_from_score(b, 10))
                        valid = np.isfinite(a)
                        if not valid.any():
                            raise AssertionError('No finite comparison points')
                        max_error = max(max_error, float(np.max(np.abs(a[valid]-b[valid]))))
                medians = {key: statistics.median(times) for key, times in timings.items()}
                record = dict(factors=count, finite_masks=min(masks, count), seconds=timings,
                              medians=medians, speedup=medians['reference']/medians['shared'],
                              max_absolute_error=max_error, status='PASS')
                records.append(record)
                print(json.dumps(record), flush=True)
    result = dict(status='PASS', seed=319, securities=n, style_columns=5, industry_categories=30,
                  native_threads=1, repeats=3, records=records,
                  numpy=np.__version__, pandas=pd.__version__,
                  peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,
                  scope='Operations only, includes fresh preparation per decision; not end-to-end speedup')
    with output.open('x') as stream:
        json.dump(result, stream, indent=2, allow_nan=False)


if __name__ == '__main__':
    main()
