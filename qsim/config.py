from copy import deepcopy
from pathlib import Path
import re
import math
import tomllib

import pandas as pd

DEFAULTS = {
    'data': {'root': '/mnt/nas/qinml_cache/backtesting/20260930',
             'raw_root': '/mnt/nas/data/whole_history', 'warmup_days': 40},
    'backtest': {'start': '2023-01-03', 'end': '2023-03-31', 'frequency': '0800',
                 'allow_os': False, 'os_start': '2024-01-01', 'mode': 'StatsEvery',
                 'holding_period': 1, 'forecast_period': 5, 'groups': 10,
                 'capital': 10000000.0},
    'stats': {'nIntvDelay': 1, 'idelayEach': 0, 'ileadEach': None,
              'isTradeT1': True, 'nbar': 1, 'priceMode': 'vwap',
              'cleareod': False, 'signal_times': []},
    'universe': {'kind': 'all_a', 'symbols': [], 'path': ''},
    'execution': {'fee_bps': 5.0, 'sell_tax_bps': 0.0, 'slippage_bps': 0.0,
                  'borrow_bps_annual': 0.0, 'participation': 0.01,
                  'limit_filter': True, 'suspension_filter': True,
                  'reject_bad_vwap': True, 'short_mode': 'synthetic', 'enforce_t1': True},
    'operations': {'exclude_st': True, 'st_unknown': 'error', 'st_path': '',
                   'winsor_mad': 5.0, 'standardize': True,
                   'exposures_path': '', 'neutralize_styles': [],
                   'neutralize_industries': [], 'min_cross_section': 20},
    'output': {'dump': True, 'directory': '/mnt/nas/qinml_cache/qsim_runs',
               'name': 'run', 'plots': True, 'positions': False},
    'factors': [{'name': 'momentum', 'kind': 'momentum', 'lookback': 20, 'weight': 1.0}],
}


def schedule(spec):
    """The supplied interval is [start, end); bars finish no later than end."""
    if spec in ('0800', '1d', 'daily'):
        return '1d', None, None
    match = re.fullmatch(r'(\d{4})-(\d{4})-(1|5|30)', str(spec))
    if not match:
        raise ValueError('frequency must be 0800 or HHMM-HHMM-{1,5,30}')
    a, b, size = match.groups()
    def minute(s):
        h, m = int(s[:2]), int(s[2:])
        if h > 23 or m > 59:
            raise ValueError('Invalid clock time')
        return 60 * h + m
    a, b, size = minute(a), minute(b), int(size)
    if not 570 <= a < b <= 900 or a % size or b % size:
        raise ValueError('Window must lie in 09:30–15:00 and align to the bar size')
    if not any(a <= t and t + size <= b for t in range(570,900,size)
               if t < 690 or t >= 780):
        raise ValueError('Window has no trading bars')
    return f'{size}m', a, b


def load_config(value):
    if isinstance(value, (str, Path)):
        with open(value, 'rb') as stream:
            value = tomllib.load(stream)
    cfg = deepcopy(DEFAULTS)
    if 'livetest' in value:
        from .livetest_config import options
        cfg['livetest'] = options(value['livetest'])
    unknown = set(value) - set(cfg)
    if unknown:
        raise ValueError(f'Unknown config sections: {unknown}')
    for key, val in value.items():
        if key == 'livetest':
            continue
        if isinstance(cfg[key], dict):
            extra = set(val) - set(cfg[key])
            if extra:
                raise ValueError(f'Unknown {key} options: {extra}')
            cfg[key].update(val)
        else:
            cfg[key] = deepcopy(val)
    b = cfg['backtest']
    for section,names in {
        'backtest':['allow_os'],
        'execution':['limit_filter','suspension_filter','reject_bad_vwap','enforce_t1'],
        'stats':['isTradeT1','cleareod'],
        'operations':['exclude_st','standardize'],
        'output':['dump','plots','positions'],
    }.items():
        for name in names:
            if type(cfg[section][name]) is not bool:
                raise ValueError(f'{section}.{name} must be true or false, not a string')
    for field in ('start', 'end', 'os_start'):
        b[field] = str(pd.Timestamp(b[field]).date())
    if b['start'] > b['end']:
        raise ValueError('start must be <= end')
    if b['os_start'] > '2024-01-01':
        raise ValueError('OS boundary cannot be moved later than 2024-01-01')
    if b['end'] >= b['os_start'] and not b['allow_os']:
        raise ValueError('OS access denied: set backtest.allow_os=true explicitly')
    aliases = {'statsevery':'StatsEvery','statsinterval':'StatsInterval','cohort':'Cohort'}
    b['mode'] = aliases.get(b['mode'],b['mode'])
    if b['mode'] not in ('StatsEvery','StatsInterval','Cohort'):
        raise ValueError('mode must be StatsEvery, StatsInterval or Cohort (legacy simulator)')
    s = cfg['stats']
    for name,minimum in [('nIntvDelay',1),('idelayEach',0),('nbar',1)]:
        if type(s[name]) is not int or s[name]<minimum:
            raise ValueError(f'stats.{name} must be an integer >= {minimum}')
    if s['ileadEach'] is not None and (type(s['ileadEach']) is not int or s['ileadEach']<1):
        raise ValueError('ileadEach must be null/omitted or a positive integer')
    if s['priceMode'] not in ('vwap','open','close'):
        raise ValueError('priceMode must be vwap, open or close')
    if b['mode']=='StatsEvery' and s['priceMode']!='vwap':
        raise ValueError('StatsEvery currently uses VWAP; priceMode applies to StatsInterval')
    if b['mode']=='StatsInterval' and s['nIntvDelay']!=1:
        raise ValueError('nIntvDelay is a StatsEvery option; StatsInterval signals start each segment')
    freq = schedule(b['frequency'])[0]
    if freq=='1d' and s['nIntvDelay']!=1:
        raise ValueError('Daily execution uses same-day VWAP (nIntvDelay=1)')
    if freq=='1d' and b['mode']=='StatsInterval' and s['nbar']!=1:
        raise ValueError('Daily StatsInterval supports nbar=1; multi-bar windows are intraday')
    if not isinstance(s['signal_times'],list) or len(set(s['signal_times']))!=len(s['signal_times']):
        raise ValueError('signal_times must be a list of unique HHMM strings')
    for clock in s['signal_times']:
        if not isinstance(clock,str) or not re.fullmatch(r'\d{4}',clock) or int(clock[:2])>23 or int(clock[2:])>59:
            raise ValueError('signal_times must contain valid HHMM strings')
    for name in ('holding_period', 'forecast_period', 'groups'):
        if type(b[name]) is not int or b[name] < (2 if name == 'groups' else 1):
            raise ValueError(f'{name} must be a positive integer (groups >= 2)')
    if b['mode']!='Cohort' and b['holding_period']!=1:
        raise ValueError('holding_period belongs to legacy Cohort mode. Use 1 for daily same-time '
                         'StatsEvery rebalancing, or stats.nbar for StatsInterval spans.')
    if not math.isfinite(b['capital']) or b['capital'] <= 0:
        raise ValueError('capital must be positive')
    schedule(b['frequency'])
    e = cfg['execution']
    if e['short_mode'] != 'synthetic':
        raise ValueError('Only explicitly synthetic shorting is implemented')
    for name in ('fee_bps','sell_tax_bps','slippage_bps','borrow_bps_annual'):
        if not 0 <= e[name] < 10000:
            raise ValueError(f'Invalid cost: {name}')
    if not 0 < e['participation'] <= 1:
        raise ValueError('participation must be in (0,1]')
    if cfg['operations']['st_unknown'] not in ('error', 'exclude', 'allow'):
        raise ValueError('st_unknown must be error, exclude or allow')
    if not cfg['factors'] or len({f['name'] for f in cfg['factors']}) != len(cfg['factors']):
        raise ValueError('Specify uniquely named factors')
    for f in cfg['factors']:
        if not math.isfinite(float(f.get('weight',1))):
            raise ValueError('Factor weights must be finite')
        if f.get('kind') not in ('momentum','reversal','volatility','table'):
            raise ValueError('Factor kind must be momentum/reversal/volatility/table')
        if f['kind'] != 'table' and (type(f.get('lookback')) is not int or f['lookback'] < 1):
            raise ValueError('Builtin factors need a positive integer lookback')
        if f['kind'] == 'table' and not f.get('path'):
            raise ValueError('Table factors require path and available_at timestamps')
    if not any(float(f.get('weight', 1)) != 0 for f in cfg['factors']):
        raise ValueError('At least one factor weight must be nonzero')
    if type(cfg['data']['warmup_days']) is not int or cfg['data']['warmup_days'] < 0:
        raise ValueError('warmup_days must be >= 0')
    op = cfg['operations']
    if not math.isfinite(op['winsor_mad']) or op['winsor_mad'] < 0:
        raise ValueError('winsor_mad must be finite and >= 0')
    if type(op['min_cross_section']) is not int or op['min_cross_section'] < 2:
        raise ValueError('min_cross_section must be an integer >= 2')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', cfg['output']['name']):
        raise ValueError('output.name must be a simple run name')
    return cfg
