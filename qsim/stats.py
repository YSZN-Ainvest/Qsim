"""Explicit research-statistics conventions, separate from executable cash PnL.

No proprietary ksim/zsim implementation was available. See STATS.md for choices.
"""
import numpy as np
import pandas as pd
from .metrics import correlation


def portfolio_weights(groups,count):
    w = np.zeros((count+2,len(groups)))
    for g in range(count):
        mask = groups==g
        if mask.any():
            w[g,mask] = 1/mask.sum()
    w[-2] = w[count-1];w[-1] = -w[0]
    return w


def simple_return(end,start):
    return np.divide(end,start,out=np.full_like(np.asarray(end,dtype=float),np.nan),
                     where=np.isfinite(start)&(start>0)&np.isfinite(end)&(end>0))-1


def contribution(weights,returns):
    # A missing price on an active holding invalidates the statistic, never becomes zero PnL.
    active = np.abs(weights)>1e-14
    bad = (active&~np.isfinite(returns)[None,:]).any(axis=1)
    values = np.sum(weights*np.nan_to_num(returns,nan=0.),axis=1)
    values[bad] = np.nan
    return values


def every_pnl(previous_alpha,alpha,previous_close,trade_price,close):
    overnight = contribution(previous_alpha,simple_return(trade_price,previous_close))
    intraday = contribution(alpha,simple_return(close,trade_price))
    return overnight,intraday


def interval_pnl(last_alpha,alpha,previous_close,close,fill,price_mode='vwap'):
    if price_mode=='open':
        return np.zeros(len(alpha)),contribution(alpha,simple_return(close,fill))
    hold = contribution(last_alpha,simple_return(close,previous_close))
    trade = contribution(alpha-last_alpha,simple_return(close,fill))
    return hold,trade


def aggregate_window(day,indices,price_mode='vwap'):
    """Non-overlapping consecutive *effective* slots; amount/volume weighted VWAP."""
    bars = [day.bar(i) for i in indices]
    volumes = np.array([b['volume'] for b in bars])
    amount = np.array([b['total_turnover'] for b in bars])
    valid = (np.isfinite(volumes)&(volumes>=0)&np.isfinite(amount)&(amount>=0)).all(axis=0)
    complete = np.array([b['bar_complete']==1 for b in bars]).all(axis=0)&valid
    vol = volumes.sum(axis=0);amt = amount.sum(axis=0)
    raw = np.divide(amt,vol,out=np.full_like(vol,np.nan),where=complete&(vol>0))
    adjusted = raw*day.adjustment
    if price_mode=='open':
        adjusted = bars[0]['open_adj'].copy()
    elif price_mode=='close':
        adjusted = bars[-1]['close_adj'].copy()
    if price_mode!='vwap':
        raw = np.divide(adjusted,day.adjustment,out=np.full_like(adjusted,np.nan),where=day.adjustment>0)
    return dict(vwap_raw=raw,vwap_adj=adjusted,volume=vol,total_turnover=amt,
                open_adj=bars[0]['open_adj'],close_adj=bars[-1]['close_adj'],
                bar_complete=complete.astype(float),
                vwap_outside_ohlc=np.array([b['vwap_outside_ohlc']!=0 for b in bars]).any(axis=0).astype(float))


class StatsEvery:
    def __init__(self,config,symbols):
        self.config=config;self.n=len(symbols);self.previous={};self.rows=[]
        self.names=[f'G{i+1:02}' for i in range(config['backtest']['groups'])]+['long','short']

    def observe(self,date,slot,alpha,previous_close,fill,close,**kwargs):
        old=self.previous.get(slot,np.zeros_like(alpha))
        hold,trade=every_pnl(old,alpha,previous_close,fill,close)
        tvr=np.sum(np.abs(alpha-old),axis=1)  # Both sides, no /2, gross weight=1 per leg.
        self.previous[slot]=alpha.copy()
        rows=self._rows(date,slot,alpha,hold,trade,tvr,1,old_alpha=old,**kwargs)
        self.rows.extend(rows)

    def _rows(self,date,slot,alpha,hold,trade,tvr,divisor,**extra):
        e=self.config['execution']
        old=extra.pop('old_alpha',None)
        if old is None:
            # The caller provides the separately computed directional sell turnover below.
            sell_tvr=extra.pop('sell_tvr',np.full(len(alpha),np.nan))
        else:
            sell_tvr=np.maximum(old-alpha,0).sum(axis=1)
        fee=tvr*(e['fee_bps']+e['slippage_bps'])/10000
        if e['sell_tax_bps']:
            fee=fee+sell_tvr*e['sell_tax_bps']/10000
        rows=[]
        for j,name in enumerate(self.names):
            rows.append(dict(date=date,slot=slot,book=name,hold_return=hold[j],trade_return=trade[j],
                raw_return=hold[j]+trade[j],cost_return=fee[j],
                reported_return=(hold[j]+trade[j]-fee[j])/divisor,
                tvr=tvr[j],long=float(np.maximum(alpha[j],0).sum()),
                short=float(np.maximum(-alpha[j],0).sum()),nbar=divisor,**extra))
        ls={key:(rows[-2][key]+rows[-1][key])/2 for key in
            ['hold_return','trade_return','raw_return','cost_return','reported_return','tvr','long','short']}
        rows.append(dict(date=date,slot=slot,book='LS',nbar=divisor,**ls,**extra))
        return rows

    def tables(self):
        rows=pd.DataFrame(self.rows)
        records=[]
        for (date,book),part in rows.groupby(['date','book']):
            records.append(dict(date=date,book=book,
                # Only TVR is controlled by isTradeT1. There is no implicit PnL/IC switch.
                tvr=part.tvr.mean() if self.config['stats']['isTradeT1'] else part.tvr.sum(),
                slots=len(part),valid_pnl_slots=int(part.reported_return.notna().sum())))
        return {'stats_slots':rows,'stats_tvr':pd.DataFrame(records)}


class StatsInterval(StatsEvery):
    def __init__(self,config,symbols):
        super().__init__(config,symbols)
        self.last_alpha=np.zeros((len(self.names),len(symbols)))
        self.last_close=None

    def observe(self,date,slot,alpha,previous_close,fill,close,first=False,**extra):
        if first and self.config['stats']['cleareod']:
            self.last_alpha.fill(0)  # Statistical state only; never a cash liquidation.
        old=self.last_alpha.copy()
        prev=previous_close if self.last_close is None else self.last_close
        hold,trade=interval_pnl(old,alpha,prev,close,fill,self.config['stats']['priceMode'])
        tvr=np.abs(alpha-old).sum(axis=1)
        self.rows.extend(self._rows(date,slot,alpha,hold,trade,tvr,self.config['stats']['nbar'],old_alpha=old,**extra))
        self.last_alpha=alpha.copy();self.last_close=close.copy()

    def tables(self):
        rows=pd.DataFrame(self.rows);outputs={'stats_intervals':rows}
        for name,frame in [('stats_sum',rows),('stats_sumnoend',rows.loc[~rows.is_last])]:
            agg=frame.groupby(['date','book'],as_index=False).agg(
                raw_return=('raw_return',lambda x:x.sum(min_count=len(x))),
                reported_return=('reported_return',lambda x:x.sum(min_count=len(x))),
                cost_return=('cost_return',lambda x:x.sum(min_count=len(x))),
                tvr=('tvr','sum'),long=('long','mean'),short=('short','mean'),
                intervals=('slot','size'))
            outputs[name]=agg
        return outputs
