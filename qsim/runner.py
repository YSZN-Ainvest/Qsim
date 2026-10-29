from collections import deque
import hashlib
import json
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .config import load_config
from .data import MarketData
from .engine import Ledger
from .factors import FactorModel
from .metrics import correlation,daily_curve,report_metrics
from .operations import groups_from_score
from .pit import PITTable
from .report import dump_results
from .alpha_events import decision_time, eligible_at


def run(config):
    config=load_config(config)  # OS check runs before any data are opened.
    if config['backtest']['mode'] != 'Cohort':
        from .stats_runner import run_stats
        return run_stats(config)
    started=time.monotonic()
    market=MarketData(config); b=config['backtest']; o=config['operations']
    symbols=market.symbols
    model=FactorModel(config,symbols)
    ledger=Ledger(config,symbols)
    st=PITTable(o['st_path'],['is_st'],symbols,b['end']) if o['st_path'] else None
    universe=PITTable(config['universe']['path'],['in_universe'],symbols,b['end']) if config['universe']['kind']=='table' else None
    notes=['Synthetic short positions: no historical borrow inventory or recall model.',
           'Adjusted fractional units model total return; not an exact corporate-action/share ledger.',
           'VWAP limit/volume checks are bar-level approximations, not order-book queue simulation.',
           'No forced liquidation at the end; open positions remain marked and are exported.',
           'Universe uses cached listing lifecycle from a security snapshot; historical completeness is not independently certified.']
    if o['exclude_st'] and o['st_unknown']=='allow':
        text='Historical ST unknowns are explicitly allowed by this config; this run is not fully ST-filtered.'
        notes.append(text);warnings.warn(text,stacklevel=2)
    for i,date in enumerate(market.warmup_dates):
        day=market.load_day(date)
        for j in range(len(day.calendar)):
            model.observe(day.bar(j)['close_adj'])
    pending=deque(); diagnostics=[]; positions=[]
    previous_score=np.full(len(symbols),np.nan)
    index=0; unknown_st=0; no_signal=0; split_labels=0
    prior_date=None
    for day_index,date in enumerate(market.run_dates):
        day=market.load_day(date)
        for j,row in day.calendar.iterrows():
            when=decision_time(date,row,market.freq)
            data=day.bar(j)
            eligible,unknown=eligible_at(day,when,config,st,universe)
            unknown_st+=unknown
            score=model.compute(when,eligible)
            groups=groups_from_score(score,b['groups'])
            if not np.any(groups>=0):
                no_signal+=1
            # Forecast labels are resolved only after their future bar arrives. Never passed to model.
            label_price=np.where((data['bar_complete']==1)&(data['vwap_adj']>0),data['vwap_adj'],np.nan)
            if config['execution']['reject_bad_vwap']:
                label_price=np.where(data['vwap_outside_ohlc']==0,label_price,np.nan)
            while pending and pending[0]['due']<=index:
                item=pending.popleft(); diag=diagnostics[item['record']]
                if (item['date']<b['os_start']) != (date<b['os_start']):
                    split_labels+=1;diag['label_status']='split_embargo';continue
                target=np.divide(label_price,item['entry'],out=np.full(len(symbols),np.nan),where=item['entry']>0)-1
                diag['ic']=correlation(item['score'],target)
                diag['rank_ic']=correlation(item['score'],target,True)
                diag['label_count']=int(np.count_nonzero(np.isfinite(item['score'])&np.isfinite(target)))
                diag['label_end']=str(row.bar_end);diag['label_status']='complete'
                for group in range(b['groups']):
                    x=target[(item['groups']==group)&np.isfinite(target)]
                    diag[f'G{group+1:02}_forecast_return']=float(np.mean(x)) if len(x) else np.nan
            ledger.step(index,day_index,row.bar_end,data,day,groups,eligible,model.reference_price())
            actual=ledger.q[-2]+ledger.q[-1]
            actual=actual*np.nan_to_num(ledger.last)
            diagnostics.append(dict(timestamp=when,date=date,ic=np.nan,rank_ic=np.nan,label_count=0,
                label_end=None,label_status='pending',auto_corr=correlation(score,previous_score),
                TC=correlation(score,actual),signal_count=int(np.isfinite(score).sum())))
            pending.append({'due':index+b['forecast_period'],'entry':label_price.copy(),
                            'score':score.copy(),'groups':groups,'record':len(diagnostics)-1,'date':date})
            previous_score=score.copy()
            model.observe(data['close_adj'])
            index+=1
        elapsed=(pd.Timestamp(date)-pd.Timestamp(prior_date)).days if prior_date else 1
        ledger.end_day(pd.Timestamp(date+' 15:00',tz='Asia/Shanghai'),day_index,day.eod_close,elapsed)
        if config['output']['positions']:
            positions.extend(dict(date=date,**r) for r in ledger.positions())
        prior_date=date
        if day_index==0 or (day_index+1)%20==0 or day_index==len(market.run_dates)-1:
            print(f"{date}: {day_index+1}/{len(market.run_dates)} days; LS equity={ledger.equity[-2:].sum():,.2f}",flush=True)
    for item in pending:
        diagnostics[item['record']]['label_status']='right_censored'
    events=pd.DataFrame(ledger.records)
    diag=pd.DataFrame(diagnostics);curve=daily_curve(events)
    summary=report_metrics(curve,diag,config)
    if not events.turnover.gt(0).any():
        notes.append('NO TRADES: inspect factor coverage, ST policy, warmup and execution filters.')
    if ledger.audit['stale_mark_cells']:
        notes.append('Some held securities use last valid marks; inspect stale_mark_cells and final_positions.')
    audit=dict(schema_version=1,status='complete',elapsed_seconds=round(time.monotonic()-started,3),
        frequency=market.freq,schedule=b['frequency'],tensor_axes=['day','security','bar'],
        symbols=len(symbols),days=len(market.run_dates),bars=index,warmup_days=len(market.warmup_dates),
        os_enabled=b['allow_os'],os_start=b['os_start'],forecast_right_censored=len(pending),
        forecast_split_embargo=split_labels,st_unknown_security_bars=unknown_st,
        no_signal_bars=no_signal,notes=notes,execution=ledger.audit,
        config_sha256=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest())
    audit['code_sha256'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                            for p in sorted(Path(__file__).parent.glob('*.py'))}
    catalog = Path(config['data']['root'])/'catalog.json'
    if catalog.is_file():
        audit['cache_catalog_sha256'] = hashlib.sha256(catalog.read_bytes()).hexdigest()
    result={'summary':summary,'daily':curve,'events':events,'diagnostics':diag,
            'final_positions':pd.DataFrame(ledger.positions(),columns=['book','order_book_id','adjusted_units','mark_adj','market_value']),
            'positions':pd.DataFrame(positions),'audit':audit,'config':config}
    result['output_directory']=dump_results(result,config)
    return result
