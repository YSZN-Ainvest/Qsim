"""Constant-state account/diagnostic aggregation (annual and ALL periods)."""
from collections import deque
import math
import numpy as np
import pandas as pd


class Moments:
    def __init__(self,lag=0):
        self.n=0;self.mean=0.;self.m2=0.;self.lag=lag;self.tail=deque(maxlen=lag)
        self.cross=np.zeros((lag,4))

    def add(self,value):
        if not np.isfinite(value): return
        for k,old in enumerate(reversed(self.tail)):
            self.cross[k]+=np.array([old*value,old,value,1.])
        self.tail.append(value)
        self.n+=1;delta=value-self.mean;self.mean+=delta/self.n;self.m2+=delta*(value-self.mean)

    @property
    def average(self): return self.mean if self.n else np.nan
    @property
    def std(self): return math.sqrt(max(0.,self.m2/(self.n-1))) if self.n>1 else np.nan
    @property
    def ratio(self): return self.mean/self.std if self.std>0 else np.nan

    def tstat(self):
        if self.n<3: return np.nan
        lag=min(self.lag,self.n-1);variance=self.m2/self.n
        for k in range(lag):
            xy,left,right,count=self.cross[k]
            covariance=(xy-self.mean*(left+right)+count*self.mean**2)/self.n
            variance+=2*(1-(k+1)/(lag+1))*covariance
        return self.mean/math.sqrt(variance/self.n) if variance>1e-20 else np.nan


class OnlineMetrics:
    def __init__(self,config):
        self.config=config;self.accounts={};self.diagnostics={}
        self.horizon=(config['stats']['ileadEach'] or config['backtest']['forecast_period']) if config['backtest']['mode']!='Cohort' else config['backtest']['forecast_period']

    def add_daily(self,frame,slot='main'):
        for row in frame.to_dict('records'):
            for period in (row['date'][:4],'ALL'):
                key=(slot,row['book'],period)
                if key not in self.accounts:
                    self.accounts[key]=dict(start=row['previous_equity'],first=row['date'],last=row['date'],
                        equity=row['equity'],n=0,returns=Moments(),sums={},peak=1.,peak_date='initial',
                        dd=0.,dd_start='initial',dd_end=row['date'],sum_dd=0.,wins=0)
                a=self.accounts[key];a['n']+=1;a['last']=row['date'];a['equity']=row['equity']
                a['returns'].add(row['return']);a['wins']+=row['return']>0
                for name in ('pnl','hold_pnl','trade_pnl','tradecost','turnover','exit_notional','age_notional','tvr','net_tvr','nHold'):
                    a['sums'][name]=a['sums'].get(name,0.)+row[name]
                for name in ('long','short'):
                    a['sums'][name]=a['sums'].get(name,0.)+row[name]/row['equity']
                nav=row['equity']/a['start']
                if nav>a['peak']: a['peak']=nav;a['peak_date']=row['date']
                draw=1-nav/a['peak'];a['sum_dd']+=draw
                if draw>a['dd']:
                    a['dd']=draw;a['dd_start']=a['peak_date'];a['dd_end']=row['date']

    def add_diagnostic(self,row):
        for slot in set(['main',row.get('slot','main')]):
            for period in (row['date'][:4],'ALL'):
                key=(slot,period)
                d=self.diagnostics.setdefault(key,{name:Moments(self.horizon-1 if name=='ic' else 0)
                    for name in ('ic','rank_ic','auto_corr','TC')})
                for name,value in d.items(): value.add(row.get(name,np.nan))

    def tables(self):
        rows=[]
        for (slot,book,period),a in self.accounts.items():
            d=self.diagnostics.get((slot,period),{name:Moments() for name in ('ic','rank_ic','auto_corr','TC')})
            s=a['sums'];n=a['n'];start=a['start'];nav=a['equity']/start
            rows.append(dict(slot=slot,book=book,period=period,**{'from':a['first'],'to':a['last'],
                'return':nav-1,'annual_return':nav**(252/n)-1 if nav>0 else np.nan,
                'long':s['long']/n,'short':s['short']/n,'tvr':s['tvr']/n,'net_tvr':s['net_tvr']/n,
                'sharp':a['returns'].ratio*np.sqrt(252),'dd':a['dd'],'dd_start':a['dd_start'],'dd_end':a['dd_end'],
                'bp_mrgn':s['pnl']/s['turnover']*10000 if s['turnover'] else np.nan,'winrate':a['wins']/n,
                'nHold':s['nHold']/n,'traderet':s['trade_pnl']/start,'holdret':s['hold_pnl']/start,
                'tradecost':s['tradecost']/start,'tradecost_cash':s['tradecost'],'avgdd':a['sum_dd']/n,
                'net':nav,'pnl':s['pnl'],'IC':d['ic'].average,'ICIR':d['ic'].ratio,
                'rank_IC':d['rank_ic'].average,'rank_ICIR':d['rank_ic'].ratio,
                'auto_corr':d['auto_corr'].average,'Tstat':d['ic'].tstat(),'AHD':s['age_notional']/s['exit_notional'] if s['exit_notional'] else np.nan,
                'TC':d['TC'].average,'ic_observations':d['ic'].n,'trading_days':n}))
        table=pd.DataFrame(rows)
        return table.loc[table.slot.eq('main')].drop(columns='slot').reset_index(drop=True),table.loc[~table.slot.eq('main')].reset_index(drop=True)
