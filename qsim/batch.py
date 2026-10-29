"""Bounded independent backtest jobs with shared projected input indexes."""
from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
from copy import deepcopy
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import uuid

from .config import load_config
from .pit_store import source_columns,prepare_store
from .resources import allowed_workers
from .run_store import write_json


def _job(config):
    import pyarrow as pa
    pa.set_cpu_count(1);pa.set_io_thread_count(1)
    from .runner import run
    try:
        result=run(config)
        return dict(status='PASS',name=config['output']['name'],output_directory=str(result['output_directory'] or ''),
                    summary=result['summary'].to_dict('records'),pid=os.getpid())
    except Exception:
        import traceback
        return dict(status='ERROR',name=config['output']['name'],error=traceback.format_exc(),pid=os.getpid())


def run_batch(values,pools=None):
    configs=[load_config(value) for value in values]
    if not configs: raise ValueError('Batch requires at least one configuration')
    root=Path(configs[0]['output']['directory'])/('batch_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:8])
    root.mkdir(parents=True,exist_ok=False)
    write_json(root/'requested_configs.json',configs)
    try:
        return _run_batch(configs,pools,root)
    except Exception:
        import traceback
        result=dict(status='ERROR',error=traceback.format_exc(),output_directory=str(root),exit_code=1)
        write_json(root/'batch_failure.json',result)
        (root/'INCOMPLETE').write_text('Batch failed; see batch_failure.json and completed run folders.\n')
        return result


def _run_batch(configs,pools,root):
    if any(not c['output']['dump'] or not c['output']['stream'] for c in configs):
        raise ValueError('Multi-config jobs require output.dump=true and output.stream=true')
    requested=pools or max(c['compute']['batch_pools'] for c in configs)
    memory=max(c['compute']['memory_per_job_mb'] for c in configs)
    workers,budget=allowed_workers(min(requested,len(configs)),memory)
    unions={}
    for cfg in configs:
        for path,cols in source_columns(cfg).items():
            key=(cfg['data']['cache_directory'],path)
            unions[key]=sorted(set(unions.get(key,[])+cols))
    # Build each projected physical source once, before workers are started.
    for (cache,path),columns in unions.items(): prepare_store(path,columns,cache)
    for cfg in configs:
        cfg['data']['pit_columns']={path:cols for (cache,path),cols in unions.items() if cache==cfg['data']['cache_directory']}
        cfg['output']['directory']=str(root)
        cfg['compute']['pools']=min(cfg['compute']['pools'],max(1,int(budget['cpu_budget'])//workers))
    manifest=dict(requested_pools=requested,effective_pools=workers,resource_budget=budget,shared_sources=len(unions),jobs=[])
    write_json(root/'batch_config.json',dict(manifest,configs=configs))
    if workers==1:
        manifest['jobs']=[dict(index=i,**_job(cfg)) for i,cfg in enumerate(configs)]
    else:
        for key in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','ARROW_IO_THREADS'): os.environ[key]='1'
        with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as executor:
            next_index=0;pending={}
            while pending or next_index<len(configs):
                while next_index<len(configs) and len(pending)<workers:
                    try: current,_=allowed_workers(workers,memory)
                    except RuntimeError:
                        if pending: break
                        raise
                    if len(pending)>=current: break
                    pending[executor.submit(_job,configs[next_index])]=next_index
                    next_index+=1
                done,_=wait(pending,return_when=FIRST_COMPLETED)
                for future in done:
                    index=pending.pop(future)
                    try: result=future.result()
                    except Exception as error: result=dict(status='ERROR',error=str(error))
                    manifest['jobs'].append(dict(index=index,**result))
    manifest['jobs'].sort(key=lambda r:r['index'])
    manifest['status']='PASS' if all(j['status']=='PASS' for j in manifest['jobs']) else 'ERROR'
    write_json(root/'batch_summary.json',manifest)
    (root/('COMPLETE' if manifest['status']=='PASS' else 'INCOMPLETE')).write_text(manifest['status']+'\n')
    print(f"Batch {manifest['status']}: {root}; effective pools={workers}",flush=True)
    return dict(manifest,output_directory=root,exit_code=0 if manifest['status']=='PASS' else 1)
