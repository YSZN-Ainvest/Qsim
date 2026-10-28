import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np


def plot_results(result,directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    curve,diag,summary=result['daily'],result['diagnostics'],result['summary']
    selected=summary.loc[summary.book.eq('LS')&summary.period.eq('ALL')].iloc[0]
    fig,axes=plt.subplots(2,2,figsize=(17,11),layout='constrained')
    for name in ['LS','long','short']:
        frame=curve.loc[curve.book.eq(name)]
        axes[0,0].plot(frame.date,frame.equity/frame.previous_equity.iloc[0],label=name)
    axes[0,0].set_title('Net equity (costs included)');axes[0,0].legend()
    ls=curve.loc[curve.book.eq('LS')];net=ls.equity/ls.previous_equity.iloc[0]
    axes[0,1].fill_between(ls.date,net/np.maximum.accumulate(np.r_[1.,net])[1:]-1,0,alpha=.5)
    axes[0,1].set_title('Long-short drawdown')
    for name,frame in curve.loc[curve.book.str.startswith('G')].groupby('book'):
        axes[1,0].plot(frame.date,frame.equity/frame.previous_equity.iloc[0],label=name)
    axes[1,0].set_title('Group portfolios (G01 = low factor)');axes[1,0].legend(ncol=5,fontsize=7)
    daily_ic=diag.groupby('date')[['ic','rank_ic']].mean()
    axes[1,1].plot(daily_ic.index,daily_ic.ic,label='IC')
    axes[1,1].plot(daily_ic.index,daily_ic.rank_ic,label='Rank IC')
    axes[1,1].axhline(0,color='gray',lw=.5);axes[1,1].legend();axes[1,1].set_title('Daily average factor IC')
    keys=['from','to','long','short','return','tvr','sharp','dd','dd_start','dd_end','IC','ICIR',
          'rank_IC','winrate','auto_corr','Tstat','AHD','TC']
    cells=[f'{k}: {selected[k]:.4g}' if isinstance(selected[k],(float,np.floating)) else f'{k}: {selected[k]}' for k in keys]
    caption='\n'.join('  |  '.join(cells[i:i+6]) for i in range(0,len(cells),6))
    st_note = ', ST unknowns allowed' if result['config']['operations']['exclude_st'] and result['config']['operations']['st_unknown']=='allow' else ''
    cfg=result['config']
    title=f"QSim — {cfg['backtest']['mode']}, {cfg['stats']['priceMode']}, synthetic long/short"
    title+=', T+1' if cfg['execution']['enforce_t1'] else ', T+1 disabled'
    if cfg['backtest']['mode']=='StatsEvery':
        title+=' (independent timings: equal-capital comparison)'
    fig.suptitle(title+st_note+'\n'+caption,fontsize=9)
    for ax in axes.flat:
        ax.grid(alpha=.2);ax.xaxis.set_major_locator(plt.MaxNLocator(6));ax.tick_params(axis='x',rotation=20)
    fig.savefig(directory/'dashboard.png',dpi=160);plt.close(fig)
    fig,axes=plt.subplots(1,2,figsize=(12,4),layout='constrained')
    groups=summary.loc[summary.period.eq('ALL')&summary.book.str.startswith('G')]
    axes[0].bar(groups.book,groups['return']);axes[0].set_title('Group net cumulative returns')
    axes[1].plot(diag.timestamp,diag.ic.fillna(0).cumsum(),label='Cumulative IC')
    axes[1].plot(diag.timestamp,diag.rank_ic.fillna(0).cumsum(),label='Cumulative rank IC')
    axes[1].legend();axes[1].tick_params(axis='x',rotation=20)
    fig.savefig(directory/'factor_analysis.png',dpi=160);plt.close(fig)
    if cfg['backtest']['mode']=='StatsEvery' and 'slot_daily' in result:
        fig,axes=plt.subplots(1,2,figsize=(15,5),layout='constrained')
        for slot,part in result['slot_daily'].query('book == "LS"').groupby('slot'):
            axes[0].plot(part.date,part.net,label=slot)
        slots=result['slot_summary'].query('book == "LS" and period == "ALL"')
        axes[1].bar(slots.slot,slots['return'])
        axes[0].set_title('Separate daily fixed-time portfolios (net equity)')
        axes[1].set_title('Fixed signal time versus total net return')
        if len(slots)<=12:
            axes[0].legend(ncol=3,fontsize=8)
        for ax in axes:
            ax.xaxis.set_major_locator(plt.MaxNLocator(8));ax.tick_params(axis='x',rotation=35)
        fig.savefig(directory/'timing_comparison.png',dpi=160);plt.close(fig)


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
        (directory/'config.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
        (directory/'audit.json').write_text(json.dumps(result['audit'],ensure_ascii=False,indent=2,default=str))
        if o['plots']:
            plot_results(result,directory)
        (directory/'COMPLETE').write_text('All artifacts written successfully.\n')
    except Exception:
        (directory/'INCOMPLETE').write_text('Output generation failed; this directory is not a complete result.\n')
        raise
    return directory
