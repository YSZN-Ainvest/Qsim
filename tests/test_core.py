import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsim.config import load_config,schedule
from qsim.engine import Ledger
from qsim.factors import FactorModel
from qsim.metrics import daily_curve,report_metrics
from qsim.operations import neutralize,standardize,groups_from_score
from qsim.pit import PITTable,boolean_values
from qsim.runner import run


def cfg(**backtest):
    return load_config({'backtest':{'mode':'Cohort','groups':2,'holding_period':1,'capital':10000.,**backtest},
        'execution':{'participation':1.,'fee_bps':0.,'reject_bad_vwap':False},
        'operations':{'exclude_st':False,'min_cross_section':2},'output':{'dump':False}})


def fixtures(n=4,price=10.):
    a=lambda v:np.full(n,v,dtype=float)
    m={'vwap_adj':a(price),'vwap_raw':a(price),'close_adj':a(price),
       'volume':a(1000000),'bar_complete':a(1),'vwap_outside_ohlc':a(0)}
    d=SimpleNamespace(suspended=a(0),limit_up=a(11),limit_down=a(9),adjustment=a(1))
    return m,d


class CoreTests(unittest.TestCase):
    def test_os_gate_before_open(self):
        with patch('qsim.runner.MarketData') as source:
            with self.assertRaisesRegex(ValueError,'OS access denied'):
                run({'backtest':{'end':'2024-01-02'}})
            source.assert_not_called()
        with self.assertRaises(ValueError):
            load_config({'backtest':{'os_start':'2025-01-01'}})
        with self.assertRaises(ValueError):
            load_config({'backtest':{'allow_os':'false','end':'2024-01-02'}})

    def test_schedules(self):
        self.assertEqual(schedule('0800'),('1d',None,None))
        self.assertEqual(schedule('0930-1450-5'),('5m',570,890))
        for bad in ['0932-1450-5','0960-1450-5','1200-1230-5','0900-1450-5']:
            with self.assertRaises(ValueError):schedule(bad)

    def test_pit_no_future_and_false_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'f.csv'
            pd.DataFrame({'order_book_id':['A','A'],
                'available_at':['2023-01-03 08:00','2023-01-03 10:00'],'x':[2,999]}).to_csv(p,index=False)
            table=PITTable(p,['x'],['A'],'2023-12-31')
            self.assertTrue(pd.isna(table.at(pd.Timestamp('2023-01-03 07:59',tz='Asia/Shanghai')).x.iloc[0]))
            self.assertEqual(table.at(pd.Timestamp('2023-01-03 09:59',tz='Asia/Shanghai')).x.iloc[0],2)
            self.assertEqual(table.at(pd.Timestamp('2023-01-03 10:00',tz='Asia/Shanghai')).x.iloc[0],999)
        np.testing.assert_equal(boolean_values(pd.Series(['False','True',None])),[0,1,np.nan])

    def test_regression_residual_orthogonality(self):
        rng=np.random.default_rng(42);n=100
        x=rng.normal(size=n); industry=np.array(['a','b']*50)
        y=3*x+2*(industry=='b')+rng.normal(size=n)
        r=neutralize(y,pd.DataFrame({'size':x}),pd.DataFrame({'industry':industry}))
        self.assertAlmostEqual(float(r.sum()),0,places=10)
        self.assertAlmostEqual(float(r@x),0,places=10)
        self.assertAlmostEqual(float(r@(industry=='b')),0,places=10)
        z=standardize(r);self.assertAlmostEqual(float(z.mean()),0);self.assertAlmostEqual(float(z.std()),1)

    def test_t1_retry_and_no_forced_end_exit(self):
        l=Ledger(cfg(),list('ABCD'));m,d=fixtures()
        groups=np.array([0,0,1,1]);mask=np.ones(4,bool);ref=np.full(4,10.)
        l.step(0,0,'2023-01-03 09:35',m,d,groups,mask,ref)
        opened=l.q.copy()
        # Due within same day, but may not sell today's purchases.
        l.step(1,0,'2023-01-03 09:40',m,d,np.full(4,-1),mask,ref)
        np.testing.assert_allclose(l.q,opened)
        self.assertGreater(l.audit['t1_blocked_cells'],0)
        l.step(2,1,'2023-01-04 09:35',m,d,np.full(4,-1),mask,ref)
        np.testing.assert_allclose(l.q,0,atol=1e-10)
        np.testing.assert_allclose(l.equity,l.initial)

    def test_directional_limits_and_suspension(self):
        l=Ledger(cfg(),list('ABCD'));m,d=fixtures()
        m['vwap_raw'][2]=11;m['vwap_adj'][2]=11;m['close_adj'][2]=11
        d.suspended[3]=1
        l.step(0,0,'2023-01-03 09:35',m,d,np.array([0,0,1,1]),np.ones(4,bool),np.full(4,10.))
        self.assertEqual(l.q[1,2],0)  # Cannot buy at limit-up.
        self.assertEqual(l.q[1,3],0)  # Suspended.
        m,d=fixtures();l=Ledger(cfg(),list('ABCD'))
        l.step(0,0,'2023-01-03 09:35',m,d,np.array([0,0,1,1]),np.ones(4,bool),np.full(4,10.))
        old=l.q.copy();m['vwap_raw'][0]=9;m['vwap_adj'][0]=9;m['close_adj'][0]=9
        l.step(1,1,'2023-01-04 09:35',m,d,np.full(4,-1),np.ones(4,bool),np.full(4,10.))
        self.assertEqual(l.q[0,0],old[0,0])  # Long sell remains blocked at limit-down.
        self.assertEqual(l.q[-1,0],0)  # Short cover is a buy and is allowed.

    def test_cost_capacity_and_accounting(self):
        c=cfg();c['execution']['fee_bps']=10;c['execution']['participation']=.1
        l=Ledger(c,list('ABCD'));m,d=fixtures();m['volume'][:]=100
        l.step(0,0,'2023-01-03 09:35',m,d,np.array([0,0,1,1]),np.ones(4,bool),np.full(4,10.))
        self.assertLessEqual(np.max(np.abs(l.q)),10)
        events=pd.DataFrame(l.records);ls=events[events.book.eq('LS')].iloc[0]
        self.assertAlmostEqual(ls.pnl,-ls.tradecost)
        m['close_adj'][:]=10.5
        l.step(1,0,'2023-01-03 09:40',m,d,np.full(4,-1),np.ones(4,bool),np.full(4,10.))
        events=pd.DataFrame(l.records)
        np.testing.assert_allclose(events.pnl,events.hold_pnl+events.trade_pnl-events.tradecost,atol=1e-8)

    def test_split_adjusted_prices_preserve_pnl(self):
        l=Ledger(cfg(),list('ABCD'));m,d=fixtures()
        mask=np.ones(4,bool);g=np.array([0,0,1,1]);ref=np.full(4,10.)
        l.step(0,0,'2023-01-03',m,d,g,mask,ref)
        m['vwap_raw'][:]=5;d.adjustment[:]=2;d.limit_up[:]=5.5;d.limit_down[:]=4.5
        l.step(1,1,'2023-01-04',m,d,np.full(4,-1),mask,ref)
        np.testing.assert_allclose(l.equity,l.initial)

    def test_signal_only_observed_history(self):
        c=cfg();c['factors']=[{'name':'x','kind':'momentum','lookback':1,'weight':1}]
        f=FactorModel(c,list('ABCD'));mask=np.ones(4,bool)
        f.observe(np.array([10,10,10,10.]));f.observe(np.array([11,12,13,14.]))
        a=f.compute(pd.Timestamp('2023-01-03 09:30',tz='Asia/Shanghai'),mask)
        np.testing.assert_allclose(a,standardize(np.array([.1,.2,.3,.4])))
        np.testing.assert_equal(groups_from_score(a,2),[0,0,1,1])


if __name__=='__main__':unittest.main()
