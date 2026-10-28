"""User tables carry actual knowledge timestamps, not reporting periods."""
from pathlib import Path
import numpy as np
import pandas as pd


class PITTable:
    def __init__(self, path, columns, symbols, max_date):
        p = Path(path)
        frame = pd.read_parquet(p) if p.suffix == '.parquet' else pd.read_csv(p)
        required = {'order_book_id', 'available_at', *columns}
        if not required <= set(frame):
            raise ValueError(f'{path}: missing {required - set(frame)}')
        timestamps = pd.to_datetime(frame.available_at)
        if timestamps.dt.tz is None:
            timestamps = timestamps.dt.tz_localize('Asia/Shanghai')
        else:
            timestamps = timestamps.dt.tz_convert('Asia/Shanghai')
        frame['available_at'] = timestamps
        if frame.available_at.isna().any():
            raise ValueError(f'{path}: invalid available_at')
        # No OS rows are exposed to the factor computation during an IS-only run.
        end = pd.Timestamp(max_date, tz='Asia/Shanghai') + pd.Timedelta(days=1)
        frame = frame.loc[frame.available_at.lt(end) & frame.order_book_id.isin(symbols)]
        if frame.duplicated(['available_at','order_book_id']).any():
            raise ValueError(f'{path}: duplicate knowledge-time/security keys')
        if frame.available_at.isna().any():
            raise ValueError(f'{path}: invalid available_at')
        self.frame = frame.sort_values('available_at').reset_index(drop=True)
        self.times = self.frame.available_at.to_numpy()
        self.columns = columns
        self.symbols = symbols
        self.state = pd.DataFrame(index=symbols, columns=columns, dtype=object)
        self.cursor = 0
        self.last = None

    def at(self, when):
        if self.last is not None and when < self.last:
            raise ValueError('PITTable requires monotonic decision timestamps')
        stop = self.cursor
        while stop < len(self.frame) and self.times[stop] <= when:
            stop += 1
        if stop > self.cursor:
            update = self.frame.iloc[self.cursor:stop].drop_duplicates('order_book_id', keep='last')
            self.state.loc[update.order_book_id, self.columns] = update[self.columns].to_numpy()
        self.cursor, self.last = stop, when
        return self.state.copy()


def boolean_values(series):
    """Unknown stays NaN; reject ambiguous truthiness such as string 'False'."""
    mapping = {True: 1., False: 0., 1: 1., 0: 0., 'true': 1., 'false': 0.,
               'True': 1., 'False': 0., '1': 1., '0': 0.}
    bad = series.notna() & ~series.isin(mapping)
    if bad.any():
        raise ValueError('Boolean columns must contain true/false/0/1/null')
    return series.map(mapping).to_numpy(dtype=float, na_value=np.nan)
