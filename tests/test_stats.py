import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import pandas as pd

from qsim.config import load_config
from qsim.data import DayCube
from qsim.runner import run
from qsim.stats import StatsEvery,StatsInterval,every_pnl,interval_pnl,aggregate_window
from qsim.target_engine import TargetLedger


def config(mode='StatsEvery',**stats):
    return load_config({'backtest':{'mode':mode,'start':'2023-12-04','end':'2023-12-06',
        'frequency':'0930-0945-5','groups':2,'forecast_period':1,'capital':10000},
        'stats':stats,'execution':{'fee_bps':0,'participation':1},
        'operations':{'min_cross_section':2},'output':{'dump':False},
        'factors':[{'name':'mom','kind':'momentum','lookback':1,'weight':1}]})


class SmallMarket:
    def __init__(self,cfg):
        self.symbols=list('ABCD');self.freq='5m'
        self.warmup_dates=['2023-11-30','2023-12-01']
        self.run_dates=['2023-12-04','2023-12-05','2023-12-06']

    def load_day(self,date):
        d=(self.warmup_dates+self.run_dates).index(date)
        base=np.arange(1,5)*10.
        values=[]
        for j in range(3):
            price=base*(1+.01*d+.002*np.sin(np.arange(4)+j+d))
            vol=np.arange(1,5)*100000.
            values.append(dict(open_adj=price-.01,close_adj=price+.01,vwap_adj=price,
                vwap_raw=price,volume=vol,total_turnover=price*vol,
                bar_complete=np.ones(4),vwap_outside_ohlc=np.zeros(4)))
        tensors={k:np.array([v[k] for v in values]).T[None,:,:] for k in values[0]}
        start=pd.Timestamp(date+' 09:30',tz='Asia/Shanghai')
        cal=pd.DataFrame({'bar_start':[start+pd.Timedelta(minutes=5*j) for j in range(3)],
                          'bar_end':[start+pd.Timedelta(minutes=5*(j+1)) for j in range(3)]})
        return DayCube(date,cal,self.symbols,tensors,np.ones(4,bool),np.zeros(4),np.zeros(4),
            np.full(4,1000.),np.ones(4),np.ones(4),values[-1]['close_adj']+.05)


class StatsTests(unittest.TestCase):
    def simulate(self,cfg):
        with patch('qsim.stats_runner.MarketData',SmallMarket):
            return run(cfg)

    def test_every_document_formula(self):
        hold,trade=every_pnl(np.array([[.5]]),np.array([[.25]]),
                            np.array([100.]),np.array([110.]),np.array([121.]))
        np.testing.assert_allclose(hold,[.05]);np.testing.assert_allclose(trade,[.025])

    def test_missing_active_price_not_zero_profit(self):
        hold,trade=every_pnl(np.array([[1.],[0.]]),np.zeros((2,1)),
                            np.array([np.nan]),np.array([10.]),np.array([10.]))
        self.assertTrue(np.isnan(hold[0]));self.assertEqual(hold[1],0)

    def test_every_slots_do_not_share_alpha(self):
        calculator=StatsEvery(config(),list('ABCD'))
        a=np.zeros((4,4));a[:,0]=1;b=np.zeros((4,4));b[:,1]=1
        price=np.full(4,100.)
        for slot,alpha in [('0930',a),('0935',b)]:
            calculator.observe('2023-12-04',slot,alpha,price,price,price)
        calculator.observe('2023-12-05','0930',a,price,price,price)
        row=calculator.tables()['stats_slots'].query('date == "2023-12-05" and book == "G01"').iloc[0]
        self.assertEqual(row.tvr,0)

    def test_isTradeT1_controls_tvr_only(self):
        a=self.simulate(config(isTradeT1=True));b=self.simulate(config(isTradeT1=False))
        pd.testing.assert_frame_equal(a['daily'],b['daily'])
        pd.testing.assert_frame_equal(a['stats_slots'],b['stats_slots'])
        np.testing.assert_allclose(b['stats_tvr'].tvr,a['stats_tvr'].tvr*3)

    def test_fixed_time_portfolio_is_independent_of_other_times(self):
        all_slots=self.simulate(config())
        one=self.simulate(config(signal_times=['0935']))
        a=all_slots['slot_daily'].query('slot == "0935"').reset_index(drop=True)
        b=one['slot_daily'].reset_index(drop=True)
        pd.testing.assert_frame_equal(a,b)
        self.assertEqual(all_slots['audit']['portfolio_count'],3)
        self.assertEqual(one['audit']['portfolio_count'],1)
        pooled=all_slots['daily']
        np.testing.assert_allclose(pooled.tvr,pooled.turnover/pooled.previous_equity)
        np.testing.assert_allclose(pooled.net_tvr,pooled.net_turnover/pooled.previous_equity)

    def test_execution_delay_and_no_cross_day_order(self):
        r=self.simulate(config(nIntvDelay=2))
        first=r['stats_slots'].query('slot == "0930"').iloc[0]
        self.assertIn('09:40:00',first.execution_end)
        self.assertEqual(r['audit']['portfolio_count'],2)
        self.assertEqual(r['audit']['skipped_delay_signals'],3)
        self.assertEqual(r['diagnostics'].label_status.iloc[-1],'complete')

    def test_amount_weighted_vwap(self):
        d=SmallMarket(config()).load_day('2023-12-04')
        d.values['volume'][0,:,0]=1;d.values['volume'][0,:,1]=3
        d.values['total_turnover'][0,:,0]=10;d.values['total_turnover'][0,:,1]=60
        result=aggregate_window(d,[0,1])
        np.testing.assert_allclose(result['vwap_raw'],17.5)
        self.assertNotEqual(result['vwap_raw'][0],15.)

    def test_interval_direction_and_scaling(self):
        hold,trade=interval_pnl(np.array([[.5]]),np.array([[1.]]),np.array([100.]),
                               np.array([121.]),np.array([110.]))
        np.testing.assert_allclose(hold,[.105]);np.testing.assert_allclose(trade,[.05])
        calculator=StatsInterval(config('StatsInterval',nbar=2),list('ABCD'))
        calculator.last_alpha[:]=.125
        alpha=np.full((4,4),.25)
        calculator.observe('2023-12-04','0930',alpha,np.full(4,100.),np.full(4,110.),
                           np.full(4,121.),first=True,is_last=False,actual_bars=2)
        r=calculator.tables()['stats_intervals'].iloc[0]
        self.assertAlmostEqual(r.reported_return,.155/2)

    def test_interval_tail_noend_and_nan_ic_policy(self):
        r=self.simulate(config('StatsInterval',nbar=2))
        intervals=r['stats_intervals'].query('book == "LS"')
        self.assertEqual(intervals.actual_bars.tolist(),[2,1]*3)
        np.testing.assert_allclose(intervals.reported_return,(intervals.raw_return-intervals.cost_return)/2)
        self.assertTrue((r['stats_sumnoend'].intervals==1).all())
        self.assertEqual(r['audit']['portfolio_count'],1)
        self.assertEqual(r['diagnostics'].label_status.iloc[-1],'right_censored')

    def test_cleareod_is_not_an_actual_liquidation(self):
        a=self.simulate(config('StatsInterval',cleareod=False))
        b=self.simulate(config('StatsInterval',cleareod=True))
        pd.testing.assert_frame_equal(a['daily'],b['daily'])
        self.assertFalse(a['stats_intervals'].equals(b['stats_intervals']))

    def test_actual_t1_independent_from_statistics_flag(self):
        c=config(isTradeT1=False);ledger=TargetLedger(c,list('ABCD'))
        day=SmallMarket(c).load_day('2023-12-04');m=day.bar(0);ref=m['close_adj']
        w=np.zeros((4,4));w[:3,0]=1;w[3,1]=-1
        ledger.rebalance(0,day.calendar.bar_end[0],m,day,w,ref)
        original=ledger.q.copy()
        ledger.rebalance(0,day.calendar.bar_end[1],day.bar(1),day,np.zeros_like(w),ref)
        np.testing.assert_allclose(ledger.q,original)
        ledger.rebalance(1,day.calendar.bar_end[2],day.bar(2),day,np.zeros_like(w),ref)
        np.testing.assert_allclose(ledger.q,0,atol=1e-10)

    def test_invalid_legacy_holding_period_not_silently_ignored(self):
        with self.assertRaisesRegex(ValueError,'holding_period belongs'):
            load_config({'backtest':{'mode':'StatsEvery','holding_period':5}})

    def test_new_modes_keep_split_and_tail_labels_unknown(self):
        for mode in ['StatsEvery','StatsInterval']:
            c=config(mode);c['backtest'].update(allow_os=True,os_start='2023-12-05',forecast_period=3)
            r=self.simulate(c)
            self.assertEqual(r['diagnostics'].label_status.tolist(),
                             ['split_embargo']*3+['complete']*3+['right_censored']*3)
            self.assertTrue(r['diagnostics'].iloc[:3].rank_ic.isna().all())

    def test_interval_undefined_rank_ic_zero_only_after_maturity(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'constant.csv'
            pd.DataFrame({'order_book_id':list('ABCD'),'available_at':['2023-11-29 08:00']*4,
                          'alpha':[1.]*4}).to_csv(p,index=False)
            c=config('StatsInterval');c['factors']=[{'name':'alpha','kind':'table','path':str(p)}]
            r=self.simulate(c);d=r['diagnostics']
            self.assertTrue(d.iloc[:-1].rank_ic.eq(0).all())
            self.assertTrue(d.iloc[:-1].ic_undefined.all())
            self.assertTrue(pd.isna(d.rank_ic.iloc[-1]))

    def test_ic_entry_delay_is_separate_from_execution_delay(self):
        c=config(idelayEach=1,nIntvDelay=1);r=self.simulate(c)
        market=SmallMarket(c);past=market.load_day('2023-12-01');today=market.load_day('2023-12-04')
        score=past.bar(2)['close_adj']/past.bar(1)['close_adj']-1
        future=today.bar(2)['vwap_adj']/today.bar(1)['vwap_adj']-1
        expected=pd.Series(score).rank().corr(pd.Series(future).rank())
        self.assertAlmostEqual(r['diagnostics'].rank_ic.iloc[0],expected)

    def test_close_price_mode_has_zero_reference_trade_contribution(self):
        r=self.simulate(config('StatsInterval',priceMode='close'))
        np.testing.assert_allclose(r['stats_intervals'].trade_return,0,atol=1e-12)

    def test_effective_signal_windows_skip_nonfire_slots(self):
        r=self.simulate(config('StatsInterval',nbar=2,signal_times=['0930','0940']))
        self.assertEqual(len(r['diagnostics']),3)
        self.assertTrue(r['stats_intervals'].actual_bars.eq(2).all())
        self.assertTrue(r['stats_sumnoend'].empty)

    def test_dump_contains_per_timing_and_research_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=config();c['output'].update(dump=True,directory=tmp,plots=False)
            r=self.simulate(c);path=r['output_directory']
            for file in ['slot_summary.csv','slot_daily.parquet','stats_slots.csv','stats_tvr.csv','COMPLETE']:
                self.assertTrue((path/file).exists())
            events=r['events']
            np.testing.assert_allclose(events.pnl,events.hold_pnl+events.trade_pnl-events.tradecost,atol=1e-7)


if __name__=='__main__':unittest.main()
