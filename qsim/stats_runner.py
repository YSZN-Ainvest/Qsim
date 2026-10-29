"""StatsEvery = separate daily strategies; StatsInterval = one sequential strategy."""
import hashlib
import json
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

from .data import MarketData
from .factors import FactorModel
from .metrics import correlation,daily_curve,report_metrics
from .operations import groups_from_score
from .pit import PITTable
from .report import dump_results
from .stats import StatsEvery,StatsInterval,portfolio_weights,aggregate_window
from .target_engine import TargetLedger
from .alpha_events import decision_time, eligible_at, signal_plan


def run_stats(config):
    started=time.monotonic();b=config['backtest'];s=config['stats'];o=config['operations']
    market=MarketData(config);symbols=market.symbols;n=len(symbols)
    every=b['mode']=='StatsEvery'
    calculator=(StatsEvery if every else StatsInterval)(config,symbols)
    model=FactorModel(config,symbols)
    st=model.registry.get(o['st_path'])
    universe=model.registry.get(config['universe']['path']) if config['universe']['kind']=='table' else None
    notes=['Statistics conventions are QSim choices documented in STATS.md, not an exact ksim/zsim replica.',
           'stats_* tables describe target-alpha research attribution; daily/summary/events are constrained cash simulation.',
           'Synthetic short side; adjusted fractional units; bar-level fills, no order queue model.',
           'All fixed-time portfolios start with the configured capital independently. comparison_mean is an equal-capital comparison basket.',
           'ST and universe snapshot coverage limitations from the source cache still apply.']
    if o['exclude_st'] and o['st_unknown']=='allow':
        notes.append('Historical ST unknowns explicitly allowed; not a fully ST-filtered run.')
        warnings.warn(notes[-1],stacklevel=2)
    previous_close=np.full(n,np.nan)
    for date in market.warmup_dates:
        day=market.load_day(date)
        for j in range(len(day.calendar)):
            model.observe(day.bar(j)['close_adj'])
        previous_close=day.eod_close.copy()
    ledgers={};diagnostics=[];pending=[];positions=[];prior_scores={}
    index=0;unknown_st=0;no_signal=0;skipped_delay=0;prior_date=None
    horizon=s['ileadEach'] or b['forecast_period']
    for day_index,date in enumerate(market.run_dates):
        day=market.load_day(date);cal=day.calendar
        clocks,plan,skipped=signal_plan(cal,market.freq,config)
        skipped_delay+=skipped
        for j in plan:
            slot=clocks[j] if every else 'interval'
            if slot not in ledgers:
                ledgers[slot]=TargetLedger(config,symbols)
        executions={};first=True
        for j,row in cal.iterrows():
            when=decision_time(date,row,market.freq)
            data=day.bar(j)
            if j in plan:
                eligible,unknown=eligible_at(day,when,config,st,universe)
                unknown_st+=unknown
                score=model.compute(when,eligible)
                groups=groups_from_score(score,b['groups'])
                alpha=portfolio_weights(groups,b['groups'])
                fire=np.any(groups>=0)
                no_signal+=int(not fire)
                slot=clocks[j] if every else 'interval'
                if not fire:
                    alpha=calculator.previous.get(slot,np.zeros_like(alpha)).copy() if every else calculator.last_alpha.copy()
                    alpha[:,~eligible]=0
                execution_fire=bool(fire or np.any((np.abs(ledgers[slot].q)>1e-10)&~eligible[None,:]))
                execution_index=plan[j][-1]
                diag=dict(timestamp=when,date=date,slot=slot,ic=np.nan,rank_ic=np.nan,label_count=0,
                    label_end=None,label_status='pending',auto_corr=correlation(score,prior_scores.get(slot,np.full(n,np.nan))),
                    TC=np.nan,signal_count=int(np.isfinite(score).sum()),is_last=execution_index==len(cal)-1,
                    ic_undefined=False)
                diagnostics.append(diag);record=len(diagnostics)-1
                pending.append(dict(entry_index=index+s['idelayEach'],due=index+s['idelayEach']+horizon,
                                    entry=None,score=score.copy(),record=record,date=date))
                prior_scores[slot]=score.copy()
                executions[execution_index]=dict(slot=slot,alpha=alpha,reference=model.reference_price(),
                    fire=execution_fire,indices=plan[j],record=record,score=score,signal_index=j,first=first)
                first=False
            if j in executions:
                item=executions.pop(j);slot=item['slot'];ledger=ledgers[slot]
                fill_data=day.bar(j) if every else aggregate_window(day,item['indices'],s['priceMode'])
                ledger.rebalance(day_index,row.bar_end,fill_data,day,item['alpha'],item['reference'],item['fire'])
                evaluation_price=np.where(fill_data['bar_complete']==1,fill_data['vwap_adj'],np.nan)
                if config['execution']['reject_bad_vwap']:
                    evaluation_price=np.where(fill_data['vwap_outside_ohlc']==0,evaluation_price,np.nan)
                extra=dict(signal_time=clocks[item['signal_index']],execution_end=str(row.bar_end),
                           actual_bars=len(item['indices']),is_last=j==len(cal)-1)
                if every:
                    calculator.observe(date,slot,item['alpha'],previous_close,evaluation_price,day.eod_close,**extra)
                else:
                    calculator.observe(date,clocks[item['signal_index']],item['alpha'],previous_close,
                                       evaluation_price,fill_data['close_adj'],first=item['first'],**extra)
                actual=(ledger.q[-2]+ledger.q[-1])*np.nan_to_num(ledger.last)
                diagnostics[item['record']]['TC']=correlation(item['score'],actual)
            label=np.where((data['bar_complete']==1)&(data['vwap_adj']>0),data['vwap_adj'],np.nan)
            if config['execution']['reject_bad_vwap']:
                label=np.where(data['vwap_outside_ohlc']==0,label,np.nan)
            remaining=[]
            for item in pending:
                if item['entry_index']==index:
                    item['entry']=label.copy()
                if item['due']==index:
                    diag=diagnostics[item['record']]
                    target=np.divide(label,item['entry'],out=np.full(n,np.nan),where=item['entry']>0)-1
                    diag['ic']=correlation(item['score'],target)
                    rank=correlation(item['score'],target,True)
                    diag['ic_undefined']=bool(not np.isfinite(rank))
                    diag['rank_ic']=rank if every or np.isfinite(rank) else 0.
                    diag['label_count']=int(np.count_nonzero(np.isfinite(target)&np.isfinite(item['score'])))
                    diag['label_end']=str(row.bar_end);diag['label_status']='complete'
                else:
                    remaining.append(item)
            pending=remaining
            model.observe(data['close_adj']);index+=1
        elapsed=(pd.Timestamp(date)-pd.Timestamp(prior_date)).days if prior_date else 1
        for slot,ledger in ledgers.items():
            ledger.end_day(pd.Timestamp(date+' 15:00',tz='Asia/Shanghai'),day_index,day.eod_close,elapsed)
            if config['output']['positions']:
                positions.extend(dict(date=date,slot=slot,**r) for r in ledger.positions())
        previous_close=day.eod_close.copy();prior_date=date
        if day_index==0 or (day_index+1)%20==0 or day_index==len(market.run_dates)-1:
            print(f"{date}: {day_index+1}/{len(market.run_dates)} days; {b['mode']}, {len(ledgers)} independent portfolio(s)",flush=True)
    for item in pending:
        diagnostics[item['record']]['label_status']='right_censored'
    diag=pd.DataFrame(diagnostics);slot_curves=[];slot_reports=[];event_frames=[];final_positions=[]
    for slot,ledger in ledgers.items():
        events=pd.DataFrame(ledger.records);curve=daily_curve(events)
        stat=report_metrics(curve,diag.loc[diag.slot.eq(slot)],config)
        slot_curves.append(curve.assign(slot=slot));slot_reports.append(stat.assign(slot=slot))
        event_frames.append(events.assign(slot=slot))
        final_positions.extend(dict(slot=slot,**r) for r in ledger.positions())
    slot_daily=pd.concat(slot_curves,ignore_index=True)
    # Equal-capital comparison of independent strategies. It does NOT replay all slots in one account.
    numeric=slot_daily.select_dtypes(include='number').columns.tolist()
    curve=slot_daily.groupby(['date','book'],as_index=False)[numeric].mean()
    curve=curve.sort_values(['book','date'],ignore_index=True)
    curve['return']=curve.pnl/curve.previous_equity
    curve['tvr']=curve.turnover/curve.previous_equity
    curve['net_tvr']=curve.net_turnover/curve.previous_equity
    curve['net']=curve.equity/curve.groupby('book').previous_equity.transform('first')
    summary=report_metrics(curve,diag,config)
    tables=calculator.tables()
    if not every:
        for name,part in [('stats_ic_sum',diag),('stats_ic_sumnoend',diag.loc[~diag.is_last])]:
            tables[name]=part.groupby('date',as_index=False).agg(rank_ic=('rank_ic','mean'),
                valid_labels=('rank_ic','count'),undefined_to_zero=('ic_undefined','sum'))
    audit=dict(schema_version=2,status='complete',mode=b['mode'],frequency=market.freq,
        schedule=b['frequency'],symbols=n,days=len(market.run_dates),bars=index,
        portfolio_count=len(ledgers),summary_basis='comparison_mean' if every else 'single_interval_portfolio',
        elapsed_seconds=round(time.monotonic()-started,3),
        forecast_right_censored=len(pending),
        skipped_delay_signals=skipped_delay,st_unknown_security_bars=unknown_st,no_signal_bars=no_signal,
        execution={slot:l.audit for slot,l in ledgers.items()},notes=notes,
        config_sha256=hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
        code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(__file__).parent.glob('*.py'))})
    catalog=Path(config['data']['root'])/'catalog.json'
    if catalog.is_file():
        audit['cache_catalog_sha256']=hashlib.sha256(catalog.read_bytes()).hexdigest()
    audit['warmup_days']=len(market.warmup_dates)
    audit['tensor_axes']=['day','security','bar']
    result=dict(summary=summary,daily=curve,diagnostics=diag,events=pd.concat(event_frames,ignore_index=True),
        slot_daily=slot_daily,slot_summary=pd.concat(slot_reports,ignore_index=True),
        final_positions=pd.DataFrame(final_positions),positions=pd.DataFrame(positions),
        audit=audit,config=config,**tables)
    result['output_directory']=dump_results(result,config)
    return result
