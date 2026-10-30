"""Build the documented Level2 L1 last-snapshot feature as a PIT table.

Only quote rows in [decision-9 seconds, decision) can affect this last-value
feature. Stream the required quote columns, collect those rows across ALL
batches, then apply the Level2 library's audit and metric implementations.
No TWAP or flow features are calculated from these short slices.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

CLOCKS = ('1000', '1030', '1100', '1330', '1400', '1430')


def calculate(date, symbols, source, library):
    sys.path.insert(0, str(Path(library)/'src'))
    from level2_features.quality import REQUIRED, audit
    from level2_features.features import _quote_metrics
    started = time.perf_counter()
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    path = Path(source)/date[:4]/date[:7].replace('-', '')/date.replace('-', '')/'quote.parquet'
    times = pd.DatetimeIndex([pd.Timestamp(f'{date} {c[:2]}:{c[2:]}', tz='Asia/Shanghai') for c in CLOCKS])
    ends = times.as_unit('ms').asi8
    rows = []
    count = 0
    # Only explicit exchange aliases; never strip suffixes or infer an exchange
    # from the numeric code. Restrict to the historical market's security axis.
    aliases = {'XSHE': 'SZ', 'XSHG': 'SH', 'XBEI': 'BJ'}
    mapping = {s.rsplit('.', 1)[0]+'.'+aliases.get(s.rsplit('.', 1)[1], s.rsplit('.', 1)[1]): s
               for s in symbols}
    if len(mapping) != len(symbols):
        raise ValueError('Ambiguous security aliases')
    before = path.stat() if path.exists() else None
    if before is not None:
        file = pq.ParquetFile(path)
        for batch in file.iter_batches(batch_size=65536, columns=REQUIRED['quote'], use_threads=False):
            stamp = batch.column('ts').cast(pa.timestamp('ms', tz='Asia/Shanghai')).cast(pa.int64()).to_numpy()
            which = np.searchsorted(ends, stamp, side='right')
            selected = (which < len(ends)) & (stamp >= ends[np.minimum(which, len(ends)-1)]-9000)
            # Strict right boundary: an event at t never enters the feature at t.
            selected &= stamp < ends[np.minimum(which, len(ends)-1)]
            if selected.any():
                part = batch.filter(pa.array(selected)).to_pandas()
                part = part.loc[part.wind_code.isin(mapping)]
                count += len(part)
                if count > 500000:
                    raise MemoryError('Selected quote row budget exceeded; no truncation allowed')
                rows.append(part)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeError(f'Input changed: {path}')
    results = pd.MultiIndex.from_product([times, symbols], names=['available_at', 'order_book_id']).to_frame(index=False)
    results['depth_imbalance_1_last'] = np.nan
    rejected = 0
    if rows:
        quote = pd.concat(rows, ignore_index=True)
        # All rows needed for each endpoint are together, including collisions
        # across row groups. Future endpoint rows cannot invalidate earlier keys.
        report = audit(quote, 'quote', date)
        bad = report.reject.to_numpy() | quote.duplicated(['wind_code', 'ts'], keep=False).to_numpy()
        value = _quote_metrics(quote, 1.)['depth_imbalance_1'].to_numpy(copy=True)
        value[bad] = np.nan
        rejected = int(bad.sum())
        stamp = quote.ts.dt.as_unit('ms').astype('int64').to_numpy()
        quote = quote.assign(available_at=times[np.searchsorted(ends, stamp, side='right')],
                             depth_imbalance_1_last=value)
        quote['wind_code'] = quote.wind_code.map(mapping)
        last = quote.sort_values('ts', kind='stable').drop_duplicates(['available_at', 'wind_code'], keep='last')
        values = last.set_index(['available_at', 'wind_code']).depth_imbalance_1_last
        results['depth_imbalance_1_last'] = values.reindex(pd.MultiIndex.from_frame(results[['available_at', 'order_book_id']])).to_numpy()
    # Explicit NaNs for every missing security/day prevent PIT's normal carry.
    metadata = dict(date=date, source=str(path), source_missing=before is None,
                    source_bytes=before.st_size if before else 0,
                    source_mtime_ns=before.st_mtime_ns if before else None,
                    selected_rows=count, rejected_rows=rejected,
                    finite_values=int(results.depth_imbalance_1_last.notna().sum()),
                    seconds=time.perf_counter()-started,
                    peak_rss_mib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024)
    return results, metadata


def worker(job):
    date, symbols, args = job
    output = Path(args.directory)/'data'/f'{date}.parquet'
    record = Path(args.directory)/'audit'/f'{date}.json'
    if output.exists() and record.exists():
        meta = json.loads(record.read_text())
        source = Path(meta['source'])
        stat = source.stat() if source.exists() else None
        if ((stat.st_size if stat else 0) != meta['source_bytes'] or
                (stat.st_mtime_ns if stat else None) != meta['source_mtime_ns']):
            raise ValueError(f'Resume source changed: {source}')
        return meta
    frame, meta = calculate(date, symbols, args.source, args.library)
    temporary = output.with_suffix('.pending')
    frame.to_parquet(temporary, index=False)
    temporary.replace(output)
    record.write_text(json.dumps(meta, indent=2))
    return meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True)
    parser.add_argument('--end', required=True)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--workers', type=int, default=1, choices=(1, 2, 3, 4, 5))
    parser.add_argument('--source', default='/mnt/nas/tickparquet')
    parser.add_argument('--market', default='/mnt/nas/qinml_cache/backtesting/20260930')
    parser.add_argument('--library', default='/home/qinml/level2_features')
    args = parser.parse_args()
    root = Path(args.directory)
    root.mkdir(parents=True, exist_ok=True)
    (root/'data').mkdir(exist_ok=True)
    (root/'audit').mkdir(exist_ok=True)
    settings = vars(args) | dict(clocks=CLOCKS, ttl_seconds=9, dtype='float64',
        semantics='last snapshot strictly before decision; explicit NaNs; no lunch/overnight carry',
        code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        library_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in (Path(args.library)/'src/level2_features').glob('*.py')})
    config = root/'build.json'
    if config.exists() and json.loads(config.read_text()) != json.loads(json.dumps(settings)):
        raise ValueError('Resume settings/source code differ')
    config.write_text(json.dumps(settings, indent=2))
    sec = pd.read_parquet(Path(args.market)/'securities.parquet')
    symbols = sorted(sec.loc[sec.type.eq('CS'), 'order_book_id'].unique())
    cal = pd.read_parquet(Path(args.market)/'calendar/30m.parquet', columns=['date'])
    dates = sorted(d for d in cal.date.unique() if args.start <= d <= args.end)
    jobs = ((date, symbols, args) for date in dates)
    records = []
    # map queues metadata only; each worker persists its own bounded daily frame.
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for meta in pool.map(worker, jobs, chunksize=1):
            records.append(meta)
            print(json.dumps(meta), flush=True)
    if not records or not any(r['finite_values'] for r in records):
        raise ValueError('No valid feature values; build cannot be marked complete')
    (root/'COMPLETE.json').write_text(json.dumps(dict(days=len(records),
        missing_dates=[r['date'] for r in records if r['source_missing']],
        finite_values=sum(r['finite_values'] for r in records)), indent=2))


if __name__ == '__main__':
    main()
