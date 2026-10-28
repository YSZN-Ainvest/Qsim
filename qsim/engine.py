"""Vectorized securities/books, sequential execution time and dated holding cohorts."""
from dataclasses import dataclass
import numpy as np


@dataclass
class Cohort:
    quantity: np.ndarray
    opened: int
    due: int


class Ledger:
    def __init__(self, config, symbols):
        self.cfg, self.symbols = config, symbols
        b = config['backtest']; g = b['groups']
        self.names = [f'G{i+1:02}' for i in range(g)] + ['long','short']
        self.initial = np.array([b['capital']]*g+[b['capital']/2]*2,dtype=float)
        self.cash = self.initial.copy()
        self.q = np.zeros((g+2,len(symbols)))
        self.last = np.full(len(symbols),np.nan)
        self.equity = self.initial.copy()
        self.previous_eod_short = np.zeros(len(self.names))
        self.cohorts = []
        self.records = []
        self.audit = {'t1_blocked_cells':0,'limit_blocked_cells':0,
                      'unfilled_exit_cells':0,'stale_mark_cells':0,'entry_signals':0}

    def _trade(self, delta, fill):
        e = self.cfg['execution']
        notional = np.abs(delta)*fill[None,:]
        costs = notional*((e['fee_bps']+e['slippage_bps'])/10000 + (delta<0)*e['sell_tax_bps']/10000)
        self.cash -= np.sum(delta*fill[None,:]+costs,axis=1)
        self.q += delta
        return costs.sum(axis=1),notional.sum(axis=1)

    def step(self, index, day_index, timestamp, market, day, score_groups, eligible, reference):
        b,e = self.cfg['backtest'],self.cfg['execution']
        oldq, previous_equity = self.q.copy(),self.equity.copy()
        if np.any(previous_equity<=0):
            raise RuntimeError('Portfolio equity exhausted; refusing to continue with leverage')
        fill = market['vwap_adj']
        raw = market['vwap_raw']
        close = market['close_adj']
        valid = np.isfinite(fill)&(fill>0)&np.isfinite(raw)&(raw>0)&(market['volume']>0)&(market['bar_complete']==1)
        if e['reject_bad_vwap']:
            valid &= market['vwap_outside_ohlc']==0
        if e['suspension_filter']:
            valid &= day.suspended==0
        # Status affects new membership; exits remain allowed for ST/universe removals.
        buy,sell = valid.copy(),valid.copy()
        if e['limit_filter']:
            buy &= np.isfinite(day.limit_up)&(day.limit_up>0)&(raw < day.limit_up-0.005)
            sell &= np.isfinite(day.limit_down)&(day.limit_down>0)&(raw > day.limit_down+0.005)
        safe_fill = np.where(valid,fill,0.0)
        capacity = np.divide(market['volume']*e['participation'],day.adjustment,
                             out=np.zeros(len(raw)),where=np.isfinite(day.adjustment)&(day.adjustment>0))
        remaining = np.broadcast_to(capacity,self.q.shape).copy()
        costs = np.zeros(len(self.names)); turnover = costs.copy(); age_dollars = costs.copy(); exit_dollars = costs.copy()
        delta_total = np.zeros_like(self.q)
        for cohort in self.cohorts:
            forced = ~eligible
            due = np.broadcast_to((index >= cohort.due)|forced,self.q.shape)
            requested = np.where(due,-cohort.quantity,0.)
            if self.cfg['execution']['enforce_t1'] and day_index <= cohort.opened:
                self.audit['t1_blocked_cells'] += int(np.count_nonzero(requested))
                continue
            allowed = np.where(requested>0,buy[None,:],sell[None,:])
            self.audit['limit_blocked_cells'] += int(np.count_nonzero((requested!=0)&~allowed&valid[None,:]))
            delta = np.where(allowed,np.sign(requested)*np.minimum(np.abs(requested),remaining),0.)
            self.audit['unfilled_exit_cells'] += int(np.count_nonzero(np.abs(requested-delta)>1e-10))
            cost,turn = self._trade(delta,safe_fill)
            costs += cost; turnover += turn
            exit_dollars += turn; age_dollars += turn*(day_index-cohort.opened)
            cohort.quantity += delta
            remaining -= np.abs(delta); delta_total += delta
        self.cohorts = [c for c in self.cohorts if np.any(np.abs(c.quantity)>1e-10)]
        # Position size is decided using prior observable prices/equity, never this bar's close.
        targets = np.zeros_like(self.q)
        for j in range(b['groups']):
            mask = (score_groups==j)&eligible&np.isfinite(reference)&(reference>0)
            if mask.any():
                targets[j,mask] = previous_equity[j]/b['holding_period']/mask.sum()/reference[mask]
        targets[-2] = targets[b['groups']-1]*(previous_equity[-2]/previous_equity[b['groups']-1])
        targets[-1] = -targets[0]*(previous_equity[-1]/previous_equity[0])
        allowed = np.where(targets>=0,buy[None,:],sell[None,:])
        self.audit['limit_blocked_cells'] += int(np.count_nonzero((targets!=0)&~allowed&valid[None,:]))
        delta = np.where(allowed,np.sign(targets)*np.minimum(np.abs(targets),remaining),0.)
        mark_at_fill = np.where(valid,fill,self.last)
        mark_at_fill = np.where(np.isfinite(mark_at_fill),mark_at_fill,0.)
        equity_at_fill = self.cash+np.sum(self.q*mark_at_fill,axis=1)
        gross = np.sum(np.abs(self.q)*mark_at_fill,axis=1)
        headroom = np.maximum(equity_at_fill-gross,0.)
        requested_value = np.sum(np.abs(delta)*safe_fill,axis=1)
        rate = (e['fee_bps']+e['slippage_bps']+e['sell_tax_bps'])/10000
        scale = np.minimum(1.,headroom/np.maximum(requested_value*(1+rate),1e-30))
        # Long-only books additionally obey their own cash budget. Short proceeds stay segregated.
        scale[:-1] = np.minimum(scale[:-1],np.maximum(self.cash[:-1],0)/np.maximum(requested_value[:-1]*(1+rate),1e-30))
        delta *= scale[:,None]
        cost,turn = self._trade(delta,safe_fill)
        costs += cost; turnover += turn; delta_total += delta
        if np.any(delta):
            self.cohorts.append(Cohort(delta.copy(),day_index,index+b['holding_period']))
            self.audit['entry_signals'] += 1
        mark = np.where(np.isfinite(close)&(close>0),close,self.last)
        self.audit['stale_mark_cells'] += int(np.count_nonzero((np.abs(self.q)>1e-10)&(~np.isfinite(close)|(close<=0))))
        mark = np.where(np.isfinite(mark),mark,0.)
        oldmark = np.where(np.isfinite(self.last),self.last,mark)
        hold_pnl = np.sum(oldq*(mark-oldmark),axis=1)
        trade_pnl = np.sum(delta_total*(mark-safe_fill),axis=1)
        self.last = np.where(mark>0,mark,np.nan)
        self.equity = self.cash + np.sum(self.q*mark,axis=1)
        if not np.allclose(self.equity-previous_equity,hold_pnl+trade_pnl-costs,atol=1e-5,rtol=1e-8):
            raise AssertionError('Cash/position PnL reconciliation failed')
        self._record(timestamp,previous_equity,hold_pnl,trade_pnl,costs,turnover,
                     delta_total*safe_fill,exit_dollars,age_dollars,'bar',mark)

    def end_day(self, timestamp, day_index, close, elapsed_days=1):
        previous_equity = self.equity.copy()
        mark = np.where(np.isfinite(close)&(close>0),close,self.last)
        mark = np.where(np.isfinite(mark),mark,0.)
        oldmark = np.where(np.isfinite(self.last),self.last,mark)
        hold = np.sum(self.q*(mark-oldmark),axis=1)
        current_short = np.sum(np.maximum(-self.q,0)*mark,axis=1)
        borrow = (current_short+self.previous_eod_short*max(0,elapsed_days-1))*self.cfg['execution']['borrow_bps_annual']/10000/365
        self.previous_eod_short = current_short
        self.cash -= borrow
        self.equity = self.cash+np.sum(self.q*mark,axis=1)
        self.last = np.where(mark>0,mark,np.nan)
        z = np.zeros(len(self.names))
        self._record(timestamp,previous_equity,hold,z,borrow,z,np.zeros_like(self.q),z,z,'eod',mark)

    def _record(self,timestamp,previous,hold,trade,cost,turn,net_values,exits,ages,event,mark):
        values = []
        for j,name in enumerate(self.names):
            long = np.sum(np.maximum(self.q[j],0)*mark)
            short = np.sum(np.maximum(-self.q[j],0)*mark)
            values.append(dict(timestamp=timestamp,book=name,event=event,equity=self.equity[j],
                previous_equity=previous[j],pnl=self.equity[j]-previous[j],hold_pnl=hold[j],
                trade_pnl=trade[j],tradecost=cost[j],turnover=turn[j],net_turnover=np.abs(net_values[j]).sum(),
                long=long,short=short,nHold=int(np.count_nonzero(np.abs(self.q[j])>1e-10)),
                exit_notional=exits[j],age_notional=ages[j]))
        ls = {k:values[-2][k]+values[-1][k] for k in values[-1] if k not in ('timestamp','book','event')}
        ls.update(timestamp=timestamp,book='LS',event=event)
        ls['net_turnover'] = np.abs(net_values[-2]+net_values[-1]).sum()
        self.records.extend(values+[ls])

    def positions(self):
        rows = []
        for j,name in enumerate(self.names):
            for i in np.flatnonzero(np.abs(self.q[j])>1e-10):
                rows.append({'book':name,'order_book_id':self.symbols[i],
                             'adjusted_units':self.q[j,i],'mark_adj':self.last[i],
                             'market_value':self.q[j,i]*self.last[i]})
        return rows
