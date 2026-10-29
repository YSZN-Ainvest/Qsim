"""Shared decision scheduling and eligibility for backtests and alpha replay."""
import numpy as np
import pandas as pd

from .pit import boolean_values


def decision_time(date, row, frequency):
    return pd.Timestamp(date + ' 08:00', tz='Asia/Shanghai') if frequency == '1d' else row.bar_start


def signal_plan(calendar, frequency, config):
    s = config['stats']
    clocks = ['0800' if frequency == '1d' else row.bar_start.strftime('%H%M')
              for _, row in calendar.iterrows()]
    if config['backtest']['mode'] == 'Cohort':
        return clocks, {j: [j] for j in range(len(calendar))}, 0
    if s['signal_times'] and not set(s['signal_times']) <= set(clocks):
        raise ValueError('signal_times must be present on the configured sampling grid')
    effective = [j for j, clock in enumerate(clocks) if not s['signal_times'] or clock in s['signal_times']]
    if config['backtest']['mode'] == 'StatsEvery':
        plan = {j: [j + s['nIntvDelay'] - 1] for j in effective if j + s['nIntvDelay'] - 1 < len(calendar)}
        skipped = len(effective) - len(plan)
    else:
        plan = {effective[p]: effective[p:p + s['nbar']] for p in range(0, len(effective), s['nbar'])}
        skipped = 0
    if not plan:
        raise ValueError('No executable signals: check signal_times and nIntvDelay')
    return clocks, plan, skipped


def eligible_at(day, when, config, st=None, universe=None):
    o = config['operations']
    eligible = day.member.copy()
    if universe is not None:
        eligible &= boolean_values(universe.at(when).in_universe) == 1
    if config['execution']['suspension_filter']:
        eligible &= day.suspended == 0
    count = 0
    if o['exclude_st']:
        values = boolean_values(st.at(when).is_st) if st is not None else day.st
        unknown = eligible & ~np.isfinite(values)
        count = int(unknown.sum())
        if count and o['st_unknown'] == 'error':
            raise ValueError(f'{day.date}: historical ST status missing for {count} securities')
        eligible &= (values == 0) | (np.isnan(values) & (o['st_unknown'] == 'allow'))
    return eligible, count
