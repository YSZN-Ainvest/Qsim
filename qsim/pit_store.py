"""Bounded PIT input: chunked projection -> indexed shared SQLite -> as-of state."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import uuid

import numpy as np
import pandas as pd

from .source_cache import batches,build_lock,cache_path,source_version


def knowledge_times(values):
    times=pd.to_datetime(values,format='mixed')
    if times.dt.tz is None: times=times.dt.tz_localize('Asia/Shanghai')
    else: times=times.dt.tz_convert('Asia/Shanghai')
    if times.isna().any(): raise ValueError('Invalid available_at')
    return times.dt.as_unit('ns')


def prepare_store(path,columns,root,batch_rows=4096):
    columns=sorted(set(columns))
    target,version=cache_path(path,root,'pit_v2',columns)
    target=target.with_suffix('.sqlite')
    with build_lock(target):
        if not target.exists():
            temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
            connection=sqlite3.connect(temporary)
            try:
                connection.execute('PRAGMA temp_store=FILE')
                connection.execute('PRAGMA cache_size=-8192')
                connection.execute('CREATE TABLE inputs (instrument TEXT NOT NULL, available_ns INTEGER NOT NULL,'+
                                   ','.join(f'c{i}' for i in range(len(columns)))+')')
                marks=','.join('?' for _ in range(len(columns)+2))
                count=0
                for batch in batches(path,['order_book_id','available_at']+columns,batch_rows):
                    frame=batch.to_pandas()
                    times=knowledge_times(frame.available_at)
                    frame=frame[['order_book_id']+columns].copy()
                    frame.insert(1,'available_ns',times.astype('int64'))
                    rows=[]
                    for row in frame.itertuples(index=False,name=None):
                        rows.append(tuple(None if pd.isna(v) else v.item() if isinstance(v,np.generic) else v for v in row))
                    connection.executemany(f'INSERT INTO inputs VALUES ({marks})',rows)
                    count+=len(rows)
                connection.execute('CREATE INDEX knowledge ON inputs(available_ns,instrument)')
                connection.execute('CREATE TABLE metadata (payload TEXT)')
                connection.execute('INSERT INTO metadata VALUES (?)',(json.dumps(dict(source=version,columns=columns,rows=count)),))
                connection.commit()
                if source_version(path)!=version: raise ValueError(f'PIT input changed during build: {path}')
            finally:
                connection.close()
            temporary.rename(target)
    return target


class StreamingPITTable:
    def __init__(self,path,columns,symbols,max_date,input_policy=None,*,cache_directory,batch_rows=4096,store_columns=None):
        self.path=Path(path).resolve();self.columns=list(dict.fromkeys(columns));self.symbols=list(symbols)
        self.policy=input_policy;self.batch_rows=batch_rows
        self.version=source_version(path)
        store_columns=sorted(set(store_columns or columns))
        self.indices=[store_columns.index(c) for c in self.columns]
        self.database=prepare_store(path,store_columns,cache_directory,batch_rows)
        self.connection=sqlite3.connect(f'{self.database.as_uri()}?mode=ro',uri=True)
        self.connection.execute('PRAGMA cache_size=-4096')
        self.connection.execute('CREATE TEMP TABLE selected (instrument TEXT PRIMARY KEY)')
        self.connection.executemany('INSERT INTO selected VALUES (?)',[(s,) for s in symbols])
        self.end=pd.Timestamp(max_date,tz='Asia/Shanghai')+pd.Timedelta(days=1)
        self.state=pd.DataFrame(index=symbols,columns=self.columns,dtype=object)
        self.cursor=0;self.last=None;self.rows_read=0;self.queries=0
        if self.policy is not None: self.policy.record(path)

    def _chunks(self,lower,upper):
        if self.policy is not None and self.policy.mode=='truncate': upper=min(upper,self.policy.cutoff.value)
        upper=min(upper,self.end.value-1)
        fields=','.join('i.c'+str(i) for i in self.indices)
        query=f'SELECT i.instrument,i.available_ns,{fields} FROM inputs i JOIN selected s ON i.instrument=s.instrument WHERE i.available_ns>? AND i.available_ns<=? ORDER BY i.available_ns,i.instrument'
        self.queries+=1
        cursor=self.connection.execute(query,(lower,upper))
        previous=None
        while True:
            rows=cursor.fetchmany(self.batch_rows)
            if not rows: break
            frame=pd.DataFrame(rows,columns=['order_book_id','available_ns']+self.columns)
            keys=list(zip(frame.available_ns,frame.order_book_id))
            if previous==keys[0] or len(set(keys))!=len(keys):
                raise ValueError(f'{self.path}: duplicate knowledge-time/security keys')
            previous=keys[-1]
            frame['available_at']=pd.to_datetime(frame.pop('available_ns'),utc=True).dt.tz_convert('Asia/Shanghai')
            self.rows_read+=len(frame)
            if self.policy is not None:
                frame=self.policy.apply(frame,frame.available_at,str(self.path),self.columns)
            yield frame

    def at(self,when):
        when=pd.Timestamp(when)
        if self.last is not None and when<self.last: raise ValueError('PITTable requires monotonic decision timestamps')
        if self.last is None or when>self.last:
            lower=self.last.value if self.last is not None else np.iinfo(np.int64).min
            for frame in self._chunks(lower,when.value):
                update=frame.drop_duplicates('order_book_id',keep='last')
                self.state.loc[update.order_book_id,self.columns]=update[self.columns].to_numpy()
                self.cursor+=len(frame)
            self.last=when
        return self.state.copy()

    @property
    def frame(self):
        """Explicit diagnostic materialization; normal alpha calculations never use it."""
        chunks=list(self._chunks(np.iinfo(np.int64).min,self.end.value-1))
        return pd.concat(chunks,ignore_index=True) if chunks else pd.DataFrame(columns=['order_book_id','available_at']+self.columns)

    def state_dict(self):
        return deepcopy(dict(cursor=self.cursor,last=self.last,state=self.state,columns=self.columns,symbols=self.symbols,version=self.version))

    def load_state_dict(self,state):
        if state['columns']!=self.columns or state['symbols']!=self.symbols or state['version']!=self.version:
            raise ValueError('PIT checkpoint schema/input version mismatch')
        self.cursor,self.last=state['cursor'],state['last']
        self.state=state['state'].copy(deep=True)

    def close(self):
        if getattr(self,'connection',None) is not None:
            self.connection.close();self.connection=None

    def __del__(self):
        self.close()


def source_columns(config):
    result={}
    def add(path,columns):
        if path:
            key=str(Path(path).resolve())
            result.setdefault(key,[])
            result[key]=list(dict.fromkeys(result[key]+columns))
    for factor in config['factors']:
        if factor['kind']=='table': add(factor['path'],[factor.get('column',factor['name'])])
    o=config['operations'];u=config['universe']
    add(o['exposures_path'],o['neutralize_styles']+o['neutralize_industries'])
    add(o['st_path'],['is_st'])
    if u['kind']=='table': add(u['path'],['in_universe'])
    return {p:c for p,c in result.items() if c}


class PITRegistry:
    def __init__(self,config,symbols,policy=None):
        self.sources={p:StreamingPITTable(p,cols,symbols,config['backtest']['end'],policy,
            cache_directory=config['data']['cache_directory'],batch_rows=config['data']['batch_rows'],
            store_columns=config['data']['pit_columns'].get(p,cols))
            for p,cols in source_columns(config).items()}

    def get(self,path): return self.sources[str(Path(path).resolve())] if path else None
    def close(self):
        for source in self.sources.values(): source.close()
