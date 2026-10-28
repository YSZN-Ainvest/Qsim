import numpy as np
import pandas as pd


def correlation(x,y,rank=False):
    valid = np.isfinite(x)&np.isfinite(y)
    if valid.sum()<3:
        return np.nan
    x,y=x[valid],y[valid]
    if rank:
        x=pd.Series(x).rank().to_numpy();y=pd.Series(y).rank().to_numpy()
    if np.std(x)<1e-12 or np.std(y)<1e-12:
        return np.nan
    return float(np.corrcoef(x,y)[0,1])


def hac_tstat(values,lag):
    x=np.asarray(values,dtype=float); x=x[np.isfinite(x)]
    n=len(x)
    if n<3:
        return np.nan
    z=x-x.mean(); variance=np.dot(z,z)/n
    lag=min(lag,n-1)
    for k in range(1,lag+1):
        variance+=2*(1-k/(lag+1))*np.dot(z[k:],z[:-k])/n
    return x.mean()/np.sqrt(variance/n) if variance>1e-20 else np.nan


def daily_curve(events):
    frame=events.copy()
    frame['date']=pd.to_datetime(frame.timestamp).dt.strftime('%Y-%m-%d')
    rows=[]
    for (book,date),part in frame.groupby(['book','date'],sort=True):
        last=part.iloc[-1]
        row={k:last[k] for k in ('equity','long','short','nHold')}
        row.update(book=book,date=date,previous_equity=part.previous_equity.iloc[0])
        for k in ('pnl','hold_pnl','trade_pnl','tradecost','turnover','net_turnover','exit_notional','age_notional'):
            row[k]=part[k].sum()
        row['return']=row['pnl']/row['previous_equity']
        row['tvr']=row['turnover']/row['previous_equity']
        row['net_tvr']=row['net_turnover']/row['previous_equity']
        rows.append(row)
    result = pd.DataFrame(rows)
    result['net'] = result.equity / result.groupby('book').previous_equity.transform('first')
    return result


def report_metrics(curve,diagnostics,config):
    rows=[]; boundary=config['backtest']['os_start']
    for book,full in curve.groupby('book',sort=False):
        full=full.sort_values('date')
        periods=[(str(y),full.loc[full.date.str[:4].eq(y)]) for y in full.date.str[:4].unique()]
        periods += [('IS',full.loc[full.date<boundary]),('OS',full.loc[full.date>=boundary]),('ALL',full)]
        for period,part in periods:
            if part.empty:
                continue
            ret=part['return'].to_numpy(); start=float(part.previous_equity.iloc[0])
            nav=part.equity.to_numpy()/start
            peaks=np.maximum.accumulate(np.r_[1.,nav]); draw=np.r_[1.,nav]/peaks-1
            trough=int(np.argmin(draw)); peak=int(np.argmax(np.r_[1.,nav][:trough+1]))
            dstart=part.date.iloc[max(0,peak-1)] if peak else 'initial'
            dend=part.date.iloc[max(0,trough-1)]
            std=np.std(ret,ddof=1) if len(ret)>1 else np.nan
            diag=diagnostics.loc[diagnostics.date.between(part.date.iloc[0],part.date.iloc[-1])]
            ic=diag.ic.dropna(); ric=diag.rank_ic.dropna()
            turnover=part.turnover.sum()
            row={'period':period,'book':book,'from':part.date.iloc[0],'to':part.date.iloc[-1],
                 'long':(part.long/part.equity).mean(),'short':(part.short/part.equity).mean(),
                 'return':nav[-1]-1,'annual_return':nav[-1]**(252/len(part))-1 if nav[-1]>0 else np.nan,
                 'tvr':part.tvr.mean(),'net_tvr':part.net_tvr.mean(),
                 'sharp':np.mean(ret)/std*np.sqrt(252) if std>0 else np.nan,
                 'dd':-draw.min(),'dd_start':dstart,'dd_end':dend,
                 'bp_mrgn':part.pnl.sum()/turnover*10000 if turnover else np.nan,
                 'winrate':float(np.mean(ret>0)),'nHold':part.nHold.mean(),
                 'traderet':part.trade_pnl.sum()/start,'holdret':part.hold_pnl.sum()/start,
                 'tradecost':part.tradecost.sum()/start,'tradecost_cash':part.tradecost.sum(),
                 'avgdd':-draw[1:].mean(),'net':nav[-1],'pnl':part.pnl.sum(),
                 'IC':ic.mean(),'ICIR':ic.mean()/ic.std() if len(ic)>1 and ic.std()>0 else np.nan,
                 'rank_IC':ric.mean(),'rank_ICIR':ric.mean()/ric.std() if len(ric)>1 and ric.std()>0 else np.nan,
                 'auto_corr':diag.auto_corr.mean(),'Tstat':hac_tstat(ic,
                     ((config['stats']['ileadEach'] if config['backtest']['mode']!='Cohort' else None)
                      or config['backtest']['forecast_period'])-1),
                 'AHD':part.age_notional.sum()/part.exit_notional.sum() if part.exit_notional.sum()>0 else np.nan,
                 'TC':diag.TC.mean(),'ic_observations':len(ic),'trading_days':len(part)}
            rows.append(row)
    return pd.DataFrame(rows)
