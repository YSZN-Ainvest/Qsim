import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from qsim.data import MarketData
from qsim.factors import FactorModel
from qsim.runner import run
from qsim.target_engine import TargetLedger
from test_livetest import fixture


class PrecisionTests(unittest.TestCase):
    def test_float64_signal_cash_and_persisted_outputs_keep_small_differences(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cfg = fixture(root)
            cfg['data']['cache_directory'] = str(root/'shared')
            cfg['backtest']['capital'] = 100_000_000.01
            market = MarketData(cfg)
            day = market.load_day(market.run_dates[0])
            for values in day.values.values():
                self.assertEqual(values.dtype, np.dtype('float64'))
            # These distinct source values collapse to one value in float32.
            values = 1 + np.arange(1, 7, dtype=np.float64)*1e-9
            self.assertEqual(len(np.unique(values.astype(np.float32))), 1)
            table = root/'precision.parquet'
            pd.DataFrame(dict(order_book_id=market.symbols,
                              available_at='2022-12-01 07:30', value=values)).to_parquet(table, index=False)
            cfg['factors'] = [dict(name='precise', kind='table', path=str(table), column='value', weight=1.)]
            cfg['combine']['expression'] = '<precise>'
            model = FactorModel(cfg, market.symbols)
            try:
                score = model.compute(pd.Timestamp(market.run_dates[0]+' 08:00', tz='Asia/Shanghai'),
                                      np.ones(len(values), dtype=bool))
                np.testing.assert_array_equal(model.last_raw['precise'], values)
                self.assertEqual(model.last_raw['precise'].dtype, np.dtype('float64'))
                self.assertEqual(model.last_processed['precise'].dtype, np.dtype('float64'))
                self.assertEqual(score.dtype, np.dtype('float64'))
                self.assertEqual(len(np.unique(score)), len(values))
            finally:
                model.close()
            ledger = TargetLedger(cfg, market.symbols)
            for name in ('cash', 'q', 'last', 'equity'):
                self.assertEqual(getattr(ledger, name).dtype, np.dtype('float64'))
            self.assertAlmostEqual(ledger.cash[0]-100_000_000, .01, places=7)
            cfg['output'].update(dump=True, stream=True, plots=False, factors=True,
                                 directory=str(root/'results'))
            result = run(cfg)
            output = result['output_directory']
            for name, columns in [('daily', ['equity', 'pnl', 'return', 'net']),
                                  ('events', ['equity', 'pnl', 'tradecost']),
                                  ('factors', ['value'])]:
                schema = pq.read_schema(next((output/(name+'.parquet')).glob('part-*.parquet')))
                for column in columns:
                    self.assertEqual(schema.field(column).type, pa.float64())
            raw = result['factors'].to_pandas().query("stage == 'raw'")
            first = raw.loc[raw.timestamp.eq(raw.timestamp.min())].sort_values('instrument')
            np.testing.assert_array_equal(first.value.to_numpy(), values)
