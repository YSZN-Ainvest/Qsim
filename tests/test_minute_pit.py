"""Minute decision-time contracts, including lunch and weekend boundaries."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsim.config import load_config, schedule
from qsim.data import DayCube
from qsim.factors import FactorModel
from qsim.runner import run


class SessionMarket:
    """Deterministic complete sessions; Friday warmup, Monday/Tuesday evaluation."""
    def __init__(self, config):
        self.config = config
        self.symbols = list('ABCD')
        self.freq, self.start, self.end = schedule(config['backtest']['frequency'])
        self.size = int(self.freq[:-1])
        self.warmup_dates = ['2023-01-06']
        self.run_dates = ['2023-01-09', '2023-01-10']
        self.minutes = list(range(570,690,self.size)) + list(range(780,900,self.size))

    def load_day(self, date):
        d = (self.warmup_dates+self.run_dates).index(date)
        base = pd.Timestamp(date, tz='Asia/Shanghai')
        calendar, bars = [], []
        for j, minute in enumerate(self.minutes):
            index = d*len(self.minutes)+j
            close = 20 + np.arange(4)*10 + .02*(index+1)*(np.arange(4)+1) + .1*np.sin(index+np.arange(4))
            if self.start <= minute and minute+self.size <= self.end:
                calendar.append(dict(bar_start=base+pd.Timedelta(minutes=minute),
                                     bar_end=base+pd.Timedelta(minutes=minute+self.size)))
                bars.append(dict(open_adj=close-.02,close_adj=close,vwap_adj=close-.01,vwap_raw=close-.01,
                    volume=np.full(4,1e8),total_turnover=(close-.01)*1e8,
                    bar_complete=np.ones(4),vwap_outside_ohlc=np.zeros(4)))
        tensors = {k:np.array([bar[k] for bar in bars]).T[None,:,:] for k in bars[0]}
        return DayCube(date,pd.DataFrame(calendar),self.symbols,tensors,np.ones(4,bool),
            np.zeros(4),np.zeros(4),np.full(4,10000.),np.ones(4),np.ones(4),close)


def config(size=5, mode='StatsEvery', end='1500'):
    return load_config({'data':{'warmup_days':1},'backtest':{'start':'2023-01-09','end':'2023-01-10',
        'frequency':f'0930-{end}-{size}','mode':mode,'groups':2,'forecast_period':1,'capital':10000.},
        'operations':{'exclude_st':False,'winsor_mad':0.,'standardize':False,'min_cross_section':3},
        'factors':[{'name':'momentum','kind':'momentum','lookback':1,'weight':1.}],
        'output':{'dump':False}})


class MinutePITTests(unittest.TestCase):
    def simulate(self, cfg):
        traces = []
        original = FactorModel.compute
        def capture(model, when, eligible):
            score = original(model,when,eligible)
            traces.append(dict(when=when,history=np.array(model.history),score=score.copy()))
            return score
        with patch('qsim.stats_runner.MarketData',SessionMarket), patch.object(FactorModel,'compute',capture):
            result = run(cfg)
        return traces, result

    def test_completed_bars_only_at_every_decision_for_all_minute_frequencies(self):
        for size in (1,5,30):
            for mode in ('StatsEvery','StatsInterval'):
                with self.subTest(size=size,mode=mode):
                    cfg=config(size,mode)
                    if mode=='StatsEvery':
                        last=900-size
                        cfg['stats']['signal_times']=['0930','1000','1300',f'{last//60:02}{last%60:02}']
                    else:
                        cfg['stats']['nbar']=2
                    market=SessionMarket(cfg)
                    timeline=[]
                    for date in market.warmup_dates+market.run_dates:
                        day=market.load_day(date)
                        timeline.extend((row.bar_end,day.bar(j)['close_adj']) for j,row in day.calendar.iterrows())
                    traces,_=self.simulate(cfg)
                    self.assertTrue(traces)
                    for trace in traces:
                        known=[(end,close) for end,close in timeline if end<=trace['when']]
                        self.assertGreaterEqual(len(known),2)
                        expected=np.array([row[1] for row in known[-2:]])
                        np.testing.assert_allclose(trace['history'],expected,rtol=0,atol=0)
                        np.testing.assert_allclose(trace['score'],expected[-1]/expected[0]-1,rtol=1e-13,atol=1e-13)
                        if trace['when'].strftime('%H%M')=='1300':
                            self.assertEqual(known[-1][0].strftime('%H%M'),'1130')
                        if str(trace['when'])=='2023-01-09 09:30:00+08:00':
                            self.assertEqual(str(known[-1][0]),'2023-01-06 15:00:00+08:00')

    def test_every_delays_skip_lunch_and_do_not_roll_orders_to_next_day(self):
        for size in (1,5,30):
            with self.subTest(size=size):
                cfg=config(size)
                am,pm=690-size,900-size
                clock=lambda minute:f'{minute//60:02}{minute%60:02}'
                cfg['stats'].update(nIntvDelay=2,signal_times=[clock(am),clock(pm)])
                traces,result=self.simulate(cfg)
                self.assertEqual(len(traces),2)
                self.assertEqual(result['audit']['skipped_delay_signals'],2)
                rows=result['stats_slots'].loc[result['stats_slots'].book.eq('LS')]
                for _,row in rows.iterrows():
                    self.assertEqual(row.signal_time,clock(am))
                    self.assertEqual(pd.Timestamp(row.execution_end).strftime('%H%M'),clock(780+size))
                    self.assertEqual(str(pd.Timestamp(row.execution_end).date()),row.date)

    def test_interval_signal_precedes_whole_execution_window(self):
        cfg=config(5,'StatsInterval');cfg['stats']['nbar']=2
        traces,result=self.simulate(cfg)
        rows=result['stats_intervals'].loc[result['stats_intervals'].book.eq('LS')]
        self.assertEqual(len(rows),len(traces))
        for (_,row),trace in zip(rows.iterrows(),traces):
            self.assertEqual(pd.Timestamp(row.execution_end)-trace['when'],pd.Timedelta(minutes=10))
            self.assertEqual(row.signal_time,trace['when'].strftime('%H%M'))
            self.assertEqual(row.actual_bars,2)

    def test_external_factor_exact_and_delayed_availability(self):
        # Created by this test under configured TMPDIR; retain tiny evidence files.
        root=Path(tempfile.mkdtemp(prefix='qsim-minute-pit-'))
        rows=[]
        updates=[('2023-01-06 15:01:00',0.),('2023-01-09 09:35:00',10.),
                 ('2023-01-09 09:35:00.000001',20.),('2023-01-09 13:02:00',30.)]
        for when,offset in updates:
            for i,symbol in enumerate('ABCD'):
                rows.append(dict(order_book_id=symbol,available_at=when,x=offset+i+1))
        # Uniform microsecond formatting exercises timestamp boundaries without mixed-format parsing.
        frame=pd.DataFrame(rows)
        frame['available_at']=[pd.Timestamp(x).strftime('%Y-%m-%d %H:%M:%S.%f') for x in frame.available_at]
        path=root/'factor.csv';frame.to_csv(path,index=False,mode='x')
        cfg=config();cfg['factors']=[{'name':'external','kind':'table','path':str(path),'column':'x'}]
        cfg['stats']['signal_times']=['0930','0935','0940','1300','1305']
        traces,_=self.simulate(cfg)
        first_day={t['when'].strftime('%H%M'):t['score'] for t in traces if str(t['when'].date())=='2023-01-09'}
        for clock,offset in [('0930',0.),('0935',10.),('0940',20.),('1300',20.),('1305',30.)]:
            np.testing.assert_array_equal(first_day[clock],np.arange(1,5)+offset)

    def test_selected_window_defines_overnight_history(self):
        cfg=config(end='1450');cfg['stats']['signal_times']=['0930']
        traces,_=self.simulate(cfg)
        previous=SessionMarket(cfg).load_day('2023-01-06')
        self.assertEqual(previous.calendar.iloc[-1].bar_end.strftime('%H%M'),'1450')
        np.testing.assert_array_equal(traces[0]['history'][-1],previous.bar(len(previous.calendar)-1)['close_adj'])
        self.assertFalse(np.array_equal(traces[0]['history'][-1],previous.eod_close))


if __name__=='__main__':
    unittest.main()
