"""Small, sequential single-factor and combined-factor plumbing experiments."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .config import load_config
from .runner import run


def comparison_cases(config):
    config = load_config(config)
    factors = [f for f in config['factors'] if float(f.get('weight', 1)) != 0]
    if not 2 <= len(factors) <= 5:
        raise ValueError('Factor comparison requires 2–5 nonzero-weight factors')
    cases = []
    for i, factor in enumerate(factors):
        case = deepcopy(config)
        case['factors'] = [deepcopy(factor)]
        case['output']['name'] = f'factor_{i + 1}'
        cases.append((factor['name'], case))
    combined = deepcopy(config)
    combined['output']['name'] = 'combined'
    cases.append(('combined', combined))
    return cases


def summarize_run(name, result):
    summary = result['summary']
    row = summary.loc[summary.book.eq('LS') & summary.period.eq('ALL')].iloc[0]
    diag, daily, events = result['diagnostics'], result['daily'], result['events']
    ls = daily.loc[daily.book.eq('LS')]
    valid_ic = diag.label_status.eq('complete') & np.isfinite(diag.ic) & np.isfinite(diag.rank_ic)
    residual = events.pnl - (events.hold_pnl + events.trade_pnl - events.tradecost)
    accounting_ok = bool(np.isfinite(residual).all() and np.allclose(
        residual, 0, atol=1e-6, rtol=0))
    # Every signal must be usable; zero-trade / all-NaN IC runs must not look successful.
    checks = {
        'signals': bool(diag.signal_count.ge(result['config']['backtest']['groups']).all()
                        and result['audit']['no_signal_bars'] == 0),
        'trades': bool(ls.turnover.gt(0).any()),
        'ic': bool(valid_ic.any()),
        'equity': bool(np.isfinite(ls.equity).all() and ls.equity.gt(0).all()),
        'accounting': accounting_ok,
    }
    return dict(
        experiment=name, **{key: row[key] for key in
            ('return', 'sharp', 'dd', 'IC', 'rank_IC', 'tvr')},
        days=result['audit']['days'], min_signal_count=int(diag.signal_count.min()),
        valid_ic_days=int(diag.loc[valid_ic, 'date'].nunique()),
        traded_days=int(ls.loc[ls.turnover.gt(0), 'date'].nunique()),
        no_signal_bars=result['audit']['no_signal_bars'],
        max_accounting_error=float(residual.abs().max()),
        passed=all(checks.values()),
        failed_checks=','.join(key for key, passed in checks.items() if not passed),
        output_directory=str(result['output_directory'] or ''),
    )


def compare_factors(config):
    """Run independent accounts sequentially; retain only their compact summaries."""
    config = load_config(config)
    cases = comparison_cases(config)  # Validate before writing or reading market data.
    directory = None
    if config['output']['dump']:
        stamp = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S_%f')
        directory = Path(config['output']['directory']) / f"{config['output']['name']}_comparison_{stamp}"
        directory.mkdir(parents=True, exist_ok=False)
    rows = []
    try:
        for name, case in cases:
            if directory is not None:
                case['output']['directory'] = str(directory)
            print(f'\nExperiment: {name}', flush=True)
            result = run(case)
            rows.append(summarize_run(name, result))
            del result
            # Preserve finished summaries if a later experiment fails.
            comparison = pd.DataFrame(rows)
            if directory is not None:
                comparison.to_csv(directory / 'comparison.csv', index=False)
        if not comparison.passed.all():
            failed = comparison.loc[~comparison.passed, ['experiment', 'failed_checks']]
            raise RuntimeError(f'Factor smoke checks failed:\n{failed.to_string(index=False)}')
        if directory is not None:
            (directory / 'COMPLETE').write_text('All factor experiments passed smoke checks.\n')
    except Exception:
        if directory is not None:
            (directory / 'INCOMPLETE').write_text('Experiment failed; inspect comparison.csv and individual runs.\n')
        raise
    return {'comparison': comparison, 'output_directory': directory}
