"""Compare the streamed last-value adapter with the Level2 reference library."""
import argparse
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd

from build_readme_l1_feature import CLOCKS, calculate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--library', default='/home/qinml/level2_features')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.library)/'src'))
    from level2_features.features import compute_features
    from level2_features.config import FeatureConfig
    from level2_features.quality import REQUIRED

    date = '2022-01-04'
    rows = []
    for symbol in ('000001.SZ', '600000.SH'):
        for clock in CLOCKS:
            end = pd.Timestamp(f'{date} {clock[:2]}:{clock[2:]}', tz='Asia/Shanghai')
            for offset in (-10, -9, -3, 0):
                row = dict(wind_code=symbol, ts=end+pd.Timedelta(seconds=offset),
                           price=110, acc_volume=100, acc_amount=11000)
                for depth in range(1, 11):
                    row.update({f'bid_price_{depth}':101-depth, f'ask_price_{depth}':119+depth,
                                f'bid_volume_{depth}':300+offset, f'ask_volume_{depth}':100})
                rows.append(row)
    quote = pd.DataFrame(rows)
    # Unresolved collisions, bad latest quote, exact TTL and stale snapshots.
    collision = quote.loc[quote.ts.eq(pd.Timestamp(date+' 10:29:57', tz='Asia/Shanghai'))]
    quote = pd.concat([quote, collision], ignore_index=True)
    quote.loc[quote.ts.eq(pd.Timestamp(date+' 10:59:57', tz='Asia/Shanghai')), 'bid_price_1'] = 200
    quote = quote.loc[~quote.ts.eq(pd.Timestamp(date+' 13:29:57', tz='Asia/Shanghai'))]
    quote = quote.loc[~quote.ts.isin([pd.Timestamp(date+' 13:59:57', tz='Asia/Shanghai'),
                                     pd.Timestamp(date+' 13:59:51', tz='Asia/Shanghai')])]
    quote = quote.sample(frac=1, random_state=17).reset_index(drop=True)
    empty = {}
    for kind in ('trade', 'order'):
        frame = pd.DataFrame(columns=REQUIRED[kind])
        frame['ts'] = pd.Series([], dtype='datetime64[ms, Asia/Shanghai]')
        empty[kind] = frame
    reference = compute_features(quote, empty['trade'], empty['order'], FeatureConfig(frequency='30min'),
                                 pd.Timestamp(date+' 09:30', tz='Asia/Shanghai'),
                                 pd.Timestamp(date+' 14:57', tz='Asia/Shanghai'), coverage_complete=True)
    reference = reference.loc[reference.window_end.dt.strftime('%H%M').isin(CLOCKS)].copy()
    reference['order_book_id'] = reference.wind_code.map({'000001.SZ':'000001.XSHE', '600000.SH':'600000.XSHG'})
    keys = ['available_at', 'order_book_id']
    reference = reference.rename(columns={'window_end':'available_at'})[keys+['depth_imbalance_1_last']].sort_values(keys)
    root = Path(tempfile.mkdtemp(prefix='readme-l1-validation-'))
    target = root/'2022/202201/20220104/quote.parquet'
    target.parent.mkdir(parents=True)
    # Tiny row groups deliberately split symbols and duplicate timestamps.
    quote.to_parquet(target, index=False, row_group_size=3)
    actual, meta = calculate(date, ['000001.XSHE', '600000.XSHG'], root, args.library)
    actual = actual.sort_values(keys)
    pd.testing.assert_frame_equal(actual.reset_index(drop=True), reference.reset_index(drop=True),
                                  check_dtype=False, check_exact=True)
    assert actual.depth_imbalance_1_last.notna().sum() == 6
    missing, missing_meta = calculate('2022-01-05', ['000001.XSHE', '600000.XSHG'], root, args.library)
    assert missing.depth_imbalance_1_last.isna().all() and missing_meta['source_missing']
    with Path(args.output).open('x') as stream:
        json.dump(dict(status='PASS', checks=['reference_values', 'unsorted_row_groups', 'timestamp_collisions',
            'strict_right_boundary', 'ttl_boundary', 'stale_quote', 'invalid_latest', 'security_aliases',
            'missing_day_explicit_nan'], metadata=meta, scratch=str(root)), stream, indent=2)
    print('PASS: L1 adapter equals Level2 reference; missing days explicit NaN')


if __name__ == '__main__':
    main()
