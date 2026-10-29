from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsim.alpha_compare import compare_alpha
from qsim.alpha_replay import AlphaReplay
from qsim.config import load_config
from qsim.factors import FactorModel
from qsim.input_policy import InputPolicy
from qsim.livetest import run_livetest
from qsim.livetest_config import parse_checks
from qsim.runner import run


def fixture(root, minute=False):
    """Small on-disk market exercising the real loader, not a mocked data API."""
    root = Path(root)
    cache, raw = root/'market', root/'raw'
    symbols = list('ABCDEF')
    dates = pd.bdate_range('2022-12-19', periods=22).strftime('%Y-%m-%d').tolist()
    (cache/'calendar').mkdir(parents=True)
    pd.DataFrame({'order_book_id': symbols, 'type': ['CS']*len(symbols)}).to_parquet(cache/'securities.parquet')
    calendars = {'1d': [], '5m': []}
    for d, date in enumerate(dates):
        stamp = date.replace('-', '')
        start = pd.Timestamp(date+' 09:30', tz='Asia/Shanghai')
        for freq, count in [('1d', 1), ('5m', 3)]:
            price_rows, vwap_rows = [], []
            for j in range(count):
                bar_id = d*count+j
                end = pd.Timestamp(date+' 15:00',tz='Asia/Shanghai') if freq=='1d' else start+pd.Timedelta(minutes=5*(j+1))
                calendars[freq].append(dict(date=date,bar_id=bar_id,bar_start=start+pd.Timedelta(minutes=5*j),bar_end=end))
                for i, symbol in enumerate(symbols):
                    price = 20+i*5+.13*(i+1)*d+.4*np.sin(d+i+j)
                    price_rows.append(dict(order_book_id=symbol,bar_id=bar_id,open_adj=price-.2,
                                           close_adj=price,bar_complete=1))
                    vwap_rows.append(dict(order_book_id=symbol,bar_id=bar_id,vwap_raw=price-.1,
                        vwap_adj=price-.1,volume=100000.,total_turnover=(price-.1)*100000,vwap_outside_ohlc=0))
            for category, records in [('prices',price_rows),('vwap',vwap_rows)]:
                target = cache/category/freq/date[:4]/f'{stamp}.parquet'
                target.parent.mkdir(parents=True,exist_ok=True)
                pd.DataFrame(records).to_parquet(target,index=False)
        target = cache/'universe/all_a'/date[:4]/f'{stamp}.parquet'
        target.parent.mkdir(parents=True,exist_ok=True)
        pd.DataFrame(dict(order_book_id=symbols,in_all_a=True,suspended=0.,st_status=0.)).to_parquet(target,index=False)
        target = raw/'daily_bar'/date[:4]/f'{stamp}.csv'
        target.parent.mkdir(parents=True,exist_ok=True)
        pd.DataFrame(dict(order_book_id=symbols,close=[20+i*5+.13*(i+1)*d+.4*np.sin(d+i) for i in range(6)],
                          limit_up=1000.,limit_down=1.)).to_csv(target,index=False)
    for freq, records in calendars.items():
        pd.DataFrame(records).to_parquet(cache/f'calendar/{freq}.parquet',index=False)
    return load_config({'data':{'root':str(cache),'raw_root':str(raw),'warmup_days':5},
        'backtest':{'start':dates[10],'end':dates[21],'frequency':'0930-0945-5' if minute else '0800','groups':2,'forecast_period':1},
        'operations':{'exclude_st':False,'min_cross_section':3},
        'factors':[{'name':'mom','kind':'momentum','lookback':3,'weight':1.}],
        'output':{'dump':False},'livetest':{'report_directory':str(root/'reports'),'samples':3}})


class FutureReader(FactorModel):
    def compute(self, when, eligible):
        return super().compute(when,eligible) + float(self.tables['external'].frame.x.iloc[-1])


class StartDependent(FactorModel):
    def compute(self, when, eligible):
        return super().compute(when,eligible) + pd.Timestamp(self.config['backtest']['start']).day


class MissingHistory(FactorModel):
    def state_dict(self):
        state = super().state_dict()
        state['history'] = []
        return state


class RandomAlpha(FactorModel):
    def compute(self, when, eligible):
        return super().compute(when,eligible) + random.random() + np.random.random()


class AllHistory(FactorModel):
    history_kind = 'all_history'


class AccountDependent(FactorModel):
    depends_on_account = True


class AllNaN(FactorModel):
    def compute(self, when, eligible):
        return np.full(len(self.symbols), np.nan)


class ComparisonTests(unittest.TestCase):
    def frame(self, values):
        return pd.DataFrame(dict(timestamp=pd.Timestamp('2023-01-03 08:00',tz='Asia/Shanghai'),
            instrument=list('ABC')[:len(values)],alpha_name='score',value=values))

    def test_keys_nan_infinity_and_tolerance(self):
        a = self.frame([1.,np.nan,0.])
        self.assertEqual(compare_alpha(a,a)['status'],'PASS')
        self.assertEqual(compare_alpha(a,a.iloc[:-1])['missing_records'],1)
        self.assertEqual(compare_alpha(a,pd.concat([a,a.iloc[:1]]))['status'],'FAIL')
        b = a.copy(); b.loc[0,'value'] += 1e-9
        self.assertEqual(compare_alpha(a,b,atol=0,rtol=1e-8)['status'],'PASS')
        self.assertEqual(compare_alpha(a,b,atol=0,rtol=0)['status'],'FAIL')
        for values, status in [([np.nan]*3,'INSUFFICIENT_DATA'),([np.inf]*3,'FAIL')]:
            self.assertEqual(compare_alpha(self.frame(values),self.frame(values))['status'],status)
        empty = a.iloc[:0]
        self.assertEqual(compare_alpha(empty,empty)['status'],'INSUFFICIENT_DATA')
        b = a.copy(); b.loc[2,'value']=1
        r = compare_alpha(a,b)
        self.assertEqual(r['max_relative_error'],'Infinity')
        self.assertEqual(r['first_difference']['instrument'],'C')

    def test_name_and_timestamp_are_keys_and_order_is_irrelevant(self):
        a = self.frame([1.,2.,3.])
        self.assertEqual(compare_alpha(a,a.iloc[::-1])['status'],'PASS')
        for col, value in [('alpha_name','other'),('timestamp',pd.Timestamp('2023-01-04',tz='Asia/Shanghai'))]:
            b=a.copy();b[col]=value
            self.assertEqual(compare_alpha(a,b)['missing_records'],6)


class LivetestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.config = fixture(cls.root/'daily')
        cls.minute = fixture(cls.root/'minute',minute=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def check(self, checks, config=None, model=FactorModel, process=False):
        return run_livetest(deepcopy(config or self.config),checks,model_class=model,checkpoint_process=process)

    def test_causal_alpha_passes_all_and_writes_strict_json(self):
        result = self.check('all')
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(result['exit_code'],0)
        self.assertEqual(len(result['selected_times']),3)
        self.assertEqual(len(result['selected_starts']),3)
        self.assertEqual(json.loads(Path(result['report_path']).read_text())['status'],'PASS')
        self.assertTrue(result['data_version'])
        for case in result['checks']['c']['cases']:
            self.assertGreater(case['worker']['first_event'],0)
            self.assertGreater(case['worker']['processed_events'],0)

    def table_config(self):
        config = deepcopy(self.config)
        path = self.root/'external.csv'
        rows=[]
        for date in pd.bdate_range('2022-12-19', periods=22):
            for i,symbol in enumerate('ABCDEF'):
                rows.append(dict(order_book_id=symbol,available_at=str(date.date())+' 08:00',x=i*i+date.day*.1))
        pd.DataFrame(rows).to_csv(path,index=False)
        config['factors']=[{'name':'external','kind':'table','path':str(path),'column':'x','weight':1.}]
        return config

    def test_future_table_access_is_detected_before_feature_calculation(self):
        config=self.table_config()
        self.assertEqual(self.check('f',config)['status'],'PASS')
        result=self.check('f',config,FutureReader)
        self.assertEqual(result['status'],'FAIL',result)
        self.assertTrue(all(c['status']=='FAIL' for c in result['checks']['f']['cases']))

    def test_market_input_boundary_and_reproducible_perturbation(self):
        from qsim.data import MarketData
        when=pd.Timestamp('2023-01-02 09:35',tz='Asia/Shanghai')
        full=MarketData(self.minute).load_day('2023-01-02')
        cut=MarketData(self.minute,InputPolicy('truncate',when,17)).load_day('2023-01-02')
        np.testing.assert_array_equal(full.bar(0)['close_adj'],cut.bar(0)['close_adj'])
        self.assertTrue(np.isnan(cut.bar(1)['close_adj']).all())
        changed=[]
        for _ in range(2):
            day=MarketData(self.minute,InputPolicy('perturb',when,17)).load_day('2023-01-02')
            np.testing.assert_array_equal(full.bar(0)['close_adj'],day.bar(0)['close_adj'])
            self.assertFalse(np.array_equal(full.bar(1)['close_adj'],day.bar(1)['close_adj']))
            changed.append(day.bar(1)['close_adj'])
        np.testing.assert_array_equal(*changed)

    def test_missing_replay_output_is_not_hidden_by_intersection(self):
        original=AlphaReplay.run
        calls=[0]
        def omit(instance,*args,**kwargs):
            frame=original(instance,*args,**kwargs)
            calls[0]+=1
            return frame if calls[0]==1 else frame.iloc[1:]
        config=deepcopy(self.config);config['livetest']['samples']=2
        with patch.object(AlphaReplay,'run',omit):
            result=self.check('f',config)
        self.assertEqual(result['status'],'FAIL')
        self.assertTrue(all(c['missing_records']==1 for c in result['checks']['f']['cases']))

    def test_start_dependence_and_all_history_are_not_passed(self):
        self.assertEqual(self.check('p',model=StartDependent)['status'],'FAIL')
        self.assertEqual(self.check('p',model=AllHistory)['status'],'UNSUPPORTED')

    def test_missing_checkpoint_history_is_detected(self):
        result=self.check('c',model=MissingHistory)
        self.assertEqual(result['status'],'FAIL',result)
        self.assertTrue(all(c['inconsistent_points']>0 for c in result['checks']['c']['cases']))

    def test_checkpoint_restores_rng_and_parent_rng_is_unchanged(self):
        random.seed(27); np.random.seed(29)
        py_state, np_state=random.getstate(),np.random.get_state()
        result=self.check('c',model=RandomAlpha)
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(random.getstate(),py_state)
        np.testing.assert_array_equal(np.random.get_state()[1],np_state[1])

    def test_checkpoint_restores_pit_cursor_and_state(self):
        self.assertEqual(self.check('c',self.table_config())['status'],'PASS')

    def test_real_subprocess_resume(self):
        import os
        config=deepcopy(self.config);config['livetest']['samples']=2
        result=self.check('c',config,process=True)
        self.assertEqual(result['status'],'PASS',result)
        for case in result['checks']['c']['cases']:
            self.assertNotEqual(case['worker']['pid'],os.getpid())
            self.assertEqual(case['isolation'],'subprocess')

    def test_minute_modes_cross_day_and_intraday_boundaries(self):
        for mode in ('StatsEvery','StatsInterval','Cohort'):
            config=deepcopy(self.minute);config['backtest']['mode']=mode
            config['livetest']['times']=['2023-01-02 09:30','2023-01-06 09:40']
            if mode=='StatsInterval':
                config['stats']['nbar']=2
            result=self.check('all',config)
            self.assertEqual(result['status'],'PASS',(mode,result))

    def test_insufficient_warmup_invalid_times_all_nan_and_account_dependency(self):
        config=deepcopy(self.config);config['data']['warmup_days']=1
        self.assertEqual(self.check('all',config)['status'],'INSUFFICIENT_DATA')
        config=deepcopy(self.config);config['data']['warmup_days']=100
        self.assertEqual(self.check('all',config)['status'],'INSUFFICIENT_DATA')
        self.assertEqual(self.check('all',model=AllNaN)['status'],'INSUFFICIENT_DATA')
        self.assertEqual(self.check('all',model=AccountDependent)['status'],'UNSUPPORTED')
        config=deepcopy(self.config);config['livetest']['times']=['2023-01-08 08:00']
        self.assertEqual(self.check('f,c',config)['status'],'INSUFFICIENT_DATA')

    def test_record_budget_and_exception_are_reported(self):
        config=deepcopy(self.config);config['livetest']['max_records']=1
        self.assertEqual(self.check('all',config)['status'],'UNSUPPORTED')
        config=deepcopy(self.config);config['data']['root']=str(self.root/'missing')
        result=self.check('all',config)
        self.assertEqual(result['status'],'ERROR')
        self.assertEqual(result['exit_code'],1)
        self.assertTrue(Path(result['report_path']).exists())

    def test_checkpoint_rejects_input_change(self):
        config=self.table_config()
        replay=AlphaReplay(config)
        replay.run(until=pd.Timestamp('2023-01-03 08:00',tz='Asia/Shanghai'))
        checkpoint=self.root/'changed_input.pkl'
        replay.save_checkpoint(checkpoint)
        path=Path(config['factors'][0]['path'])
        path.write_text(path.read_text()+'Z,2023-01-03 08:00,3\n')
        with self.assertRaisesRegex(ValueError,'input changed'):
            AlphaReplay(config).load_checkpoint(checkpoint)

    def test_default_backtest_compatibility_and_same_production_scores(self):
        config=deepcopy(self.config)
        config.pop('livetest')
        self.assertNotIn('livetest',load_config({}))
        captured=[]
        original=FactorModel.compute
        def capture(model,when,eligible):
            score=original(model,when,eligible)
            captured.append(score.copy())
            return score
        with patch.object(FactorModel,'compute',capture):
            first=run(config)
        replay=AlphaReplay(config).run()
        np.testing.assert_allclose(replay.value.to_numpy(),np.concatenate(captured),equal_nan=True)
        configured=deepcopy(config);configured['livetest']={'samples':2}
        second=run(configured)  # merely declaring options must not execute checks.
        for name in ('daily','summary','events','diagnostics'):
            pd.testing.assert_frame_equal(first[name],second[name])

    def test_cli_opt_in_exit_code_and_selection(self):
        from qsim.cli import main
        self.assertEqual(parse_checks('f,p'),['f','p'])
        with self.assertRaises(ValueError):parse_checks('f,x')
        config_path=self.root/'cli.toml'
        config_path.write_text('[backtest]\nstart="2023-01-03"\nend="2023-01-10"\n')
        with patch('sys.argv',['qsim','--config',str(config_path)]), patch('qsim.cli.run') as normal:
            normal.return_value={'summary':pd.DataFrame({'book':['LS'],'period':['ALL'],'from':['a'],'to':['b'],
                'return':[0],'tvr':[0],'sharp':[0],'dd':[0],'IC':[0],'rank_IC':[0],'nHold':[0]}),
                'audit':{},'output_directory':None}
            with patch('qsim.livetest.run_livetest') as live:
                main();live.assert_not_called();normal.assert_called_once()
        for code in (0,1):
            with patch('sys.argv',['qsim','--config',str(config_path),'--livetest','f,c']), \
                 patch('qsim.livetest.run_livetest',return_value={'exit_code':code}) as live, \
                 patch('qsim.cli.run') as normal:
                with self.assertRaises(SystemExit) as error:main()
                self.assertEqual(error.exception.code,code)
                self.assertEqual(live.call_args.args[1],['f','c'])
                normal.assert_not_called()


if __name__ == '__main__':
    unittest.main()
