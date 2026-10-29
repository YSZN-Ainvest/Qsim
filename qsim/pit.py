"""Point-in-time tables backed by a bounded, shared disk index."""
import os
import numpy as np
from .pit_store import StreamingPITTable


class PITTable(StreamingPITTable):
    def __init__(self,path,columns,symbols,max_date,input_policy=None):
        super().__init__(path,columns,symbols,max_date,input_policy,
            cache_directory=os.environ.get('QSIM_CACHE_ROOT','/mnt/nas/qinml_cache/qsim/cache'))


def boolean_values(series):
    """Unknown stays NaN; reject ambiguous truthiness such as string 'False'."""
    mapping = {True: 1., False: 0., 1: 1., 0: 0., 'true': 1., 'false': 0.,
               'True': 1., 'False': 0., '1': 1., '0': 0.}
    bad = series.notna() & ~series.isin(mapping)
    if bad.any():
        raise ValueError('Boolean columns must contain true/false/0/1/null')
    return series.map(mapping).to_numpy(dtype=float, na_value=np.nan)
