"""Separate PNG figures and bounded reads of persisted summaries."""
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd


def sampled(frame,limit=2000,books=1,columns=None):
    if isinstance(frame,pd.DataFrame):
        if columns is not None and not frame.empty: frame=frame[columns]
        iterator=[frame];count=len(frame)
    else:
        iterator=(batch.to_pandas() for batch in frame.iter_batches(columns=columns));count=len(frame)
    stride=max(1,count//max(1,limit*books))
    counters={};selected={};last={}
    for chunk in iterator:
        for row in chunk.to_dict('records'):
            key=row.get('book','all');index=counters.get(key,0);counters[key]=index+1
            if index%stride==0: selected.setdefault(key,[]).append(row)
            last[key]=row
    for key,row in last.items():
        rows=selected.setdefault(key,[])
        if not rows or rows[-1].get('date',rows[-1].get('timestamp'))!=row.get('date',row.get('timestamp')):
            rows.append(row)
    return pd.DataFrame([r for rows in selected.values() for r in rows])


def plot_results(result,directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    from matplotlib.ticker import PercentFormatter, FuncFormatter, LogLocator, NullFormatter
    config=result['config'];limit=config['output']['plot_points']
    summary=result['summary'];groups=summary.loc[summary.period.eq('ALL')&summary.book.str.startswith('G')].sort_values('book')
    curve=sampled(result['daily'],limit,config['backtest']['groups']+3,
                  columns=['book','date','net','equity','previous_equity','tvr']).sort_values(['book','date'])
    curve['date']=pd.to_datetime(curve['date'])
    diag=sampled(result['diagnostics'],limit,columns=['timestamp','ic','rank_ic'])
    caption='Net of configured costs; synthetic shorts; sampled curves when needed'
    def nav_axis(ax,values):
        values=np.asarray(values,dtype=float)
        if np.any(np.isfinite(values)&(values<=0)):
            # Preserve exhausted/negative accounts instead of hiding them on log axes.
            ax.set_yscale('symlog',linthresh=.01)
            ax.set_ylabel('NAV (symlog; nonpositive values present)')
        else:
            ax.set_yscale('log')
            ax.yaxis.set_major_locator(LogLocator(base=10,subs=(1,2,5)))
            ax.yaxis.set_minor_formatter(NullFormatter())
            ax.set_ylabel('NAV (log scale)')
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value,pos:f'{value:g}'))
        ax.axhline(1,color='#64748b',lw=.8,ls='--',alpha=.7)
    def group_bars(ax,values):
        values=np.asarray(values,dtype=float)
        ax.set_yscale('symlog',linthresh=.01,linscale=1)
        bars=ax.bar(np.arange(len(groups)),values,width=.72)
        ax.axhline(0,color='#475569',lw=.9)
        ax.set_ylabel('Return (symlog; linear within ±1%)')
        ax.margins(y=.18)
        for bar,value in zip(bars,values):
            if np.isfinite(value):
                ax.annotate(f'{value:+.1%}',(bar.get_x()+bar.get_width()/2,value),
                            xytext=(0,5 if value>=0 else -5),textcoords='offset points',
                            ha='center',va='bottom' if value>=0 else 'top',fontsize=8)
    def save(name,title,draw):
        fig,ax=plt.subplots(figsize=(12,5),layout='constrained')
        draw(ax);ax.set_title(title+'\n'+caption,fontsize=10);ax.grid(alpha=.2)
        ax.tick_params(axis='x',rotation=25)
        if name in ('group_returns.png','group_excess_returns.png'):
            ax.set_xticks(np.arange(len(groups)),groups.book.tolist())
        else:
            locator=mdates.AutoDateLocator(minticks=3,maxticks=8,tz=ZoneInfo('Asia/Shanghai'))
            ax.xaxis.set_major_locator(locator)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator,tz=ZoneInfo('Asia/Shanghai')))
        if name in ('group_returns.png','group_excess_returns.png','turnover.png'):
            ax.yaxis.set_major_formatter(PercentFormatter(1.))
        fig.savefig(directory/name,dpi=150);plt.close(fig)
    ls=curve.loc[curve.book.eq('LS')]
    def ls_line(ax):
        ax.plot(ls.date,ls.net)
        nav_axis(ax,ls.net)
    save('long_short_nav.png','Long-short NAV (initial = 1)',ls_line)
    initial=float(ls.previous_equity.iloc[0])
    save('long_short_pnl.png','Long-short cumulative PnL (CNY)',lambda ax:ax.plot(ls.date,ls.equity-initial))
    def group_lines(ax):
        for name,part in curve.loc[curve.book.str.startswith('G')].groupby('book'):
            ax.plot(part.date,part.net,label=name)
        nav_axis(ax,curve.loc[curve.book.str.startswith('G'),'net'])
        ax.legend(ncol=5,fontsize=8,loc='upper center',bbox_to_anchor=(.5,-.15),frameon=False)
    save('group_nav.png','Group NAV (G01 = lowest score)',group_lines)
    save('group_returns.png','Group cumulative net returns (factor-rank order)',lambda ax:group_bars(ax,groups['return']))
    def excess(ax):
        if 'benchmark_daily' in result:
            benchmark=sampled(result['benchmark_daily'],limit,columns=['book','date','net']).sort_values('date')
            group_bars(ax,groups.net/float(benchmark.net.iloc[-1])-1)
        else:
            ax.text(.05,.5,'Benchmark unavailable in legacy memory mode',transform=ax.transAxes)
    save('group_excess_returns.png','Relative return: group NAV / equal-weight eligible-universe NAV - 1',excess)
    def ic(ax):
        if len(diag):
            diag.sort_values('timestamp',inplace=True)
            ax.plot(diag.timestamp,diag.ic,label='IC');ax.plot(diag.timestamp,diag.rank_ic,label='Rank IC')
            ax.axhline(0,color='gray',lw=.5);ax.legend()
    save('ic.png','Decision-time IC (missing values remain missing)',ic)
    save('turnover.png','Long-short daily two-sided turnover',lambda ax:ax.plot(ls.date,ls.tvr))


def dump_results(result,config):
    o=config['output']
    if not o['dump']:
        return None
    root=Path(o['directory'])
    stamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
    directory=root/f"{o['name']}_{stamp}"
    directory.mkdir(parents=True,exist_ok=False)
    try:
        for name in ['summary','daily','diagnostics']:
            result[name].to_csv(directory/f'{name}.csv',index=False)
        for name in ['events','daily','diagnostics','final_positions']:
            result[name].to_parquet(directory/f'{name}.parquet',index=False)
        for name,frame in result.items():
            if name.startswith('stats_') or name in ('slot_daily','slot_summary'):
                frame.to_csv(directory/f'{name}.csv',index=False)
                frame.to_parquet(directory/f'{name}.parquet',index=False)
        if o['positions']:
            result['positions'].to_parquet(directory/'positions.parquet',index=False)
        from .run_store import write_json
        write_json(directory/'summary.json',result['summary'].to_dict('records'))
        (directory/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
        (directory/'audit.json').write_text(json.dumps(result['audit'],ensure_ascii=False,indent=2,default=str))
        if o['plots']:
            plot_results(result,directory)
        (directory/'COMPLETE').write_text('All artifacts written successfully.\n')
    except Exception:
        (directory/'INCOMPLETE').write_text('Output generation failed; this directory is not a complete result.\n')
        raise
    return directory
