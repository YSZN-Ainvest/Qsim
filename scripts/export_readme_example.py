"""Verify saved account summaries and export authentic README illustrations."""
import argparse
import html
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd
import pyarrow.dataset as ds


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', required=True)
    parser.add_argument('--stdout', required=True)
    parser.add_argument('--prefix', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--config', required=True)
    parser.add_argument('--directory', required=True)
    args = parser.parse_args()
    root = Path(args.run)
    target = Path(args.directory)
    target.mkdir(parents=True, exist_ok=True)
    assert (root/'COMPLETE').exists()
    summary = pd.DataFrame(json.loads((root/'summary.json').read_text()))
    daily = ds.dataset(root/'daily.parquet', format='parquet').to_table(use_threads=False).to_pandas()
    assert not daily.duplicated(['date', 'book']).any()
    keys = ['date', 'book']
    slots = ds.dataset(root/'slot_daily.parquet', format='parquet').to_table(use_threads=False).to_pandas()
    assert not slots.duplicated(['slot', *keys]).any()
    columns = ['equity', 'previous_equity', 'pnl', 'hold_pnl', 'trade_pnl', 'tradecost', 'turnover']
    expected_daily = slots.groupby(keys)[columns].mean().sort_index()
    actual_daily = daily.set_index(keys).sort_index()
    assert expected_daily.index.equals(actual_daily.index)
    np.testing.assert_allclose(actual_daily[columns], expected_daily, rtol=1e-10, atol=1e-7)
    np.testing.assert_allclose(daily['return'], daily.pnl/daily.previous_equity, rtol=1e-10, atol=1e-12)
    diagnostics = ds.dataset(root/'diagnostics.parquet', format='parquet').to_table(use_threads=False).to_pandas()
    for _, row in summary.iterrows():
        part = daily.loc[daily.book.eq(row.book)].sort_values('date')
        if row.period != 'ALL':
            part = part.loc[part.date.str.startswith(row.period)]
        start = part.previous_equity.iloc[0]
        nav = part.equity.to_numpy()/start
        peak, peak_date, drawdown, begin, end = 1., 'initial', 0., 'initial', part.date.iloc[0]
        for date, value in zip(part.date, nav):
            if value > peak:
                peak, peak_date = value, date
            if 1-value/peak > drawdown:
                drawdown, begin, end = 1-value/peak, peak_date, date
        actual = [row['return'], row.net, row.pnl, row.winrate, row.dd, row.tradecost_cash, row.tvr]
        expected = [nav[-1]-1, nav[-1], part.pnl.sum(), (part['return']>0).mean(), drawdown,
                    part.tradecost.sum(), part.tvr.mean()]
        np.testing.assert_allclose(actual, expected, rtol=1e-10, atol=1e-7)
        assert (row.dd_start, row.dd_end) == (begin, end)
        diag = diagnostics if row.period == 'ALL' else diagnostics.loc[diagnostics.date.str.startswith(row.period)]
        np.testing.assert_allclose([row.IC, row.rank_IC], [diag.ic.mean(), diag.rank_ic.mean()],
                                   rtol=1e-10, atol=1e-12, equal_nan=True)
        assert row.ic_observations == int(diag.ic.notna().sum())
    error = 0.
    for batch in ds.dataset(root/'events.parquet', format='parquet').scanner(
            columns=['pnl','hold_pnl','trade_pnl','tradecost'], use_threads=False).to_batches():
        f = batch.to_pandas()
        values = f.pnl-f.hold_pnl-f.trade_pnl+f.tradecost
        assert np.isfinite(values).all()
        error = max(error, float(values.abs().max()))
    assert error < 1e-5
    # The exact saved configuration must be reproducible by the published TOML.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from qsim.config import load_config
    config = json.loads((root/'config.json').read_text())
    assert load_config(args.config) == config
    assets = Path(__file__).resolve().parents[1]/'document/assets'
    for name in ('long_short_nav','long_short_pnl','group_nav','group_returns','group_excess_returns','ic','turnover'):
        src = root/f'{name}.png'
        dest = assets/f'{args.prefix}_{name}.png'
        assert src.stat().st_size > 1000
        shutil.copy2(src, dest)
        assert src.read_bytes() == dest.read_bytes()
    text = Path(args.stdout).read_text()
    text = text[text.index('Mode: '):text.index('Output: ')]
    first, second = text.split('[Trading and costs]', 1)
    pages = [first, '[Trading and costs]'+second]
    for index, content in enumerate(pages):
        suffix = 'terminal' if index == 0 else 'terminal_details'
        page = f'''<!doctype html><html><meta charset="utf-8"><title>QSim terminal output</title>
<style>*{{box-sizing:border-box}}body{{margin:0;padding:8px;width:700px;background:#101827;color:#e5e7eb}}
main{{border:1px solid #334155;border-radius:8px;overflow:hidden}}header{{padding:8px;background:#1e293b;font:10px sans-serif}}
pre{{margin:0;padding:8px;font:10px/1.35 monospace;white-space:pre}}footer{{padding:8px;color:#94a3b8;font:8px sans-serif}}</style>
<main><header>{html.escape(args.title)} · {index+1}/2</header><pre>$ bash run.sh {html.escape(args.config)}
{html.escape(content)}</pre><footer>Actual CLI stdout, formatted for documentation. Returns / turnover / winrate are fractions; cash in CNY.</footer></main></html>'''
        (target/f'{args.prefix}_{suffix}.html').write_text(page)
    payload = dict(status='PASS', run=str(root), summaries=len(summary), max_accounting_error=error,
                   audit=json.loads((root/'audit.json').read_text()),
                   long_short=summary.loc[summary.book.eq('LS')].to_dict('records'))
    with (target/f'{args.prefix}_verified.json').open('x') as stream:
        json.dump(payload, stream, indent=2, allow_nan=False)
    print(json.dumps(dict(status='PASS', summaries=len(summary), max_accounting_error=error,
                         long_short_all=payload['long_short'][1]), indent=2))


if __name__ == '__main__':
    main()
