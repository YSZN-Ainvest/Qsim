import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsim.data import DayCube
from qsim.runner import run


class TinyMarket:
    def __init__(self,config):
        self.symbols=['A','B','C','D'];self.freq='1d'
        self.warmup_dates=['2023-12-27','2023-12-28']
        self.run_dates=['2023-12-29','2024-01-02','2024-01-03']

    def load_day(self,date):
        dates=self.warmup_dates+self.run_dates;idx=dates.index(date)
        price=10+np.arange(1,5)*.1*idx
        arrays={k:v[None,:,None] for k,v in dict(open_adj=price,close_adj=price,vwap_raw=price,
            vwap_adj=price,volume=np.full(4,100000),bar_complete=np.ones(4),vwap_outside_ohlc=np.zeros(4)).items()}
        cal=pd.DataFrame({'bar_start':[pd.Timestamp(date+' 09:30',tz='Asia/Shanghai')],
                          'bar_end':[pd.Timestamp(date+' 15:00',tz='Asia/Shanghai')]})
        return DayCube(date,cal,self.symbols,arrays,np.ones(4,bool),np.zeros(4),np.zeros(4),
                       np.full(4,100.),np.ones(4),np.ones(4),price)


class IntegrationTests(unittest.TestCase):
    def test_forecast_embargo_censoring_reports_and_no_dump(self):
        with tempfile.TemporaryDirectory() as tmp:
            config={'backtest':{'mode':'Cohort','start':'2023-12-29','end':'2024-01-03','allow_os':True,
                'holding_period':1,'forecast_period':1,'groups':2},
                'factors':[{'name':'mom','kind':'momentum','lookback':1,'weight':1}],
                'operations':{'min_cross_section':2},
                'output':{'dump':False,'directory':str(Path(tmp)/'not_created'),'positions':True}}
            with patch('qsim.runner.MarketData',TinyMarket):
                result=run(config)
            self.assertFalse((Path(tmp)/'not_created').exists())
            self.assertEqual(result['diagnostics'].label_status.tolist(),
                             ['complete','complete','right_censored'])
            self.assertEqual(set(result['summary'].period),{'2023','2024','ALL'})
            self.assertNotIn('forecast_split_embargo',result['audit'])
            np.testing.assert_allclose(result['daily'].pnl,
                result['daily'].hold_pnl+result['daily'].trade_pnl-result['daily'].tradecost,atol=1e-8)
            self.assertTrue(result['events'].turnover.gt(0).any())
            self.assertGreater(len(result['final_positions']),0)

    def test_unknown_st_fails_closed(self):
        class UnknownST(TinyMarket):
            def load_day(self,date):
                day=super().load_day(date);day.st[:]=np.nan;return day
        with patch('qsim.runner.MarketData',UnknownST):
            with self.assertRaisesRegex(ValueError,'historical ST status missing'):
                run({'backtest':{'mode':'Cohort','end':'2023-12-29'},'output':{'dump':False}})

    def test_user_tables_universe_st_and_neutralization(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)
            symbols=list('ABCD')
            table=pd.DataFrame({'available_at':['2023-12-28 16:00']*4,'order_book_id':symbols,
                'alpha':[.1,-.2,.4,-.3],'size':[1.,2.,3.,4.],'is_st':[False]*4,
                'in_universe':[True]*4})
            table.to_parquet(p/'inputs.parquet',index=False)
            config={'backtest':{'mode':'Cohort','end':'2024-01-03','allow_os':True,'groups':2},
                'factors':[{'name':'alpha','kind':'table','path':str(p/'inputs.parquet')}],
                'universe':{'kind':'table','path':str(p/'inputs.parquet')},
                'operations':{'st_path':str(p/'inputs.parquet'),'min_cross_section':2,
                    'exposures_path':str(p/'inputs.parquet'),'neutralize_styles':['size']},
                'output':{'dump':False}}
            with patch('qsim.runner.MarketData',TinyMarket):
                result=run(config)
            self.assertEqual(result['audit']['st_unknown_security_bars'],0)
            self.assertTrue(result['events'].turnover.gt(0).any())


if __name__=='__main__':unittest.main()
