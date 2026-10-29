"""Readable terminal views of existing summary metrics; no recalculation."""

SUMMARY_SECTIONS = (
    ('Performance', ('from', 'to', 'trading_days', 'return', 'annual_return', 'net', 'pnl', 'sharp')),
    ('Drawdown and holdings', ('dd', 'dd_start', 'dd_end', 'avgdd', 'winrate', 'nHold', 'long', 'short', 'AHD')),
    ('Trading and costs', ('tvr', 'net_tvr', 'bp_mrgn', 'holdret', 'traderet', 'tradecost', 'tradecost_cash')),
    ('Factor diagnostics', ('IC', 'ICIR', 'rank_IC', 'rank_ICIR', 'auto_corr', 'Tstat', 'TC', 'ic_observations')),
)


def print_summary(summary, book='LS'):
    """Print all supplied fields for one book, retaining annual and ALL rows."""
    rows = summary.loc[summary['book'].eq(book)]
    print(f'Summary: {book}; returns/turnover/winrate are fractions; cash amounts in CNY')
    if rows.empty:
        print('No summary rows available.')
        return
    shown = {'book', 'period'}
    for title, columns in SUMMARY_SECTIONS:
        present = [name for name in columns if name in rows.columns]
        if present:
            print(f'\n[{title}]')
            print(rows[['period', *present]].to_string(index=False, na_rep='NaN'))
            shown.update(present)
    # Keep new producer metrics visible even before they receive a named section.
    extra = [name for name in rows.columns if name not in shown]
    if extra:
        print('\n[Additional metrics]')
        print(rows[['period', *extra]].to_string(index=False, na_rep='NaN'))

