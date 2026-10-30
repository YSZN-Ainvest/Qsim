from dataclasses import dataclass
from pathlib import Path
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from .source_cache import shared_frame,source_version

from .config import schedule


@dataclass
class DayCube:
    """Each value tensor has axes [time (one day), security, bar]."""
    date: str
    calendar: pd.DataFrame
    symbols: list
    values: dict
    member: np.ndarray
    suspended: np.ndarray
    st: np.ndarray
    limit_up: np.ndarray
    limit_down: np.ndarray
    adjustment: np.ndarray
    eod_close: np.ndarray

    def bar(self, index):
        return {name: value[0, :, index] for name, value in self.values.items()}


class MarketData:
    def __init__(self, config, input_policy=None):
        self.config = config
        self.input_policy = input_policy
        self.root = Path(config['data']['root'])
        self.raw = Path(config['data']['raw_root'])
        self.freq, self.start_min, self.end_min = schedule(config['backtest']['frequency'])
        self.daily_calendar = pd.read_parquet(self.root / 'calendar/1d.parquet')
        self.calendar = self.daily_calendar if self.freq == '1d' else pd.read_parquet(self.root / f'calendar/{self.freq}.parquet')
        # Cache immutable axis lookups, not features. PIT intervention still runs
        # on the input frame before any numeric scatter or history observation.
        self._day_rows = self.calendar.groupby('date', sort=False).indices
        if input_policy is not None:
            self._bar_ends = self.calendar.set_index('bar_id').bar_end
            self._daily_bar_ends = self.daily_calendar.set_index('bar_id').bar_end
        if input_policy is not None:
            for path in [self.root/'calendar/1d.parquet', self.root/f'calendar/{self.freq}.parquet', self.root/'securities.parquet']:
                input_policy.record(path)
            if (self.root/'catalog.json').exists():
                input_policy.record(self.root/'catalog.json')
        start, end = config['backtest']['start'], config['backtest']['end']
        if start < self.daily_calendar.date.min() or end > self.daily_calendar.date.max():
            raise ValueError('Requested dates outside the generated cache coverage')
        sec = pd.read_parquet(self.root / 'securities.parquet')
        symbols = sorted(sec.loc[sec.type.eq('CS'), 'order_book_id'].unique())
        u = config['universe']
        if u['kind'] not in ('all_a', 'symbols', 'table'):
            raise ValueError('universe.kind must be all_a, symbols, or table')
        if u['kind'] == 'symbols':
            if not u['symbols'] or not set(u['symbols']) <= set(symbols):
                raise ValueError('Supply valid exchange-qualified universe.symbols')
            symbols = sorted(set(u['symbols']))
        if u['kind'] == 'table' and not u['path']:
            raise ValueError('Custom universe requires path')
        self.symbols = symbols
        self._symbol_index = pd.Index(symbols)
        dates = self.daily_calendar.date.drop_duplicates().tolist()
        self.run_dates = [d for d in dates if start <= d <= end]
        if not self.run_dates:
            raise ValueError('No trading days in requested range')
        first = dates.index(self.run_dates[0])
        self.warmup_dates = dates[max(0, first-config['data']['warmup_days']):first]

    def _frame(self, category, freq, date):
        p = self.root / category / freq / date[:4] / (date.replace('-','')+'.parquet')
        if hasattr(self,'last_sources'): self.last_sources.append(source_version(p))
        if self.config['data']['shared_cache']:
            frame=shared_frame(p,self.config['data']['cache_directory'],symbols=self.symbols,batch_rows=self.config['data']['batch_rows'])
        else:
            frame = pq.read_table(p, filters=[('order_book_id','in',self.symbols)]).to_pandas()
        if self.input_policy is not None:
            self.input_policy.record(p)
            if category == 'universe':
                available = pd.Series(pd.Timestamp(date+' 08:00',tz='Asia/Shanghai'),index=frame.index)
            else:
                ends = self._daily_bar_ends if freq == '1d' else self._bar_ends
                available = frame.bar_id.map(ends)
                if available.isna().any():
                    raise ValueError('Market row lacks availability timestamp')
            frame = self.input_policy.apply(frame, available, str(p))
        return frame

    def day_calendar(self, date):
        cal = self.calendar.iloc[self._day_rows.get(date, [])].copy()
        if self.freq != '1d':
            a = cal.bar_start.dt.hour * 60 + cal.bar_start.dt.minute
            b = cal.bar_end.dt.hour * 60 + cal.bar_end.dt.minute
            cal = cal.loc[a.ge(self.start_min) & b.le(self.end_min)]
        return cal.reset_index(drop=True)

    def load_day(self, date):
        self.last_sources=[]
        cal = self.day_calendar(date)
        prices = self._frame('prices', self.freq, date)
        vwap = self._frame('vwap', self.freq, date)
        if prices.duplicated(['order_book_id','bar_id']).any() or vwap.duplicated(['order_book_id','bar_id']).any():
            raise ValueError(f'Duplicate market keys: {date}')
        tensors = {}
        bar_index = pd.Index(cal.bar_id)
        for frame, cols in [(prices,['open_adj','close_adj','bar_complete']),
                            (vwap,['vwap_raw','vwap_adj','volume','total_turnover','vwap_outside_ohlc'])]:
            securities = self._symbol_index.get_indexer(frame.order_book_id)
            bars = bar_index.get_indexer(frame.bar_id)
            selected = (securities >= 0) & (bars >= 0)
            all_selected = bool(selected.all())
            if not all_selected:
                securities, bars = securities[selected], bars[selected]
            for col in cols:
                # Physical storage is [bar, security], so day.bar(j) is a
                # contiguous float64 vector. Public [1, security, bar] stays.
                aligned = np.full((len(cal), len(self.symbols)), np.nan, dtype=np.float64)
                # Avoid a full mixed-dtype frame copy and repeated integer
                # index copies for every field (material on a 1-minute day).
                values = frame[col].to_numpy(dtype=np.float64, na_value=np.nan)
                aligned[bars, securities] = values if all_selected else values[selected]
                tensors[col] = aligned.T[None, :, :]
        pool = self._frame('universe','all_a',date).set_index('order_book_id').reindex(self.symbols)
        raw_path = self.raw / 'daily_bar' / date[:4] / (date.replace('-','')+'.csv')
        self.last_sources.append(source_version(raw_path))
        columns=['order_book_id','close','limit_up','limit_down']
        raw = shared_frame(raw_path,self.config['data']['cache_directory'],columns,self.symbols,self.config['data']['batch_rows']) if self.config['data']['shared_cache'] else pd.read_csv(raw_path,usecols=columns)
        if self.input_policy is not None:
            self.input_policy.record(raw_path)
            # Raw daily prices are end-of-day inputs, unavailable to an 08:00 alpha.
            available = pd.Series(pd.Timestamp(date+' 15:00',tz='Asia/Shanghai'),index=raw.index)
            raw = self.input_policy.apply(raw, available, str(raw_path))
        raw = raw.set_index('order_book_id').reindex(self.symbols)
        daily = prices if self.freq == '1d' else self._frame('prices','1d',date)
        eod = daily.set_index('order_book_id').reindex(self.symbols).close_adj.to_numpy(float)
        close_raw = raw.close.to_numpy(float)
        adjustment = np.divide(eod,close_raw,out=np.full(len(eod),np.nan),where=close_raw>0)
        return DayCube(date,cal,self.symbols,tensors,
                       pool.in_all_a.fillna(False).to_numpy(bool),
                       pool.suspended.to_numpy(dtype=float,na_value=np.nan),
                       pool.st_status.to_numpy(dtype=float,na_value=np.nan),
                       raw.limit_up.to_numpy(float),raw.limit_down.to_numpy(float),adjustment,eod)
