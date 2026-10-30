"""Run-scoped logs, durable Parquet batches and explicit lazy result tables."""
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import resource
import time
import traceback
import uuid

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq


def json_clean(value):
    if isinstance(value,dict): return {str(k):json_clean(v) for k,v in value.items()}
    if isinstance(value,(list,tuple)): return [json_clean(v) for v in value]
    if isinstance(value,(np.integer,np.bool_)): return value.item()
    if isinstance(value,(float,np.floating)): return float(value) if np.isfinite(value) else None
    if isinstance(value,(pd.Timestamp,datetime,Path)): return str(value)
    return value


def write_json(path,value):
    with Path(path).open('x') as stream: json.dump(json_clean(value),stream,ensure_ascii=False,indent=2,allow_nan=False)


def resources():
    result={'pid':os.getpid(),'peak_rss_kib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    for filename,keys in [('status',{'VmRSS','Threads'}),('io',{'rchar','wchar','read_bytes','write_bytes'})]:
        try:
            for line in Path('/proc/self/'+filename).read_text().splitlines():
                key,value=line.split(':',1)
                if key in keys: result[key]=int(value.split()[0])
        except (OSError,ValueError): pass
    try:
        for line in Path('/proc/meminfo').read_text().splitlines():
            if line.startswith('MemAvailable:'): result['system_available_kib']=int(line.split()[1])
    except OSError: pass
    return result


class DiskTable:
    """Large tables stay on disk. pandas-style access explicitly materializes data."""
    def __init__(self,path,rows): self.path=Path(path);self.rows=rows
    def __len__(self): return self.rows
    @property
    def empty(self): return not self.rows
    def iter_batches(self,columns=None,batch_size=4096):
        yield from ds.dataset(self.path,format='parquet').scanner(columns=columns,batch_size=batch_size,use_threads=False).to_batches()
    def to_pandas(self,columns=None):
        return ds.dataset(self.path,format='parquet').to_table(columns=columns,use_threads=False).to_pandas()
    def __getitem__(self,key):
        return self.to_pandas([key])[key] if isinstance(key,str) else self.to_pandas()[key]
    def __getattr__(self,key):
        if key.startswith('_'): raise AttributeError(key)
        columns=ds.dataset(self.path,format='parquet').schema.names
        if key in columns: return self[key]
        return getattr(self.to_pandas(),key)


class RunStore:
    def __init__(self,config):
        self.config=config;self.started=time.monotonic();self.phase='initialize';self.current={}
        self.run_id=config['output']['name']+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'_'+uuid.uuid4().hex[:8]
        self.directory=Path(config['output']['directory'])/self.run_id
        self.directory.mkdir(parents=True,exist_ok=False)
        self.log_json=(self.directory/'run.jsonl').open('x',buffering=1)
        self.log_text=(self.directory/'run.log').open('x',buffering=1)
        self.counts=defaultdict(int);self.parts=defaultdict(int);self.empty={};self.bytes=0;self.timings=defaultdict(float)
        self.max_buffered_events=0;self.max_pending_labels=0;self.max_factor_buffer_rows=0
        self.max_table_buffer_rows=0
        self.csv_streams={}
        self.factor_chunks=[];self.factor_rows=0
        self.last_resource_check=0.
        self.validations=dict(no_signal_bars=0,min_signal_count=None,valid_ic_days=set(),traded_days=set(),max_accounting_error=0.)
        write_json(self.directory/'config.json',config)
        self.log('INFO','run_started',resources=resources())

    def log(self,level,event,**fields):
        record=dict(timestamp=datetime.now().astimezone().isoformat(),run_id=self.run_id,level=level,event=event,phase=self.phase)
        record.update(self.current);record.update(fields)
        self.log_json.write(json.dumps(json_clean(record),ensure_ascii=False,allow_nan=False)+'\n')
        self.log_text.write(f"{record['timestamp']} {level} {event} "+json.dumps(json_clean({**self.current,**fields}),ensure_ascii=False)+'\n')
        if level=='ERROR': self.log_json.flush();self.log_text.flush()

    @contextmanager
    def stage(self,name):
        previous=self.phase;self.phase=name;started=time.monotonic()
        try:
            self.check_resources()
            yield
        except Exception:
            self.failed_phase=name
            self.log('ERROR','stage_failed',exception=traceback.format_exc());raise
        finally:
            self.timings[name]+=time.monotonic()-started;self.phase=previous

    def check_resources(self,force=False):
        if not force and time.monotonic()-self.last_resource_check<1: return
        self.last_resource_check=time.monotonic();sample=resources()
        rss=sample.get('VmRSS',sample['peak_rss_kib'])*1024
        limit=self.config['compute']['memory_per_job_mb']*1024**2
        available=sample.get('system_available_kib',0)*1024
        if rss>limit or (available and rss>available*.5):
            self.log('ERROR','resource_budget_exceeded',resources=sample,memory_limit_bytes=limit)
            raise RuntimeError('Memory budget/remaining system memory exceeded; completed partitions retained')

    def add(self,name,frame):
        if frame.empty:
            if name not in self.counts: self.empty[name]=frame
            return
        self.empty.pop(name,None)
        frame=frame.copy()
        for col in ('label_end','label_status','slot','date','book','alpha_name','stage','order_book_id','instrument'):
            if col in frame: frame[col]=frame[col].astype('string')
        target=self.directory/(name+'.parquet')
        if self.parts[name]==0: target.mkdir(exist_ok=True)
        path=target/f'part-{self.parts[name]:08}.parquet'
        with self.stage('write'):
            temporary=path.with_name('.'+path.name+'.tmp')
            pq.write_table(pa.Table.from_pandas(frame,preserve_index=False),temporary,compression='zstd')
            temporary.rename(path)
            if self.config['output']['csv'] and name not in ('events','positions','factors','final_positions'):
                if name not in self.csv_streams:
                    self.csv_streams[name]=(self.directory/(name+'.csv')).open('x',newline='')
                stream=self.csv_streams[name]
                frame.to_csv(stream,index=False,header=not self.parts[name])
                stream.flush()
        self.parts[name]+=1;self.counts[name]+=len(frame);self.bytes+=path.stat().st_size

    def add_chunks(self,name,frames,max_rows=65536):
        """Combine a finite/day-scoped iterable; never retain more than max_rows."""
        if max_rows<1: raise ValueError('max_rows must be positive')
        chunks=[];rows=0
        for frame in frames:
            if frame.empty:
                self.add(name,frame)
                continue
            offset=0
            while offset<len(frame):
                end=min(len(frame),offset+max_rows-rows)
                chunks.append(frame.iloc[offset:end].copy());rows+=end-offset;offset=end
                self.max_table_buffer_rows=max(self.max_table_buffer_rows,rows)
                if rows==max_rows:
                    self.add(name,pd.concat(chunks,ignore_index=True));chunks.clear();rows=0
        if chunks: self.add(name,pd.concat(chunks,ignore_index=True))

    def factors(self,when,symbols,model):
        if not self.config['output']['factors']: return
        n=len(symbols);batch=self.config['output']['factor_batch_rows']
        fields=[(name,'raw',value) for name,value in model.last_raw.items()]
        fields += [(name,'processed',value) for name,value in model.last_processed.items()]
        fields += [('combined_score','combined',model.last_score)]
        for name,stage,values in fields:
            offset=0
            while offset<n:
                end=min(n,offset+batch-self.factor_rows)
                frame=pd.DataFrame(dict(timestamp=when,instrument=symbols[offset:end],alpha_name=name,stage=stage,value=values[offset:end]))
                self.factor_chunks.append(frame);self.factor_rows+=len(frame)
                self.max_factor_buffer_rows=max(self.max_factor_buffer_rows,self.factor_rows)
                if self.factor_rows>=batch: self.flush_factors()
                offset=end

    def flush_factors(self):
        if self.factor_chunks:
            self.add('factors',pd.concat(self.factor_chunks,ignore_index=True))
            self.factor_chunks.clear();self.factor_rows=0

    def progress(self,date,completed,total,**fields):
        self.check_resources(force=True)
        self.log('INFO','day_complete',date=date,days=completed,total_days=total,elapsed_seconds=time.monotonic()-self.started,
                 resources=resources(),rows=dict(self.counts),parquet_bytes=self.bytes,phase_seconds=dict(self.timings),**fields)
        print(f'{date}: {completed}/{total} days; output={self.run_id}',flush=True)

    def tables(self):
        self.flush_factors()
        for name,frame in list(self.empty.items()):
            target=self.directory/(name+'.parquet');target.mkdir(exist_ok=True)
            frame.to_parquet(target/'part-00000000.parquet',index=False)
            if self.config['output']['csv']: frame.to_csv(self.directory/(name+'.csv'),index=False)
            self.counts[name]=0
        self.empty.clear()
        return {name:DiskTable(self.directory/(name+'.parquet'),count) for name,count in self.counts.items()}

    def finish(self,result):
        self.close_csv()
        audit=result['audit']
        audit.update(run_id=self.run_id,phase_seconds=dict(self.timings),parquet_bytes=self.bytes,
            rows=dict(self.counts),resources=resources(),max_buffered_events=self.max_buffered_events,
            max_pending_labels=self.max_pending_labels,max_factor_buffer_rows=self.max_factor_buffer_rows,
            max_table_buffer_rows=self.max_table_buffer_rows,parquet_parts=dict(self.parts))
        write_json(self.directory/'summary.json',result['summary'].to_dict('records'))
        write_json(self.directory/'audit.json',audit)
        write_json(self.directory/'manifest.json',dict(status='complete',layout='partitioned_parquet',tables={k:dict(path=k+'.parquet',rows=v,parts=self.parts[k] or 1) for k,v in self.counts.items()}))
        (self.directory/'COMPLETE').write_text('All run outputs completed.\n')
        self.log('INFO','run_complete',elapsed_seconds=time.monotonic()-self.started,resources=resources())
        self.close()

    def fail(self):
        self.phase=getattr(self,'failed_phase',self.phase)
        self.log('ERROR','run_failed',exception=traceback.format_exc(),resources=resources(),rows=dict(self.counts))
        (self.directory/'INCOMPLETE').write_text('Run failed; completed Parquet parts and logs retained.\n')
        write_json(self.directory/'failure.json',dict(phase=self.phase,context=self.current,exception=traceback.format_exc(),rows=dict(self.counts)))
        self.close()

    def close_csv(self):
        error=None
        for stream in self.csv_streams.values():
            try: stream.close()
            except OSError as exc: error=exc
        self.csv_streams.clear()
        if error is not None: raise error

    def close(self):
        try: self.close_csv()
        finally:
            self.log_json.close();self.log_text.close()
