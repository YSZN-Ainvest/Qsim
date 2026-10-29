"""Versioned, process-shared read caches. Builders stream; readers mmap Arrow."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import uuid

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.ipc as ipc
import pyarrow.parquet as pq
import pyarrow.dataset as ds


def source_version(path):
    path=Path(path).resolve();stat=path.stat()
    if path.is_dir():
        digest=hashlib.sha256();size=0;count=0
        for file in sorted(path.rglob('*.parquet')):
            info=file.stat();size+=info.st_size;count+=1
            digest.update(json.dumps([str(file.relative_to(path)),info.st_size,info.st_mtime_ns]).encode())
        return dict(path=str(path),size=size,files=count,partition_version=digest.hexdigest())
    return dict(path=str(path),size=stat.st_size,mtime_ns=stat.st_mtime_ns)


def cache_path(path,root,kind,columns=None):
    version=source_version(path)
    key=hashlib.sha256(json.dumps([version,sorted(columns) if columns else None,1],sort_keys=True).encode()).hexdigest()
    directory=Path(root)/kind
    directory.mkdir(parents=True,exist_ok=True)
    return directory/key,version


@contextmanager
def build_lock(path):
    with Path(str(path)+'.lock').open('a') as stream:
        fcntl.flock(stream.fileno(),fcntl.LOCK_EX)
        yield


def batches(path,columns=None,batch_rows=4096):
    path=Path(path)
    if path.is_dir():
        yield from ds.dataset(path,format='parquet').scanner(columns=columns,batch_size=batch_rows,use_threads=False).to_batches()
    elif path.suffix=='.parquet':
        yield from pq.ParquetFile(path).iter_batches(batch_size=batch_rows,columns=columns,use_threads=False)
    else:
        for frame in pd.read_csv(path,usecols=columns,chunksize=batch_rows):
            yield pa.RecordBatch.from_pandas(frame,preserve_index=False)


def shared_frame(path,root,columns=None,symbols=None,batch_rows=4096):
    target,version=cache_path(path,root,'arrow',columns)
    target=target.with_suffix('.arrow')
    with build_lock(target):
        if not target.exists():
            temporary=target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
            writer=None;schema=None
            try:
                with temporary.open('xb') as stream:
                    for batch in batches(path,columns,batch_rows):
                        if writer is None:
                            schema=batch.schema;writer=ipc.new_file(stream,schema)
                        if batch.schema!=schema: batch=pa.Table.from_batches([batch]).cast(schema).to_batches()[0]
                        writer.write_batch(batch)
                    if writer is None:
                        frame=pd.read_parquet(path,columns=columns) if Path(path).suffix=='.parquet' else pd.read_csv(path,usecols=columns,nrows=0)
                        writer=ipc.new_file(stream,pa.Schema.from_pandas(frame,preserve_index=False))
                    writer.close()
                if source_version(path)!=version: raise ValueError(f'Input changed during cache build: {path}')
                temporary.rename(target)
            except Exception:
                # Retain our partial file for diagnosis; never remove unrelated cache contents.
                raise
    with pa.memory_map(str(target),'r') as mapping:
        table=ipc.open_file(mapping).read_all()
        if symbols is not None:
            table=table.filter(pc.is_in(table['order_book_id'],value_set=pa.array(symbols)))
        return table.to_pandas()
