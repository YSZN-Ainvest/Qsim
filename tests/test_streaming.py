from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsim.batch import run_batch
from qsim.config import load_config
from qsim.expressions import Expression
from qsim.factors import FactorModel
from qsim.livetest import run_livetest
from qsim.run_store import DiskTable
from qsim.runner import run
from test_stats import SmallMarket,config as small_config
from test_integration import TinyMarket
from test_livetest import fixture


def frame(value):
    return value.to_pandas() if isinstance(value,DiskTable) else value


def sorted_frame(value,keys):
    value=frame(value).copy()
    keys=[k for k in keys if k in value]
    return value.sort_values(keys,kind='stable').reset_index(drop=True)


class StreamingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix='qsim-stream-tests-'))

    def simulate(self,cfg,source=SmallMarket):
        target='qsim.runner.MarketData' if cfg['backtest']['mode']=='Cohort' else 'qsim.stats_runner.MarketData'
        with patch(target,source): return run(cfg)

    def config(self,mode='StatsEvery'):
        cfg=small_config(mode)
        cfg['output'].update(dump=True,directory=str(self.root/'results'),stream=True,plots=False)
        cfg['data']['cache_directory']=str(self.root/'cache')
        cfg['compute']['memory_per_job_mb']=2048
        return cfg

    def test_streamed_and_legacy_numerics_all_modes(self):
        for mode in ('StatsEvery','StatsInterval','Cohort'):
            with self.subTest(mode=mode):
                cfg=self.config(mode)
                if mode=='Cohort': cfg['backtest']['holding_period']=2
                legacy=deepcopy(cfg);legacy['output']['dump']=False
                old=self.simulate(legacy);new=self.simulate(cfg)
                for name,keys in [('daily',['book','date']),('summary',['book','period']),
                                  ('diagnostics',['timestamp']),('final_positions',['book','order_book_id'])]:
                    a=sorted_frame(old[name],keys);b=sorted_frame(new[name],keys)
                    b=b[a.columns]
                    pd.testing.assert_frame_equal(a,b,check_dtype=False,check_exact=False,rtol=1e-9,atol=1e-6)
                for name in ('stats_slots','stats_tvr','stats_intervals','stats_sum','stats_sumnoend','stats_ic_sum','stats_ic_sumnoend'):
                    if name in old:
                        a=sorted_frame(old[name],['date','slot','book']);b=sorted_frame(new[name],['date','slot','book'])
                        pd.testing.assert_frame_equal(a,b[a.columns],check_dtype=False,rtol=1e-9,atol=1e-10)
                self.assertIsInstance(new['events'],DiskTable)

    def test_factor_dump_schema_logs_json_and_single_pngs(self):
        cfg=self.config();cfg['output'].update(factors=True,positions=True,plots=True,factor_batch_rows=7,plot_points=20)
        from matplotlib.figure import Figure
        original_save=Figure.savefig;labels={}
        def save(fig,path,*args,**kwargs):
            if Path(path).name in ('group_returns.png','group_excess_returns.png'):
                labels[Path(path).name]=[tick.get_text() for tick in fig.axes[0].get_xticklabels()]
            return original_save(fig,path,*args,**kwargs)
        with patch.object(Figure,'savefig',save): result=self.simulate(cfg)
        root=result['output_directory']
        self.assertEqual(labels['group_returns.png'],['G01','G02'])
        self.assertEqual(labels['group_excess_returns.png'],['G01','G02'])
        factors=frame(result['factors'])
        self.assertEqual(set(factors.stage),{'raw','processed','combined'})
        self.assertFalse(factors.duplicated(['timestamp','instrument','alpha_name','stage']).any())
        self.assertEqual(len(factors),9*4*3)
        self.assertLessEqual(result['audit']['max_factor_buffer_rows'],7)
        self.assertTrue((root/'summary.json').is_file())
        payload=json.loads((root/'summary.json').read_text(),parse_constant=lambda x:(_ for _ in ()).throw(ValueError(x)))
        self.assertTrue(payload)
        logs=[json.loads(line) for line in (root/'run.jsonl').read_text().splitlines()]
        self.assertEqual(logs[0]['event'],'run_started')
        self.assertEqual(logs[-1]['event'],'run_complete')
        self.assertEqual(sum(x['event']=='day_complete' for x in logs),3)
        self.assertTrue(any('rejection_samples' in x for x in logs))
        for name in ('long_short_nav','long_short_pnl','group_nav','group_returns','group_excess_returns','ic','turnover'):
            self.assertTrue((root/(name+'.png')).stat().st_size>1000)
        self.assertFalse((root/'dashboard.png').exists())

    def test_failure_is_durable_and_not_complete(self):
        cfg=self.config();cfg['output']['directory']=str(self.root/'failure')
        class Broken(SmallMarket):
            def load_day(self,date):
                if date=='2023-12-05': raise RuntimeError('injected read failure')
                return super().load_day(date)
        with self.assertRaisesRegex(RuntimeError,'injected read failure'): self.simulate(cfg,Broken)
        root=next((self.root/'failure').iterdir())
        self.assertTrue((root/'INCOMPLETE').exists());self.assertFalse((root/'COMPLETE').exists())
        self.assertIn('injected read failure',(root/'failure.json').read_text())
        self.assertTrue((root/'daily.parquet').is_dir())

    def test_buffers_do_not_grow_with_days(self):
        observed=[]
        for days in (5,40):
            class LongMarket(SmallMarket):
                def __init__(self,config):
                    super().__init__(config)
                    self.run_dates=pd.bdate_range('2023-12-04',periods=days).strftime('%Y-%m-%d').tolist()
            cfg=self.config();cfg['backtest']['end']='2024-03-01'
            result=self.simulate(cfg,LongMarket)
            observed.append((result['audit']['max_buffered_events'],result['audit']['max_pending_labels']))
            self.assertEqual(result['audit']['days'],days)
        self.assertEqual(observed[0],observed[1])


class FactorPoolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.root=Path(tempfile.mkdtemp(prefix='qsim-factor-pool-tests-'))

    def test_hundred_columns_use_one_source_and_bounded_state(self):
        path=self.root/'wide.csv';symbols=list('ABCD')
        rows=[]
        for day in range(1,25):
            for i,symbol in enumerate(symbols):
                rows.append(dict(order_book_id=symbol,available_at=f'2023-01-{day:02} 08:00',
                    **{f'x{k}':float(i*k+day) for k in range(100)}))
        pd.DataFrame(rows).to_csv(path,index=False,mode='x')
        cfg=load_config({'data':{'cache_directory':str(self.root/'wide-cache'),'batch_rows':7},
            'factors':[dict(name=f'a{k}',kind='table',path=str(path),column=f'x{k}',weight=1.) for k in range(100)],
            'operations':{'min_cross_section':3}})
        original=pd.read_csv
        with patch('qsim.source_cache.pd.read_csv',wraps=original) as reads:
            model=FactorModel(cfg,symbols)
            self.assertEqual(reads.call_count,1)
            self.assertEqual(len(model.registry.sources),1)
            model.compute(pd.Timestamp('2023-01-10 08:00',tz='Asia/Shanghai'),np.ones(4,bool))
            source=next(iter(model.registry.sources.values()))
            self.assertEqual(source.state.shape,(4,100))
            before=source.queries
            model.compute(pd.Timestamp('2023-01-10 08:00',tz='Asia/Shanghai'),np.ones(4,bool))
            self.assertEqual(source.queries,before)
            self.assertEqual(reads.call_count,1)
            state=model.state_dict();self.assertEqual(len(state['tables']),1)
            model.close()
            other=FactorModel(cfg,symbols);other.load_state_dict(state);other.close()
            self.assertEqual(reads.call_count,1)

    def test_named_expression_dag_and_parallel_equality(self):
        cfg=load_config({'alphas':{'alpha1':{'kind':'momentum','lookback':2},
                                  'alpha2':{'kind':'volatility','lookback':2}},
            'combine':{'expression':'0.25*(<alpha1>+<alpha2>)'},
            'operations':{'min_cross_section':3}})
        values=[]
        for pools in (1,2):
            cfg['compute']['pools']=pools
            with patch('qsim.factors.allowed_workers',return_value=(pools,{})):
                model=FactorModel(cfg,list('ABCD'))
            for row in ([10,20,30,40],[11,19,32,41],[12,21,31,44]): model.observe(row)
            value=model.compute(pd.Timestamp('2023-01-03',tz='Asia/Shanghai'),np.ones(4,bool))
            expected=.25*(model.last_processed['alpha1']+model.last_processed['alpha2'])
            np.testing.assert_array_equal(value,expected);values.append(value)
            self.assertEqual(model.effective_pools,pools);model.close()
        np.testing.assert_array_equal(*values)
        for expression in ("__import__('os').system('true')",'alpha1[0]','alpha1.__class__'):
            with self.assertRaises(ValueError): Expression(expression)
        with self.assertRaisesRegex(ValueError,'Cyclic'):
            load_config({'alphas':{'a':{'kind':'expression','expression':'b'},'b':{'kind':'expression','expression':'a'}}})

    def test_parallel_pit_and_checkpoint(self):
        cfg=fixture(self.root/'live')
        cfg['compute']['pools']=2
        cfg['factors']=[{'name':'a','kind':'momentum','lookback':2},
                        {'name':'b','kind':'volatility','lookback':3}]
        cfg['combine']['expression']='0.5*(a+b)'
        with patch('qsim.factors.allowed_workers',return_value=(2,{})):
            report=run_livetest(cfg,'all',checkpoint_process=False)
        self.assertEqual(report['status'],'PASS',report)

    def test_constant_expression_overflow_is_bounded(self):
        value=Expression('10**100000000').evaluate({},4)
        self.assertTrue(np.isnan(value).all())

    def test_partitioned_pit_future_and_subprocess_checkpoint(self):
        cfg=fixture(self.root/'partition_live')
        path=self.root/'partitioned_alpha.parquet';path.mkdir()
        for i,(when,values) in enumerate([('2022-12-18 08:00',[1,2,3,4,5,6]),('2023-01-12 08:00',[2,5,3,8,7,1])]):
            pd.DataFrame(dict(order_book_id=list('ABCDEF'),available_at=when,x=values)).to_parquet(path/f'part-{i}.parquet',index=False)
        cfg['factors']=[dict(name='x',kind='table',path=str(path),column='x')]
        cfg['livetest']['samples']=2
        result=run_livetest(cfg,'f,c')
        self.assertEqual(result['status'],'PASS',result)

    def test_multiple_configs_use_real_processes_and_match_serial(self):
        cfg=fixture(self.root/'batch')
        cfg['output'].update(dump=True,plots=False,directory=str(self.root/'batch_results'))
        cfg['compute']['memory_per_job_mb']=1024
        configs=[deepcopy(cfg),deepcopy(cfg)]
        configs[0]['output']['name']='one';configs[1]['output']['name']='two'
        table=self.root/'batch_alphas.csv'
        pd.DataFrame(dict(order_book_id=list('ABCDEF'),available_at='2022-12-18 08:00',
                          x=[1,2,3,4,5,6],y=[6,5,4,3,2,1])).to_csv(table,index=False,mode='x')
        configs[0]['factors']=[dict(name='x',kind='table',path=str(table),column='x')]
        configs[1]['factors']=[dict(name='y',kind='table',path=str(table),column='y')]
        serial=[run(c)['summary'] for c in configs]
        budget=dict(cpu_budget=4,memory_budget=4*1024**3,cgroup_visible=True)
        original_read=pd.read_csv
        with patch('qsim.batch.allowed_workers',return_value=(2,budget)),patch('qsim.source_cache.pd.read_csv',wraps=original_read) as reads:
            result=run_batch(configs,2)
            self.assertEqual(reads.call_count,1)
        self.assertEqual(result['status'],'PASS',result)
        self.assertEqual(result['shared_sources'],1)
        self.assertEqual(len({j['pid'] for j in result['jobs']}),2)
        for original,job in zip(serial,result['jobs']):
            saved=pd.read_csv(Path(job['output_directory'])/'summary.csv')
            a=sorted_frame(original,['book','period']);b=sorted_frame(saved,['book','period'])
            pd.testing.assert_frame_equal(a,b,check_dtype=False,rtol=1e-10,atol=1e-8)
