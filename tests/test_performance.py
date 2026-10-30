from concurrent.futures import ThreadPoolExecutor
import gc
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsim.data import MarketData
from qsim import source_cache
from qsim.config import load_config
from qsim.metrics import daily_curve, daily_curve_by_slot
from qsim.run_store import RunStore, DiskTable
from test_livetest import fixture


class PerformanceTests(unittest.TestCase):
    def test_daily_curve_matches_event_order_nan_and_reference_reductions(self):
        records=[]
        sums=['pnl','hold_pnl','trade_pnl','tradecost','turnover','net_turnover','exit_notional','age_notional']
        for date in ('2024-01-02','2024-01-03'):
            for j in range(3):
                for book in ('B','A'):
                    records.append(dict(timestamp=pd.Timestamp(date,tz='Asia/Shanghai')+pd.Timedelta(hours=j),
                        book=book,equity=100+j,long=80.,short=20.,nHold=j+1,previous_equity=100.,
                        **{key:(j+1)*.001 for key in sums}))
        events=pd.DataFrame(records)
        events.loc[0,'previous_equity']=np.nan
        events.loc[(events.book=='B') & (events.timestamp.dt.day==2),'tradecost']=np.nan
        events.loc[events.index[-1],'long']=np.nan
        events.loc[2,'hold_pnl']=np.inf
        rows=[]
        ref=events.assign(date=events.timestamp.dt.strftime('%Y-%m-%d'))
        for (book,date),part in ref.groupby(['book','date'],sort=True):
            row={k:part.iloc[-1][k] for k in ['equity','long','short','nHold']}
            row.update(book=book,date=date,previous_equity=part.previous_equity.iloc[0])
            row.update({key:part[key].sum() for key in sums})
            for key,source in [('return','pnl'),('tvr','turnover'),('net_tvr','net_turnover')]:
                row[key]=row[source]/row['previous_equity']
            rows.append(row)
        expected=pd.DataFrame(rows)
        expected['net']=expected.equity/expected.groupby('book').previous_equity.transform('first')
        original=events.copy(deep=True)
        pd.testing.assert_frame_equal(daily_curve(events),expected,check_dtype=False,rtol=1e-12,atol=1e-12)
        combined=pd.concat([events.assign(slot='0930'),events.assign(slot='1000')],ignore_index=True)
        actual=daily_curve_by_slot(combined)
        for slot in ('0930','1000'):
            pd.testing.assert_frame_equal(actual.loc[actual.slot.eq(slot)].drop(columns='slot').reset_index(drop=True),
                                          expected,check_dtype=False,rtol=1e-12,atol=1e-12)
        pd.testing.assert_frame_equal(events,original)

    def test_batched_tables_bound_rows_preserve_order_and_flush_csv(self):
        with tempfile.TemporaryDirectory() as temporary:
            cfg=load_config({'output':{'directory':temporary,'plots':False}})
            store=RunStore(cfg)
            expected=pd.DataFrame({'date':['2024-01-02']*19,'value':np.arange(19.)})
            try:
                store.add_chunks('sample',(expected.iloc[i:i+3] for i in range(0,19,3)),max_rows=7)
                self.assertEqual(store.parts['sample'],3)
                self.assertEqual(store.max_table_buffer_rows,7)
                # Reading while the run is active sees the latest flushed CSV.
                pd.testing.assert_frame_equal(pd.read_csv(store.directory/'sample.csv'),expected)
                table=DiskTable(store.directory/'sample.parquet',19)
                pd.testing.assert_frame_equal(table.to_pandas(),expected,check_dtype=False)
                self.assertEqual(store.counts['sample'],19)
            finally:
                store.close()
            self.assertFalse(store.csv_streams)

    def test_failed_partition_is_not_visible_as_complete_parquet(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=RunStore(load_config({'output':{'directory':temporary,'plots':False}}))
            store.add('sample',pd.DataFrame({'x':[1.]}))
            def broken(table,path,**kwargs):
                Path(path).write_bytes(b'incomplete')
                raise OSError('injected partial write')
            try:
                with patch('qsim.run_store.pq.write_table',side_effect=broken):
                    with self.assertRaisesRegex(OSError,'injected partial write'):
                        store.add('sample',pd.DataFrame({'x':[2.]}))
                self.assertEqual(len(list((store.directory/'sample.parquet').glob('part-*.parquet'))),1)
                self.assertEqual(DiskTable(store.directory/'sample.parquet',1).to_pandas().x.tolist(),[1.])
                self.assertEqual(store.counts['sample'],1)
            finally:
                store.close()

    def test_alignment_preserves_sparse_unsorted_fields_and_contiguous_bars(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg = fixture(root, minute=True)
            cfg['data']['cache_directory'] = str(root/'shared')
            cfg['backtest']['frequency'] = '0930-0940-5'
            date = cfg['backtest']['start']
            path = Path(cfg['data']['root'])/'prices/5m'/date[:4]/(date.replace('-', '')+'.parquet')
            values = pd.read_parquet(path)
            values = values.loc[values.order_book_id.ne('D')].iloc[::-1].reset_index(drop=True)
            values.loc[0, 'close_adj'] = np.nan
            values = values.drop(index=2)
            values.to_parquet(path, index=False)
            for shared in (False, True):
                cfg['data']['shared_cache'] = shared
                market = MarketData(cfg)
                day = market.load_day(date)
                self.assertEqual(len(day.calendar), 2)
                for category, columns in [('prices', ['open_adj', 'close_adj', 'bar_complete']),
                                          ('vwap', ['vwap_raw', 'vwap_adj', 'volume', 'total_turnover', 'vwap_outside_ohlc'])]:
                    frame = market._frame(category, market.freq, date)
                    for column in columns:
                        expected = frame.pivot(index='order_book_id', columns='bar_id', values=column).reindex(
                            index=market.symbols, columns=day.calendar.bar_id).to_numpy(dtype=float, na_value=np.nan)
                        np.testing.assert_array_equal(day.values[column][0], expected)
                        self.assertEqual(day.values[column].dtype, np.dtype('float64'))
                        for j in range(2):
                            self.assertTrue(day.bar(j)[column].flags.c_contiguous)
            # Even a duplicate outside the selected intraday window is invalid.
            duplicated = pd.concat([values, values.iloc[[0]]], ignore_index=True)
            duplicated.to_parquet(path, index=False)
            with self.assertRaisesRegex(ValueError, 'Duplicate market keys'):
                MarketData(cfg).load_day(date)

    def test_warm_cache_skips_build_lock_but_source_changes_rebuild(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'input.csv'
            pd.DataFrame({'order_book_id': ['A', 'B'], 'x': [1., 2.]}).to_csv(source, index=False)
            first = source_cache.shared_frame(source, root/'cache')
            with patch.object(source_cache, 'build_lock', side_effect=AssertionError('warm lock')):
                pd.testing.assert_frame_equal(first, source_cache.shared_frame(source, root/'cache'))
            pd.DataFrame({'order_book_id': ['A', 'B'], 'x': [1234., 2.]}).to_csv(source, index=False)
            with patch.object(source_cache, 'build_lock', wraps=source_cache.build_lock) as lock:
                updated = source_cache.shared_frame(source, root/'cache')
            self.assertEqual(lock.call_count, 1)
            self.assertEqual(updated.x.iloc[0], 1234.)
            self.assertEqual(len(list((root/'cache/arrow').glob('*.arrow'))), 2)
            # mmap-backed pandas arrays must release their file references before
            # TemporaryDirectory removes files on NFS (.nfs silly-rename).
            del first, updated
            gc.collect()

    def test_concurrent_cold_cache_has_one_complete_builder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'input.csv'
            expected = pd.DataFrame({'order_book_id': list('ABCDEF'), 'x': np.arange(6.)})
            expected.to_csv(source, index=False)
            with patch.object(source_cache, 'batches', wraps=source_cache.batches) as batches:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    values = list(pool.map(lambda _: source_cache.shared_frame(source, root/'cache', batch_rows=2), range(2)))
            self.assertEqual(batches.call_count, 1)
            for frame in values:
                pd.testing.assert_frame_equal(frame, expected)
            self.assertEqual(len(list((root/'cache/arrow').glob('*.arrow'))), 1)
            del values, frame
            gc.collect()


if __name__ == '__main__':
    unittest.main()
