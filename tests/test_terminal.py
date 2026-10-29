import contextlib
import io
import unittest
from unittest.mock import patch

import pandas as pd

from qsim.cli import main
from qsim.config import load_config
from qsim.terminal import print_summary


class TerminalTests(unittest.TestCase):
    def test_cli_keeps_drawdown_dates_winrate_and_all_producer_metrics(self):
        summary = pd.DataFrame([
            dict(book='LS', period='2024', **{'from': '2024-01-02', 'to': '2024-12-31'},
                 dd=0.2, dd_start='2024-03-11', dd_end='2024-07-22', winrate=0.625,
                 avgdd=0.1, annual_return=0.12, tradecost_cash=1357.0,
                 ICIR=0.3, rank_ICIR=0.4, Tstat=2.1, AHD=4.5, TC=0.8,
                 ic_observations=220, trading_days=240, future_metric=9876.0),
            dict(book='LS', period='ALL', dd_start='initial', dd_end='2024-07-22', winrate=0.625),
            dict(book='G01', period='DO_NOT_PRINT', winrate=1.),
        ])
        before = summary.copy(deep=True)
        result = dict(summary=summary, audit={'summary_basis': 'comparison_mean'}, output_directory='/example/run')
        output = io.StringIO()
        with patch('sys.argv', ['qsim', '--config', 'example.toml']), \
             patch('qsim.cli.load_config', return_value=load_config({})), \
             patch('qsim.cli.run', return_value=result), contextlib.redirect_stdout(output):
            main()
        text = output.getvalue()
        for column in summary.columns.drop('book'):
            self.assertIn(column, text)
        for value in ('2024-03-11', '2024-07-22', '0.625', 'initial', 'ALL', 'NaN', '9876', 'Output: /example/run'):
            self.assertIn(value, text)
        self.assertNotIn('DO_NOT_PRINT', text)
        pd.testing.assert_frame_equal(summary, before)

    def test_empty_book_is_explicit(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            print_summary(pd.DataFrame({'book': ['G01'], 'period': ['ALL']}))
        self.assertIn('No summary rows available', output.getvalue())
