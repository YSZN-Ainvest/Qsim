"""Reproducible small acceptance benchmarks. Outputs only to a new NAS folder."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np
import pandas as pd
import pyarrow as pa

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qsim.config import load_config
from qsim.data import DayCube,MarketData
from qsim.factors import FactorModel
from qsim.livetest import run_livetest
from qsim.run_store import resources,write_json
from qsim.stream_runner import run_stream


class SyntheticDaily:
    def __init__(self,cfg):
        self.freq='1d';self.symbols=[f'S{i:04}' for i in range(512)]
        self.run_dates=pd.bdate_range(cfg['backtest']['start'],cfg['backtest']['end']).strftime('%Y-%m-%d').tolist()
        self.warmup_dates=pd.bdate_range(end=pd.Timestamp(self.run_dates[0])-pd.Timedelta(days=1),periods=32).strftime('%Y-%m-%d').tolist()
        self.origin=pd.Timestamp(self.warmup_dates[0])

    def load_day(self,date):
        d=(pd.Timestamp(date)-self.origin).days;i=np.arange(len(self.symbols))
        close=20+i*.01+.01*d*(1+i%7)+.05*np.sin(d+i)
        values=dict(open_adj=close-.02,close_adj=close,vwap_raw=close-.01,vwap_adj=close-.01,
            volume=np.full(len(i),1e7),total_turnover=(close-.01)*1e7,
            bar_complete=np.ones(len(i)),vwap_outside_ohlc=np.zeros(len(i)))
        calendar=pd.DataFrame(dict(bar_start=[pd.Timestamp(date+' 09:30',tz='Asia/Shanghai')],bar_end=[pd.Timestamp(date+' 15:00',tz='Asia/Shanghai')]))
        return DayCube(date,calendar,self.symbols,{k:v[None,:,None] for k,v in values.items()},
            np.ones(len(i),bool),np.zeros(len(i)),np.zeros(len(i)),np.full(len(i),1e6),
            np.ones(len(i)),np.ones(len(i)),close)


def memory_case(root,days):
    end=pd.bdate_range('2024-01-02',periods=days)[-1].strftime('%Y-%m-%d')
    cfg=load_config({'data':{'root':str(root/'synthetic'),'warmup_days':32},
        'backtest':{'start':'2024-01-02','end':end,'groups':5},
        'factors':[dict(name=f'a{i}',kind='momentum' if i%2 else 'volatility',lookback=i+3) for i in range(16)],
        'operations':{'exclude_st':False},'compute':{'memory_per_job_mb':1024},
        'output':{'directory':str(root),'name':f'memory_{days}','plots':False,'csv':False,'factors':True,'positions':True}})
    before=resources();started=time.monotonic()
    result=run_stream(cfg,market_class=SyntheticDaily)
    record=dict(case='memory',days=days,stocks=512,factors=16,elapsed_seconds=time.monotonic()-started,
        resource_before=before,resource_after=resources(),audit=result['audit'],output_directory=str(result['output_directory']))
    write_json(root/f'memory_{days}.json',record)


def factor_case(root,pools):
    cfg=load_config({'compute':{'pools':pools},'operations':{'exclude_st':False},
        'factors':[dict(name=f'a{i}',kind='volatility' if i%2 else 'momentum',lookback=i+2) for i in range(100)]})
    n=4096;axis=np.arange(n);model=FactorModel(cfg,[f'S{i}' for i in range(n)])
    def close(t): return 20+axis*.001+.001*t*(axis%13+1)+.03*np.sin(t+axis)
    for t in range(102): model.observe(close(t))
    started=time.monotonic();values=[]
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=1):
        for t in range(20):
            values.append(model.compute(pd.Timestamp('2024-01-02',tz='Asia/Shanghai')+pd.Timedelta(minutes=t),np.ones(n,bool)))
            model.observe(close(t+102))
    elapsed=time.monotonic()-started
    record=dict(case='factor_pool',requested=pools,effective=model.effective_pools,stocks=n,factors=100,bars=20,
        elapsed_seconds=elapsed,score_sha256=hashlib.sha256(np.array(values).tobytes()).hexdigest(),resources=resources())
    model.close();write_json(root/f'factor_{pools}.json',record)


def batch_case(root):
    from qsim.batch import run_batch
    cfg=load_config(Path(__file__).resolve().parents[1]/'configs/streaming_demo.toml')
    cfg['output'].update(directory=str(root),plots=False,csv=False,factors=False,positions=False)
    cfg['compute'].update(pools=1,memory_per_job_mb=512)
    configs=[deepcopy(cfg),deepcopy(cfg)]
    configs[0]['output']['name']='batch_a';configs[1]['output']['name']='batch_b'
    configs[1]['combine']['expression']='0.25*alpha1 + 0.5*alpha2 + 0.25*alpha3'
    # Warm the same versioned immutable market cache before both comparisons.
    market=MarketData(cfg)
    for date in market.warmup_dates+market.run_dates: market.load_day(date)
    records=[]
    for pools in (1,2):
        started=time.monotonic();result=run_batch(configs,pools)
        records.append(dict(pools=pools,elapsed_seconds=time.monotonic()-started,**result))
    if any(r['status']!='PASS' for r in records): raise RuntimeError('Batch benchmark failed')
    for a,b in zip(records[0]['jobs'],records[1]['jobs']):
        left=pd.DataFrame(a['summary']).sort_values(['book','period']).reset_index(drop=True)
        right=pd.DataFrame(b['summary']).sort_values(['book','period']).reset_index(drop=True)
        pd.testing.assert_frame_equal(left,right,rtol=1e-12,atol=1e-8)
    write_json(root/'batch.json',dict(status='PASS',records=records,speedup=records[0]['elapsed_seconds']/records[1]['elapsed_seconds']))


def acceptance(root):
    from qsim.runner import run
    cfg=load_config(Path(__file__).resolve().parents[1]/'configs/streaming_demo.toml')
    cfg['output']['directory']=str(root)
    live=run_livetest(cfg,'all',report_path=root/'livetest.json')
    if live['status']!='PASS': raise RuntimeError('Parallel alpha livetest failed')
    result=run(cfg)
    reference_cfg=deepcopy(cfg);reference_cfg['output']['dump']=False;reference_cfg['compute']['pools']=1
    reference=run(reference_cfg)
    for name,keys in [('daily',['book','date']),('summary',['book','period']),('diagnostics',['timestamp'])]:
        a=reference[name].sort_values(keys).reset_index(drop=True)
        value=result[name];b=(value.to_pandas() if hasattr(value,'to_pandas') else value).sort_values(keys).reset_index(drop=True)
        pd.testing.assert_frame_equal(a,b[a.columns],check_dtype=False,rtol=1e-9,atol=1e-7)
    write_json(root/'acceptance.json',dict(status='PASS',livetest=str(root/'livetest.json'),
        output_directory=str(result['output_directory']),days=result['audit']['days'],
        note='2024 dates without OS opt-in; streamed pool expression agrees with serial in-memory reference'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',choices=['memory','factor','batch','acceptance'])
    parser.add_argument('--days',type=int,default=32);parser.add_argument('--pools',type=int,default=1)
    args=parser.parse_args();root=args.output
    if not root.resolve().is_relative_to('/mnt/nas/qinml_cache'): parser.error('Output must be on NAS')
    pa.set_cpu_count(1);pa.set_io_thread_count(1)
    if args.case:
        if args.case=='memory': memory_case(root,args.days)
        elif args.case=='factor': factor_case(root,args.pools)
        elif args.case=='batch': batch_case(root)
        else: acceptance(root)
        return
    root.mkdir(parents=True,exist_ok=False)
    commands=[['--case','memory','--days','32'],['--case','memory','--days','256'],
              ['--case','factor','--pools','1'],['--case','factor','--pools','2'],['--case','batch'],['--case','acceptance']]
    for index,extra in enumerate(commands):
        with (root/f'case_{index}.log').open('x') as log:
            subprocess.run([sys.executable,str(Path(__file__).resolve()),'--output',str(root)]+extra,
                           check=True,stdout=log,stderr=subprocess.STDOUT,timeout=900)
        print('Completed benchmark:',extra,flush=True)
    short=json.loads((root/'memory_32.json').read_text());long=json.loads((root/'memory_256.json').read_text())
    one=json.loads((root/'factor_1.json').read_text());two=json.loads((root/'factor_2.json').read_text())
    bounded=long['resource_after']['peak_rss_kib'] <= short['resource_after']['peak_rss_kib']*1.5+32768
    checks=dict(memory_bounded=bounded,factor_buffers_bounded=short['audit']['max_factor_buffer_rows']==long['audit']['max_factor_buffer_rows'],
        pending_labels_bounded=short['audit']['max_pending_labels']==long['audit']['max_pending_labels'],
        parallel_scores_identical=one['score_sha256']==two['score_sha256'],effective_factor_pools=two['effective']==2)
    result=dict(status='PASS' if all(checks.values()) else 'FAIL',checks=checks,
        memory=[{k:r[k] for k in ('days','elapsed_seconds','resource_after')} for r in (short,long)],
        factor_pool=[one,two],factor_speedup=one['elapsed_seconds']/two['elapsed_seconds'],
        batch=json.loads((root/'batch.json').read_text()),acceptance=json.loads((root/'acceptance.json').read_text()))
    write_json(root/'benchmark_summary.json',result)
    print(json.dumps(checks,indent=2),flush=True)
    raise SystemExit(0 if result['status']=='PASS' else 1)


if __name__=='__main__': main()
