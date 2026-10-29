"""Independent audit of the daily simple-factor experiment; imports no QSim code.

Rebuilds signals, labels, target statistics and cash accounts from saved settings
and independently read market tables. Restricted to the documented smoke setup.
Run with single-thread environment and an external resource limit/monitor.
"""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import resource
import time
import traceback

import numpy as np
import pandas as pd
import pyarrow as pa


class Audit:
    def __init__(self):
        self.checks = {}
        self.failures = []

    def equal(self, key, actual, expected, atol=1e-9, rtol=1e-11):
        a, b = np.asarray(actual, dtype=float), np.asarray(expected, dtype=float)
        same = a.shape == b.shape
        ok = same and bool(np.isclose(a, b, atol=atol, rtol=rtol, equal_nan=True).all())
        finite = np.isfinite(a) & np.isfinite(b) if same else np.array(False)
        error = float(np.max(np.abs(a[finite] - b[finite]))) if finite.any() else 0.
        entry = self.checks.setdefault(key, {'checks': 0, 'failed': 0, 'max_abs_error': 0.})
        entry['checks'] += 1
        entry['failed'] += int(not ok)
        entry['max_abs_error'] = max(entry['max_abs_error'], error)
        if not ok and len(self.failures) < 40:
            self.failures.append({'check': key, 'number': entry['checks'], 'max_abs_error': error})

    def truth(self, key, condition):
        self.equal(key, int(bool(condition)), 1, atol=0, rtol=0)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def series(frame, column):
    return frame[column].to_numpy(dtype=float, na_value=np.nan)


def ratio(a, b):
    with np.errstate(divide='ignore', invalid='ignore'):
        return np.where(np.isfinite(a) & np.isfinite(b) & (a > 0) & (b > 0), a / b, np.nan)


def read_market(config, factor_root, audit):
    root, raw_root = Path(config['data']['root']), Path(config['data']['raw_root'])
    securities = pd.read_parquet(root / 'securities.parquet')
    symbols = sorted(securities.loc[securities.type.eq('CS'), 'order_book_id'].unique())
    calendar = pd.read_parquet(root / 'calendar/1d.parquet')
    dates = sorted(calendar.date.unique())
    start, end = config['backtest']['start'], config['backtest']['end']
    run_dates = [d for d in dates if start <= d <= end]
    first = dates.index(run_dates[0])
    warmup = dates[max(0, first - config['data']['warmup_days']):first]
    dates = warmup + run_dates
    # Bound memory: only retain requested dates while streaming annual factor files.
    parts = []
    for year in sorted({d[:4] for d in dates}):
        for chunk in pd.read_csv(factor_root / f'stock/{year}.csv.gz', chunksize=50000,
                                 usecols=['order_book_id', 'date', 'backward_factor']):
            selected = chunk.loc[chunk.date.isin(dates)]
            if len(selected):
                parts.append(selected)
    factors = pd.concat(parts, ignore_index=True)
    audit.truth('input.factor_keys_unique', not factors.duplicated(['date', 'order_book_id']).any())
    factors = factors.pivot(index='date', columns='order_book_id', values='backward_factor')
    factors = factors.reindex(index=dates, columns=symbols).to_numpy(float)
    days = []
    for index, date in enumerate(dates):
        stamp = date.replace('-', '')
        def parquet(category, frequency):
            p = root / category / frequency / date[:4] / f'{stamp}.parquet'
            frame = pd.read_parquet(p)
            audit.truth('input.unique_' + category, not frame.order_book_id.duplicated().any())
            return frame.set_index('order_book_id').reindex(symbols)
        price, vwap, pool = parquet('prices', '1d'), parquet('vwap', '1d'), parquet('universe', 'all_a')
        raw = pd.read_csv(raw_root / f'daily_bar/{date[:4]}/{stamp}.csv')
        audit.truth('input.raw_keys_unique', not raw.order_book_id.duplicated().any())
        audit.truth('input.raw_dates', raw.datetime.eq(date).all())
        raw = raw.set_index('order_book_id').reindex(symbols)
        member = pool.in_all_a.eq(True).to_numpy()
        present = member & raw.datetime.notna().to_numpy()
        status = pd.read_csv(raw_root / f'trading_status/{date[:4]}/{stamp}.csv', dtype={'security_code': str})
        status['order_book_id'] = status.security_code + '.' + status.market
        audit.truth('input.status_keys_unique', not status.order_book_id.duplicated().any())
        suspended = series(status.set_index('order_book_id').reindex(symbols), 'suspended')
        audit.equal('input.suspended', series(pool, 'suspended'), np.where(member, suspended, np.nan))
        for col in ('open', 'close'):
            expected = np.where(present, series(raw, col) * factors[index], np.nan)
            audit.equal('input.' + col + '_adjusted', series(price, col + '_adj'), expected)
        for col in ('volume', 'total_turnover'):
            audit.equal('input.' + col, series(vwap, col), np.where(present, series(raw, col), np.nan), atol=1e-5)
        raw_vwap = ratio(series(raw, 'total_turnover'), series(raw, 'volume'))
        audit.equal('input.vwap_raw', series(vwap, 'vwap_raw'), np.where(present, raw_vwap, np.nan))
        audit.equal('input.vwap_adjusted', series(vwap, 'vwap_adj'),
                    np.where(present, raw_vwap * factors[index], np.nan))
        outside = np.isfinite(raw_vwap) & ((raw_vwap < series(raw, 'low') - .011)
                                         | (raw_vwap > series(raw, 'high') + .011))
        audit.equal('input.vwap_flag', series(vwap, 'vwap_outside_ohlc'), np.where(present, outside, np.nan))
        audit.equal('input.complete', series(price, 'bar_complete'), np.where(present, 1., np.nan))
        expected_bar = calendar.loc[calendar.date.eq(date), 'bar_id'].item()
        audit.equal('input.price_bar', series(price, 'bar_id'), np.where(present, expected_bar, np.nan))
        audit.equal('input.vwap_bar', series(vwap, 'bar_id'), np.where(present, expected_bar, np.nan))
        days.append(dict(date=date, close=series(price, 'close_adj'), fill=series(vwap, 'vwap_adj'),
            raw=series(vwap, 'vwap_raw'), volume=series(vwap, 'volume'),
            eligible=member & (series(pool, 'suspended') == 0), st=series(pool, 'st_status'),
            suspended=series(pool, 'suspended'), complete=series(price, 'bar_complete'),
            bad=series(vwap, 'vwap_outside_ohlc'), adjustment=ratio(series(price, 'close_adj'), series(raw, 'close')),
            up=series(raw, 'limit_up'), down=series(raw, 'limit_down')))
        if (index + 1) % 20 == 0:
            print(f'Input cross-check: {index + 1}/{len(dates)} dates', flush=True)
    return symbols, days, len(warmup)


def signal(closes, index, day, config):
    eligible = day['eligible'] & ((day['st'] == 0) | np.isnan(day['st']))
    columns = []
    for spec in config['factors']:
        k = spec['lookback']
        window = closes[index - k - 1:index]
        good = (np.isfinite(window) & (window > 0)).all(axis=0)
        if spec['kind'] == 'volatility':
            with np.errstate(divide='ignore', invalid='ignore'):
                values = -pd.DataFrame(window[1:] / window[:-1] - 1).std(axis=0, ddof=0).to_numpy()
        else:
            values = ratio(window[-1], window[0]) - 1
            if spec['kind'] == 'reversal':
                values = -values
        columns.append(np.where(good, values, np.nan))
    frame = pd.DataFrame(np.asarray(columns).T)
    usable = eligible & frame.notna().all(axis=1).to_numpy()
    values = frame.loc[usable].copy()
    for j, spec in enumerate(config['factors']):
        med = values[j].median()
        mad = (values[j] - med).abs().median() * 1.4826
        if mad > 1e-12:
            values[j] = values[j].clip(med - config['operations']['winsor_mad'] * mad,
                                       med + config['operations']['winsor_mad'] * mad)
        values[j] = (values[j] - values[j].mean()) / values[j].std(ddof=0) * spec.get('weight', 1.)
    score = np.full(len(eligible), np.nan)
    score[usable] = values.sum(axis=1).to_numpy()
    order = pd.Series(score).dropna().sort_values(kind='stable').index.to_numpy()
    group = np.full(len(score), -1)
    group[order] = np.floor(np.arange(len(order)) * config['backtest']['groups'] / len(order)).astype(int)
    count = config['backtest']['groups']
    weights = np.zeros((count + 2, len(score)))
    for j in range(count):
        weights[j] = (group == j) / (group == j).sum()
    weights[-2], weights[-1] = weights[count - 1], -weights[0]
    return score, weights


def corr(x, y, rank=False):
    good = np.isfinite(x) & np.isfinite(y)
    if good.sum() < 3:
        return np.nan
    a, b = pd.Series(x[good]), pd.Series(y[good])
    if rank:
        a, b = a.rank(), b.rank()
    if a.std(ddof=0) < 1e-12 or b.std(ddof=0) < 1e-12:
        return np.nan
    return float(((a - a.mean()) * (b - b.mean())).sum() /
                 np.sqrt(((a - a.mean()) ** 2).sum() * ((b - b.mean()) ** 2).sum()))


def contribution(weight, returns):
    active = np.abs(weight) > 1e-14
    return float(weight[active] @ returns[active]) if np.isfinite(returns[active]).all() else np.nan


class ReferenceAccount:
    """One fixed-direction account; daily rebalance, no QSim engine calls."""
    def __init__(self, capital, n, short=False):
        self.cash = capital
        self.equity = capital
        self.units = np.zeros(n)
        self.mark = np.zeros(n)
        self.birth = np.zeros(n)
        self.short = short

    def step(self, weights, reference, day, t, fee, participation, audit):
        before, old = self.equity, self.units.copy()
        p = day['fill']
        valid = np.isfinite(p) & (p > 0) & np.isfinite(day['raw']) & (day['raw'] > 0)
        valid &= (day['volume'] > 0) & (day['complete'] == 1) & (day['bad'] == 0) & (day['suspended'] == 0)
        buy = valid & np.isfinite(day['up']) & (day['up'] > 0) & (day['raw'] < day['up'] - .005)
        sell = valid & np.isfinite(day['down']) & (day['down'] > 0) & (day['raw'] > day['down'] + .005)
        price = np.where(valid, p, 0.)
        target = old.copy()
        known = np.isfinite(reference) & (reference > 0)
        target[known] = weights[known] * before / reference[known]
        capacity = np.zeros(len(p))
        adjusted = np.isfinite(day['adjustment']) & (day['adjustment'] > 0)
        capacity[adjusted] = day['volume'][adjusted] * participation / day['adjustment'][adjusted]
        reduce = np.maximum(np.abs(old) - np.abs(target), 0)
        exit_units = np.where(buy if self.short else sell, np.minimum(reduce, capacity), 0.)
        removed = exit_units if self.short else -exit_units
        age = np.zeros(len(p))
        held = np.abs(old) > 1e-12
        age[held] = self.birth[held] / np.abs(old[held])
        exit_notional = float(exit_units @ price)
        age_notional = float((exit_units * price) @ (t - age))
        self.birth -= exit_units * age
        self.units += removed
        self.cash -= float(removed @ price) + fee * exit_notional
        increase = np.maximum(np.abs(target) - np.abs(self.units), 0)
        acquired = np.where(sell if self.short else buy, np.minimum(increase, capacity - exit_units), 0.)
        provisional = np.where(valid, p, self.mark)
        value = self.cash + float(self.units @ provisional)
        headroom = max(0., value - float(np.abs(self.units) @ provisional))
        demand = float(acquired @ price) * (1 + fee)
        budget = headroom if self.short else min(headroom, max(self.cash, 0.))
        acquired *= min(1., budget / max(demand, 1e-30))
        added = -acquired if self.short else acquired
        delta = removed + added
        self.units += added
        self.birth += acquired * t
        turnover = exit_notional + float(acquired @ price)
        cost = fee * turnover
        self.cash -= float(added @ price) + fee * float(acquired @ price)
        mark = np.where(np.isfinite(day['close']) & (day['close'] > 0), day['close'], self.mark)
        hold = float(old @ (mark - self.mark))
        trade = float(delta @ (mark - price))
        self.mark = mark
        self.equity = self.cash + float(self.units @ mark)
        # Daily T+1: reductions can use only pre-existing inventory.
        audit.truth('execution.t1', (exit_units <= np.abs(old) + 1e-10).all())
        audit.truth('execution.capacity', (exit_units + acquired <= np.nan_to_num(capacity) + 1e-8).all())
        audit.truth('execution.direction', (self.units <= 1e-9).all() if self.short else (self.units >= -1e-9).all())
        audit.truth('execution.cash', self.cash >= -1e-6)
        return dict(equity=self.equity, previous_equity=before, pnl=self.equity - before,
            hold_pnl=hold, trade_pnl=trade, tradecost=cost, turnover=turnover,
            net_turnover=float(np.abs(delta * price).sum()),
            long=float(np.maximum(self.units, 0) @ mark), short=float(np.maximum(-self.units, 0) @ mark),
            nHold=int((np.abs(self.units) > 1e-10).sum()), exit_notional=exit_notional, age_notional=age_notional)


def verify_case(directory, comparison_row, symbols, days, warmup, audit):
    config = json.loads((directory / 'config.json').read_text())
    metadata = json.loads((directory / 'audit.json').read_text())
    audit.truth('artifact.complete', (directory / 'COMPLETE').is_file() and not (directory / 'INCOMPLETE').exists())
    audit.truth('artifact.config_hash', hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest() == metadata['config_sha256'])
    audit.truth('artifact.catalog_hash', sha(Path(config['data']['root']) / 'catalog.json') == metadata['cache_catalog_sha256'])
    for file, digest in metadata['code_sha256'].items():
        audit.truth('artifact.code_hash', sha(Path(__file__).resolve().parents[1] / 'qsim' / file) == digest)
    for table in ('daily', 'diagnostics', 'stats_slots', 'stats_tvr', 'slot_daily', 'slot_summary'):
        csv, parquet = pd.read_csv(directory / f'{table}.csv'), pd.read_parquet(directory / f'{table}.parquet')
        audit.truth('artifact.schema_' + table, list(csv.columns) == list(parquet.columns) and len(csv) == len(parquet))
        for column in parquet.select_dtypes(include='number').columns:
            audit.equal('artifact.csv_parquet_' + table, csv[column], parquet[column], atol=1e-7)
    daily = pd.read_parquet(directory / 'daily.parquet').set_index(['date', 'book'])
    slots = pd.read_parquet(directory / 'stats_slots.parquet').set_index(['date', 'book'])
    diagnostics = pd.read_parquet(directory / 'diagnostics.parquet').set_index('date')
    events = pd.read_parquet(directory / 'events.parquet')
    audit.equal('accounting.saved_events', events.pnl, events.hold_pnl + events.trade_pnl - events.tradecost, atol=1e-6)
    summary = pd.read_csv(directory / 'summary.csv').set_index(['book', 'period'])
    count = config['backtest']['groups']
    names = [f'G{j + 1:02}' for j in range(count)] + ['long', 'short']
    capital = config['backtest']['capital']
    accounts = [ReferenceAccount(capital if j < count else capital / 2, len(symbols), name == 'short')
                for j, name in enumerate(names)]
    closes = np.array([d['close'] for d in days])
    horizon = config['backtest']['forecast_period']
    previous_score = np.full(len(symbols), np.nan)
    previous_weights = np.zeros((count + 2, len(symbols)))
    fee = config['execution']['fee_bps'] / 10000
    ref_rows, ic_values = [], []
    nan_target_days = 0
    for t, day in enumerate(days[warmup:]):
        index, date = warmup + t, day['date']
        score, weights = signal(closes, index, day, config)
        diag = diagnostics.loc[date]
        audit.equal('signal.count', diag.signal_count, np.isfinite(score).sum())
        audit.truth('signal.timestamp', str(diag.timestamp) == date + ' 08:00:00+08:00')
        audit.equal('signal.autocorrelation', diag.auto_corr, corr(score, previous_score))
        if index + horizon < len(days):
            def label_price(d):
                return np.where((d['complete'] == 1) & (d['bad'] == 0), d['fill'], np.nan)
            label = ratio(label_price(days[index + horizon]), label_price(day)) - 1
            ic, rank_ic = corr(score, label), corr(score, label, True)
            audit.equal('label.ic', [diag.ic, diag.rank_ic], [ic, rank_ic])
            audit.equal('label.count', diag.label_count, (np.isfinite(score) & np.isfinite(label)).sum())
            audit.truth('label.status', diag.label_status == 'complete')
            audit.truth('label.end', str(diag.label_end) == days[index + horizon]['date'] + ' 15:00:00+08:00')
            ic_values.append((ic, rank_ic))
        else:
            audit.truth('label.censored', diag.label_status == 'right_censored' and pd.isna(diag.ic) and pd.isna(diag.rank_ic))
        target_rows, account_rows = [], []
        price = np.where((day['complete'] == 1) & (day['bad'] == 0), day['fill'], np.nan)
        for j, name in enumerate(names):
            hold = contribution(previous_weights[j], ratio(price, days[index - 1]['close']) - 1)
            trade = contribution(weights[j], ratio(day['close'], price) - 1)
            tvr = float(np.abs(weights[j] - previous_weights[j]).sum())
            target_rows.append([hold, trade, hold + trade, tvr * fee, hold + trade - tvr * fee, tvr])
            row = accounts[j].step(weights[j], closes[index - 1], day, t, fee,
                                    config['execution']['participation'], audit)
            account_rows.append(row)
        target_rows.append(list((np.array(target_rows[-2]) + target_rows[-1]) / 2))
        nan_target_days += int(not np.isfinite(target_rows[-1][4]))
        combined = {key: account_rows[-2][key] + account_rows[-1][key] for key in account_rows[-1]}
        # LS net turnover may offset opposite legs; validate it from saved events separately.
        combined.pop('net_turnover')
        account_rows.append(combined)
        for name, row, target in zip(names + ['LS'], account_rows, target_rows):
            saved = daily.loc[(date, name)]
            for key, value in row.items():
                audit.equal('replay.' + key, saved[key], value, atol=1e-5, rtol=1e-11)
            audit.equal('replay.daily_return', saved['return'], row['pnl'] / row['previous_equity'])
            audit.equal('replay.daily_turnover', saved.tvr, row['turnover'] / row['previous_equity'])
            target_cols = ['hold_return', 'trade_return', 'raw_return', 'cost_return', 'reported_return', 'tvr']
            audit.equal('target.formula', slots.loc[(date, name), target_cols].to_numpy(float), target)
            ref_rows.append(dict(date=date, book=name, **row))
        actual_exposure = (accounts[-2].units + accounts[-1].units) * accounts[-2].mark
        audit.equal('signal.TC', diag.TC, corr(score, actual_exposure))
        previous_score, previous_weights = score, weights
    positions = pd.read_parquet(directory / 'final_positions.parquet')
    for name, account in zip(names, accounts):
        frame = positions.loc[positions.book.eq(name)].set_index('order_book_id').reindex(symbols)
        audit.equal('replay.final_units', frame.adjusted_units.fillna(0), account.units, atol=1e-7)
        active = np.abs(account.units) > 1e-10
        audit.equal('replay.final_marks', series(frame, 'mark_adj')[active], account.mark[active])
        audit.equal('replay.final_market_value', frame.market_value.fillna(0), account.units * account.mark, atol=1e-5)
    reference = pd.DataFrame(ref_rows)
    ic_mean = np.mean(ic_values, axis=0)
    for name, part in reference.groupby('book'):
        initial = part.previous_equity.iloc[0]
        nav = np.r_[1., part.equity.to_numpy() / initial]
        returns = part.pnl / part.previous_equity
        expected = {'return': nav[-1] - 1, 'sharp': returns.mean() / returns.std(ddof=1) * np.sqrt(252),
            'dd': float(np.max(1 - nav / np.maximum.accumulate(nav))), 'IC': ic_mean[0], 'rank_IC': ic_mean[1],
            'tvr': (part.turnover / part.previous_equity).mean(), 'pnl': part.pnl.sum(),
            'holdret': part.hold_pnl.sum() / initial, 'traderet': part.trade_pnl.sum() / initial,
            'tradecost': part.tradecost.sum() / initial, 'trading_days': len(part), 'ic_observations': len(ic_values)}
        for period in ('ALL', 'IS', config['backtest']['start'][:4]):
            for key, value in expected.items():
                audit.equal('summary.' + key, summary.loc[(name, period), key], value, atol=1e-6 if key == 'pnl' else 1e-9)
        if name == 'LS':
            for key in ('return', 'sharp', 'dd', 'IC', 'rank_IC', 'tvr'):
                audit.equal('comparison.' + key, comparison_row[key], expected[key])
    return dict(experiment=comparison_row.experiment, days=len(days) - warmup,
                valid_ic_days=len(ic_values), target_ls_nan_days=nan_target_days,
                execution_audit=metadata['execution'], output_directory=str(directory))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--comparison', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='New directory on NAS')
    parser.add_argument('--factor-root', type=Path,
                        default=Path('/mnt/nas/qinml_cache/adjustment_factors/backward/20260930'))
    args = parser.parse_args()
    if not args.output.resolve().is_relative_to('/mnt/nas/qinml_cache'):
        parser.error('Audit results must be under /mnt/nas/qinml_cache')
    args.output.mkdir(parents=True, exist_ok=False)
    pa.set_cpu_count(1)
    pa.set_io_thread_count(1)
    audit, started = Audit(), time.monotonic()
    report = {'status': 'running', 'comparison': str(args.comparison), 'cases': [],
              'script_sha256': sha(__file__), 'timestamp': datetime.now().astimezone().isoformat()}
    try:
        comparison = pd.read_csv(args.comparison / 'comparison.csv')
        audit.truth('artifact.comparison_complete', (args.comparison / 'COMPLETE').is_file())
        configs = [json.loads((Path(p) / 'config.json').read_text()) for p in comparison.output_directory]
        config = configs[0]
        for c in configs:
            b, e, o, s = c['backtest'], c['execution'], c['operations'], c['stats']
            assert c['data'] == config['data'] and b == config['backtest']
            assert b['mode'] == 'StatsEvery' and b['frequency'] == '0800' and not b['allow_os']
            assert c['universe']['kind'] == 'all_a' and c['data']['warmup_days'] >= 21
            assert not o['neutralize_styles'] and not o['neutralize_industries'] and o['standardize']
            assert o['exclude_st'] and o['st_unknown'] == 'allow' and not o['st_path']
            assert e['enforce_t1'] and e['limit_filter'] and e['suspension_filter'] and e['reject_bad_vwap']
            assert e['sell_tax_bps'] == e['slippage_bps'] == e['borrow_bps_annual'] == 0
            assert s['nIntvDelay'] == 1 and s['idelayEach'] == 0 and s['ileadEach'] is None
            assert not s['signal_times'] and b['start'][:4] == b['end'][:4]
            assert all(f['kind'] in ('momentum', 'reversal', 'volatility') and 1 <= f['lookback'] <= 20 for f in c['factors'])
        symbols, days, warmup = read_market(config, args.factor_root, audit)
        for _, row in comparison.iterrows():
            print('Independent replay:', row.experiment, flush=True)
            report['cases'].append(verify_case(Path(row.output_directory), row, symbols, days, warmup, audit))
        report['status'] = 'passed' if not any(c['failed'] for c in audit.checks.values()) else 'failed'
    except Exception:
        report['status'] = 'error'
        report['exception'] = traceback.format_exc()
    finally:
        report.update(checks=audit.checks, failures=audit.failures,
                      elapsed_seconds=time.monotonic() - started,
                      peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        (args.output / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        (args.output / ('COMPLETE' if report['status'] == 'passed' else 'INCOMPLETE')).write_text(report['status'] + '\n')
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    raise SystemExit(0 if report['status'] == 'passed' else 1)


if __name__ == '__main__':
    main()
