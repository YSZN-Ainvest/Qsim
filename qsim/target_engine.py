"""Target rebalancing, with dated sellable inventory independent of stats switches."""
import numpy as np
from .engine import Ledger


class TargetLedger(Ledger):
    def __init__(self,config,symbols):
        super().__init__(config,symbols)
        self.today = np.zeros_like(self.q)
        self.birth_sum = np.zeros_like(self.q)
        self.day_index = -1

    def rebalance(self,day_index,timestamp,market,day,weights,reference,fire=True):
        e = self.cfg['execution']
        if self.day_index != day_index:
            self.today.fill(0)
            self.day_index = day_index
        oldq, previous = self.q.copy(),self.equity.copy()
        if np.any(previous<=0):
            raise RuntimeError('Portfolio equity exhausted')
        fill,raw,close = market['vwap_adj'],market['vwap_raw'],market['close_adj']
        valid = np.isfinite(fill)&(fill>0)&np.isfinite(raw)&(raw>0)&(market['volume']>0)&(market['bar_complete']==1)
        if e['reject_bad_vwap']:
            valid &= market['vwap_outside_ohlc']==0
        if e['suspension_filter']:
            valid &= day.suspended==0
        buy,sell = valid.copy(),valid.copy()
        if e['limit_filter']:
            buy &= np.isfinite(day.limit_up)&(day.limit_up>0)&(raw<day.limit_up-.005)
            sell &= np.isfinite(day.limit_down)&(day.limit_down>0)&(raw>day.limit_down+.005)
        safe_fill = np.where(valid,fill,0.)
        cap = np.divide(market['volume']*e['participation'],day.adjustment,
                        out=np.zeros(len(fill)),where=np.isfinite(day.adjustment)&(day.adjustment>0))
        remaining = np.broadcast_to(cap,self.q.shape).copy()
        desired = np.divide(weights*previous[:,None],reference[None,:],
                            out=self.q.copy(),where=np.isfinite(reference[None,:])&(reference[None,:]>0))
        if not fire:
            desired = self.q.copy()
        # Books have a fixed direction, so rebalancing reduces first, then adds.
        reduction = np.maximum(np.abs(self.q)-np.abs(desired),0.)
        inventory = np.abs(self.q)
        sellable = np.maximum(inventory-self.today,0.) if e['enforce_t1'] else inventory
        self.audit['t1_blocked_cells'] += int(np.count_nonzero(reduction>sellable+1e-10))
        request = -np.sign(self.q)*reduction
        allowed = np.where(request>0,buy[None,:],sell[None,:])
        self.audit['limit_blocked_cells'] += int(np.count_nonzero((request!=0)&~allowed&valid[None,:]))
        delta = np.where(allowed,-np.sign(self.q)*np.minimum(np.minimum(reduction,sellable),remaining),0.)
        exited = np.abs(delta)
        if e['enforce_t1']:
            birth = self.birth_sum-self.today*day_index
        else:
            birth = self.birth_sum.copy()
        mean_birth = np.divide(birth,sellable,out=np.full_like(self.q,day_index),where=sellable>1e-12)
        age_dollars = np.sum(exited*safe_fill*(day_index-mean_birth),axis=1)
        self.birth_sum -= exited*mean_birth
        if not e['enforce_t1']:
            self.today *= np.divide(inventory-exited,inventory,out=np.zeros_like(inventory),where=inventory>0)
        costs,turn = self._trade(delta,safe_fill)
        exit_dollars = turn.copy()
        total_delta = delta.copy()
        remaining -= exited
        self.audit['unfilled_exit_cells'] += int(np.count_nonzero(reduction>exited+1e-10))
        addition = np.sign(desired)*np.maximum(np.abs(desired)-np.abs(self.q),0.)
        allowed = np.where(addition>0,buy[None,:],sell[None,:])
        self.audit['limit_blocked_cells'] += int(np.count_nonzero((addition!=0)&~allowed&valid[None,:]))
        addition = np.where(allowed,np.sign(addition)*np.minimum(np.abs(addition),remaining),0.)
        fill_mark = np.where(valid,fill,np.nan_to_num(self.last))
        equity = self.cash+np.sum(self.q*fill_mark,axis=1)
        headroom = np.maximum(equity-np.sum(np.abs(self.q)*fill_mark,axis=1),0.)
        amount = np.sum(np.abs(addition)*safe_fill,axis=1)
        rate = (e['fee_bps']+e['slippage_bps']+e['sell_tax_bps'])/10000
        scale = np.minimum(1.,headroom/np.maximum(amount*(1+rate),1e-30))
        scale[:-1] = np.minimum(scale[:-1],np.maximum(self.cash[:-1],0)/np.maximum(amount[:-1]*(1+rate),1e-30))
        addition *= scale[:,None]
        extra_cost,extra_turn = self._trade(addition,safe_fill)
        costs += extra_cost;turn += extra_turn;total_delta += addition
        self.today += np.abs(addition)
        self.birth_sum += np.abs(addition)*day_index
        self.audit['entry_signals'] += int(np.any(addition))
        good_close = np.isfinite(close)&(close>0)
        self.audit['stale_mark_cells'] += int(np.count_nonzero((np.abs(self.q)>1e-10)&~good_close[None,:]))
        mark = np.where(good_close,close,np.nan_to_num(self.last))
        oldmark = np.where(np.isfinite(self.last),self.last,mark)
        hold = np.sum(oldq*(mark-oldmark),axis=1)
        trade = np.sum(total_delta*(mark-safe_fill),axis=1)
        self.last = np.where(mark>0,mark,np.nan)
        self.equity = self.cash+np.sum(self.q*mark,axis=1)
        if not np.allclose(self.equity-previous,hold+trade-costs,rtol=1e-8,atol=1e-5):
            raise AssertionError('Target ledger PnL does not reconcile')
        self._record(timestamp,previous,hold,trade,costs,turn,total_delta*safe_fill,
                     exit_dollars,age_dollars,'bar',mark)
