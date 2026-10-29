"""Day-bounded backtest orchestration; production factor and ledger logic reused."""
from copy import deepcopy
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .alpha_events import decision_time,eligible_at,signal_plan
from .alpha_replay import code_version
from .data import MarketData
from .engine import Ledger
from .factors import FactorModel
from .input_policy import file_hash
from .metrics import correlation,daily_curve
from .online_metrics import OnlineMetrics
from .operations import groups_from_score
from .run_store import RunStore,write_json
from .stats import StatsEvery,StatsInterval,portfolio_weights,aggregate_window
from .target_engine import TargetLedger


def run_stream(config,market_class=MarketData,model_class=FactorModel):
    store=RunStore(config);model=None
    try:
        result=_run(config,store,market_class,model_class)
        store.finish(result)
        if config['output']['materialize']:
            from .run_store import DiskTable
            for key,value in list(result.items()):
                if isinstance(value,DiskTable): result[key]=value.to_pandas()
        return result
    except Exception:
        if not store.log_json.closed: store.fail()
        raise


def _run(config,store,market_class,model_class):
    b,s,o=config['backtest'],config['stats'],config['operations']
    cohort=b['mode']=='Cohort';every=b['mode']=='StatsEvery'
    with store.stage('initialize'):
        market=market_class(config);symbols=market.symbols;n=len(symbols)
        model=model_class(config,symbols)
    try:
        return _simulate(config,store,market,model,symbols,cohort,every)
    finally:
        model.close()


def _simulate(config,store,market,model,symbols,cohort,every):
    b,s,o=config['backtest'],config['stats'],config['operations'];n=len(symbols)
    st=model.registry.get(o['st_path'])
    universe=model.registry.get(config['universe']['path']) if config['universe']['kind']=='table' else None
    metrics=OnlineMetrics(config)
    calculator=None if cohort else (StatsEvery if every else StatsInterval)(config,symbols)
    ledgers={};benchmarks={};bench_previous={};prior_scores={}
    benchmark_config=deepcopy(config);benchmark_config['backtest']['groups']=2
    previous_close=np.full(n,np.nan);warmup_bars=0
    version=code_version();catalog=Path(config['data']['root'])/'catalog.json'
    store.log('INFO','versions',code_sha256=version,catalog_sha256=file_hash(catalog) if catalog.exists() else None,
              pit_sources={p:v.version for p,v in model.registry.sources.items()},
              requested_factor_pools=config['compute']['pools'],effective_factor_pools=model.effective_pools,
              resource_budget=model.resource_budget,expiry_policy='carry_forward',expired_count=None)
    if o['exclude_st'] and o['st_unknown']=='allow':
        store.log('WARNING','unknown_st_allowed',reason='Historical ST unknowns are explicitly allowed')
    for date in market.warmup_dates:
        with store.stage('read'):
            day=market.load_day(date)
        for j in range(len(day.calendar)): model.observe(day.bar(j)['close_adj']);warmup_bars+=1
        previous_close=day.eod_close.copy()
    if warmup_bars<model.minimum_history:
        store.log('WARNING','short_warmup',observed=warmup_bars,required=model.minimum_history)
    pending=[];index=0;unknown_st=0;no_signal=0;skipped_delay=0;prior_date=None
    horizon=(s['ileadEach'] or b['forecast_period']) if not cohort else b['forecast_period']
    needed={};ic_days={};finished=[]
    first_equity={}

    def finish_diag(item):
        diag=item['diag'];metrics.add_diagnostic(diag);finished.append(diag)
        if np.isfinite(diag['ic']) and np.isfinite(diag['rank_ic']):
            store.validations['valid_ic_days'].add(diag['date'])
        if not cohort and not every:
            date=diag['date']
            state=ic_days.setdefault(date,dict(count=0,all=[0.,0,0,0],noend=[0.,0,0,0]))
            state['count']+=1
            for key in ['all']+([] if diag['is_last'] else ['noend']):
                a=state[key];a[3]+=1
                if np.isfinite(diag['rank_ic']): a[0]+=diag['rank_ic'];a[1]+=1
                a[2]+=diag['ic_undefined']
            if state['count']==needed[date]:
                for key,name in [('all','stats_ic_sum'),('noend','stats_ic_sumnoend')]:
                    a=state[key]
                    if a[3]: store.add(name,pd.DataFrame([dict(date=date,rank_ic=a[0]/a[1] if a[1] else np.nan,valid_labels=a[1],undefined_to_zero=a[2])]))
                del ic_days[date];del needed[date]

    for day_index,date in enumerate(market.run_dates):
        store.current=dict(date=date)
        with store.stage('read'): day=market.load_day(date)
        cal=day.calendar;clocks,plan,skipped=signal_plan(cal,market.freq,config);skipped_delay+=skipped
        if not cohort and not every: needed[date]=len(plan)
        for j in plan:
            slot='cohort' if cohort else clocks[j] if every else 'interval'
            if slot not in ledgers:
                ledgers[slot]=(Ledger if cohort else TargetLedger)(config,symbols)
                benchmarks[slot]=(Ledger if cohort else TargetLedger)(benchmark_config,symbols)
        executions={};first=True;day_signal_counts=[];day_coverages=[]
        for j,row in cal.iterrows():
            when=decision_time(date,row,market.freq);data=day.bar(j)
            store.current=dict(date=date,decision_time=str(when),bar=j)
            if j in plan:
                with store.stage('alpha'):
                    eligible,unknown=eligible_at(day,when,config,st,universe);unknown_st+=unknown
                    score=model.compute(when,eligible)
                    groups=groups_from_score(score,b['groups'])
                store.factors(when,symbols,model)
                count=int(np.isfinite(score).sum());day_signal_counts.append(count)
                day_coverages.append(count/max(1,int(eligible.sum())))
                minimum=store.validations['min_signal_count']
                store.validations['min_signal_count']=count if minimum is None else min(minimum,count)
                fire=bool(np.any(groups>=0));no_signal+=int(not fire)
                store.validations['no_signal_bars']=no_signal
                slot='cohort' if cohort else clocks[j] if every else 'interval'
                execution_index=j if cohort else plan[j][-1]
                diag=dict(timestamp=when,date=date,slot=slot,ic=np.nan,rank_ic=np.nan,label_count=0,label_end=None,
                    label_status='pending',auto_corr=correlation(score,prior_scores.get(slot,np.full(n,np.nan))),
                    TC=np.nan,signal_count=count,is_last=execution_index==len(cal)-1,ic_undefined=False)
                item=dict(entry_index=index+(s['idelayEach'] if not cohort else 0),
                    due=index+(s['idelayEach'] if not cohort else 0)+horizon,entry=None,score=score.copy(),diag=diag,
                    executed=False,label_done=False,groups=groups.copy())
                pending.append(item);prior_scores[slot]=score.copy()
                if cohort:
                    alpha=None
                else:
                    alpha=portfolio_weights(groups,b['groups'])
                    if not fire:
                        alpha=calculator.previous.get(slot,np.zeros_like(alpha)).copy() if every else calculator.last_alpha.copy()
                        alpha[:,~eligible]=0
                bench=np.zeros((4,n));mask=groups>=0
                if mask.any(): bench[0,mask]=1/mask.sum()
                else:
                    bench=bench_previous.get(slot,bench).copy();bench[:,~eligible]=0
                bench_previous[slot]=bench.copy()
                executions[execution_index]=dict(slot=slot,alpha=alpha,bench=bench,reference=model.reference_price(),
                    fire=fire,eligible=eligible,indices=plan[j],signal_index=j,first=first,item=item)
                first=False
            if j in executions:
                execution=executions.pop(j);slot=execution['slot'];ledger=ledgers[slot];bench=benchmarks[slot]
                item=execution['item'];ref=execution['reference'];eligible=execution['eligible']
                with store.stage('execution'):
                    if cohort:
                        ledger.step(index,day_index,row.bar_end,data,day,item['groups'],eligible,ref)
                        bench_groups=np.where(item['groups']>=0,0,-1)
                        bench.step(index,day_index,row.bar_end,data,day,bench_groups,eligible,ref)
                    else:
                        fill_data=data if every else aggregate_window(day,execution['indices'],s['priceMode'])
                        fire=execution['fire'] or bool(np.any((np.abs(ledger.q)>1e-10)&~eligible[None,:]))
                        ledger.rebalance(day_index,row.bar_end,fill_data,day,execution['alpha'],ref,fire)
                        bench_fire=execution['fire'] or bool(np.any((np.abs(bench.q)>1e-10)&~eligible[None,:]))
                        bench.rebalance(day_index,row.bar_end,fill_data,day,execution['bench'],ref,bench_fire)
                        price=np.where(fill_data['bar_complete']==1,fill_data['vwap_adj'],np.nan)
                        if config['execution']['reject_bad_vwap']: price=np.where(fill_data['vwap_outside_ohlc']==0,price,np.nan)
                        extra=dict(signal_time=clocks[execution['signal_index']],execution_end=str(row.bar_end),
                            actual_bars=len(execution['indices']),is_last=j==len(cal)-1)
                        if every: calculator.observe(date,slot,execution['alpha'],previous_close,price,day.eod_close,**extra)
                        else: calculator.observe(date,clocks[execution['signal_index']],execution['alpha'],previous_close,
                            price,fill_data['close_adj'],first=execution['first'],**extra)
                    actual=(ledger.q[-2]+ledger.q[-1])*np.nan_to_num(ledger.last)
                    item['diag']['TC']=correlation(item['score'],actual);item['executed']=True
            label=np.where((data['bar_complete']==1)&(data['vwap_adj']>0),data['vwap_adj'],np.nan)
            if config['execution']['reject_bad_vwap']: label=np.where(data['vwap_outside_ohlc']==0,label,np.nan)
            remaining=[]
            for item in pending:
                if item['entry_index']==index: item['entry']=label.copy()
                if not item['label_done'] and item['due']==index:
                    target=np.divide(label,item['entry'],out=np.full(n,np.nan),where=item['entry']>0)-1
                    diag=item['diag'];diag['ic']=correlation(item['score'],target);rank=correlation(item['score'],target,True)
                    diag['ic_undefined']=bool(not np.isfinite(rank))
                    diag['rank_ic']=rank if every or cohort or np.isfinite(rank) else 0.
                    diag['label_count']=int(np.count_nonzero(np.isfinite(target)&np.isfinite(item['score'])))
                    diag['label_end']=str(row.bar_end);diag['label_status']='complete';item['label_done']=True
                    if cohort:
                        for group in range(b['groups']):
                            values=target[(item['groups']==group)&np.isfinite(target)]
                            diag[f'G{group+1:02}_forecast_return']=float(np.mean(values)) if len(values) else np.nan
                if item['label_done'] and item['executed']: finish_diag(item)
                else: remaining.append(item)
            pending=remaining;store.max_pending_labels=max(store.max_pending_labels,len(pending))
            model.observe(data['close_adj']);index+=1
        elapsed=(pd.Timestamp(date)-pd.Timestamp(prior_date)).days if prior_date else 1
        slot_curves=[];benchmark_curves=[]
        for slot,ledger in ledgers.items():
            bench=benchmarks[slot]
            ledger.end_day(pd.Timestamp(date+' 15:00',tz='Asia/Shanghai'),day_index,day.eod_close,elapsed)
            bench.end_day(pd.Timestamp(date+' 15:00',tz='Asia/Shanghai'),day_index,day.eod_close,elapsed)
            store.max_buffered_events=max(store.max_buffered_events,
                sum(len(l.records) for l in ledgers.values())+sum(len(l.records) for l in benchmarks.values()))
            events=pd.DataFrame(ledger.records)
            error=float((events.pnl-events.hold_pnl-events.trade_pnl+events.tradecost).abs().max())
            store.validations['max_accounting_error']=max(store.validations['max_accounting_error'],error)
            if error>1e-5: raise AssertionError('Streamed account does not reconcile')
            curve=daily_curve(events)
            for book,part in curve.groupby('book'):
                first_equity.setdefault((slot,book),float(part.previous_equity.iloc[0]))
                curve.loc[part.index,'net']=part.equity/first_equity[(slot,book)]
            metrics.add_daily(curve,slot)
            slot_curves.append(curve.assign(slot=slot))
            store.add('events',events.assign(slot=slot))
            bench_events=pd.DataFrame(bench.records).query('book == "G01"').copy();bench_events['book']='benchmark'
            benchmark_curves.append(daily_curve(bench_events).assign(slot=slot))
            if config['output']['positions']:
                rows=[dict(date=date,slot=slot,**r) for r in ledger.positions()]
                store.add('positions',pd.DataFrame(rows))
            ledger.records.clear();bench.records.clear()
        slot_daily=pd.concat(slot_curves,ignore_index=True)
        store.add('slot_daily',slot_daily)
        numeric=slot_daily.select_dtypes(include='number').columns.tolist()
        curve=slot_daily.groupby(['date','book'],as_index=False)[numeric].mean()
        curve['return']=curve.pnl/curve.previous_equity;curve['tvr']=curve.turnover/curve.previous_equity
        curve['net_tvr']=curve.net_turnover/curve.previous_equity
        for book,part in curve.groupby('book'):
            first_equity.setdefault(('main',book),float(part.previous_equity.iloc[0]))
            curve.loc[part.index,'net']=part.equity/first_equity[('main',book)]
        metrics.add_daily(curve)
        if curve.loc[curve.book.eq('LS'),'turnover'].gt(0).any(): store.validations['traded_days'].add(date)
        store.add('daily',curve)
        bench_daily=pd.concat(benchmark_curves,ignore_index=True)
        bench_daily=bench_daily.groupby(['date','book'],as_index=False)[bench_daily.select_dtypes(include='number').columns].mean()
        bench_daily['return']=bench_daily.pnl/bench_daily.previous_equity;bench_daily['net']=bench_daily.equity/b['capital']
        store.add('benchmark_daily',bench_daily)
        if calculator is not None:
            for name,frame in calculator.tables().items(): store.add(name,frame)
            calculator.rows.clear()
        if finished: store.add('diagnostics',pd.DataFrame(finished));finished.clear()
        store.flush_factors()
        store.progress(date,day_index+1,len(market.run_dates),signals=len(day_signal_counts),
            valid_factor_count_min=min(day_signal_counts) if day_signal_counts else 0,
            factor_coverage_min=min(day_coverages) if day_coverages else 0.,expired_count=None,
            execution={slot:ledger.audit for slot,ledger in ledgers.items()},
            rejection_samples={slot:ledger.audit_samples for slot,ledger in ledgers.items()},
            input_sources=getattr(market,'last_sources',[]))
        for ledger in list(ledgers.values())+list(benchmarks.values()): ledger.audit_samples.clear()
        previous_close=day.eod_close.copy();prior_date=date
    for item in pending:
        item['diag']['label_status']='right_censored';finish_diag(item)
    if finished: store.add('diagnostics',pd.DataFrame(finished));finished.clear()
    if not every and not cohort:
        for name in ('stats_ic_sum','stats_ic_sumnoend'):
            store.add(name,pd.DataFrame(columns=['date','rank_ic','valid_labels','undefined_to_zero']))
    summary,slot_summary=metrics.tables()
    store.add('summary',summary);store.add('slot_summary',slot_summary)
    for slot,ledger in ledgers.items():
        store.add('final_positions',pd.DataFrame([dict(slot=slot,**r) for r in ledger.positions()],
            columns=['slot','book','order_book_id','adjusted_units','mark_adj','market_value']))
    audit=dict(schema_version=3,status='complete',mode=b['mode'],frequency=market.freq,schedule=b['frequency'],
        symbols=n,days=len(market.run_dates),bars=index,warmup_days=len(market.warmup_dates),portfolio_count=len(ledgers),
        summary_basis='comparison_mean' if every else 'single_cohort_portfolio' if cohort else 'single_interval_portfolio',
        forecast_right_censored=len(pending),no_signal_bars=no_signal,skipped_delay_signals=skipped_delay,
        st_unknown_security_bars=unknown_st,execution={slot:ledger.audit for slot,ledger in ledgers.items()},
        code_sha256=version,elapsed_seconds=time.monotonic()-store.started,
        benchmark='Equal-weight finite-score eligible universe, same execution windows/costs; independent account.',
        output_layout='partitioned_parquet; large result tables are lazy',
        validation={**store.validations,'valid_ic_days':len(store.validations['valid_ic_days']),
                    'traded_days':len(store.validations['traded_days'])},
        notes=['Synthetic shorts and adjusted fractional units; no order-queue simulation.',
               'Daily universe/status publication times are not independently certified.',
               'Unknown ST is allowed.' if o['exclude_st'] and o['st_unknown']=='allow' else 'Configured ST policy applied.',
               'No research IS/OS gate or date split is imposed.'])
    from .alpha_replay import fingerprint
    audit['config_sha256']=fingerprint(config)
    if catalog.exists(): audit['cache_catalog_sha256']=file_hash(catalog)
    result=store.tables();result.update(summary=summary,slot_summary=slot_summary,audit=audit,config=config,output_directory=store.directory)
    if config['output']['plots']:
        from .report import plot_results
        with store.stage('plot'): plot_results(result,store.directory)
    return result
