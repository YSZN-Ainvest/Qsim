import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsim.config import load_config
from qsim.experiments import compare_factors, comparison_cases
from qsim.factors import FactorModel
from test_integration import TinyMarket


class SimpleFactorTests(unittest.TestCase):
    def test_builtin_formulas_and_warmup(self):
        # Known daily returns: [10%, 20%], [0%, 0%], [-10%, 10%].
        closes = [[100., 100., 100.], [110., 100., 90.], [132., 100., 99.]]
        expected = {'momentum': [.32, 0., -.01], 'reversal': [-.32, 0., .01],
                    'volatility': [-.05, 0., -.1]}
        for kind, values in expected.items():
            with self.subTest(kind=kind):
                config = load_config({'factors': [{'name': kind, 'kind': kind, 'lookback': 2}],
                    'operations': {'winsor_mad': 0., 'standardize': False, 'min_cross_section': 2}})
                model = FactorModel(config, list('ABC'))
                for close in closes[:2]:
                    model.observe(close)
                self.assertTrue(np.isnan(model.compute(None, np.ones(3, bool))).all())
                model.observe(closes[-1])
                np.testing.assert_allclose(model.compute(None, np.ones(3, bool)), values, atol=1e-12)

    def test_missing_history_and_eligibility_are_excluded(self):
        config = load_config({'factors': [{'name': 'mom', 'kind': 'momentum', 'lookback': 2}],
            'operations': {'winsor_mad': 0., 'standardize': False, 'min_cross_section': 2}})
        model = FactorModel(config, list('ABCDE'))
        for close in [[10, 10, 10, 10, 10], [11, 12, np.nan, 0, 11], [12, 14, 13, 12, 12]]:
            model.observe(close)
        score = model.compute(None, np.array([True, True, True, True, False]))
        np.testing.assert_allclose(score, [.2, .4, np.nan, np.nan, np.nan])


class ComparisonTests(unittest.TestCase):
    def config(self, directory, dump=True):
        return load_config({'backtest': {'start': '2023-12-29', 'end': '2024-01-03',
                'allow_os': True, 'groups': 2, 'forecast_period': 1},
            'factors': [{'name': 'mom', 'kind': 'momentum', 'lookback': 1, 'weight': 1.},
                        {'name': 'rev', 'kind': 'reversal', 'lookback': 1, 'weight': .25}],
            'operations': {'min_cross_section': 2},
            'output': {'dump': dump, 'directory': str(directory), 'plots': False}})

    def test_case_isolation_and_factor_count(self):
        config = self.config('unused', False)
        original = deepcopy(config)
        cases = comparison_cases(config)
        self.assertEqual([len(case['factors']) for _, case in cases], [1, 1, 2])
        cases[0][1]['factors'][0]['weight'] = 99.
        self.assertEqual(cases[-1][1]['factors'][0]['weight'], 1.)
        self.assertEqual(config, original)
        config['factors'][1]['weight'] = 0
        with self.assertRaisesRegex(ValueError, '2–5'):
            comparison_cases(config)

    def test_complete_comparison_and_accounting(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('qsim.stats_runner.MarketData', TinyMarket):
                result = compare_factors(self.config(tmp))
            comparison = result['comparison']
            self.assertEqual(comparison.experiment.tolist(), ['mom', 'rev', 'combined'])
            self.assertTrue(comparison.passed.all())
            self.assertTrue(comparison.valid_ic_days.eq(1).all())
            self.assertTrue(comparison.max_accounting_error.lt(1e-6).all())
            self.assertTrue((result['output_directory'] / 'COMPLETE').is_file())
            saved = pd.read_csv(result['output_directory'] / 'comparison.csv')
            self.assertEqual(len(saved), 3)
            for directory in saved.output_directory:
                self.assertTrue((Path(directory) / 'COMPLETE').is_file())

    def test_no_dump_creates_no_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / 'absent'
            with patch('qsim.stats_runner.MarketData', TinyMarket):
                result = compare_factors(self.config(directory, False))
            self.assertIsNone(result['output_directory'])
            self.assertFalse(directory.exists())

    def test_cancelled_signals_fail_smoke_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.config(tmp)
            config['factors'][1]['weight'] = 1.
            with patch('qsim.stats_runner.MarketData', TinyMarket):
                with self.assertRaisesRegex(RuntimeError, 'Factor smoke checks failed'):
                    compare_factors(config)
            directory = next(Path(tmp).iterdir())
            self.assertTrue((directory / 'INCOMPLETE').is_file())
            self.assertFalse((directory / 'COMPLETE').exists())
            saved = pd.read_csv(directory / 'comparison.csv')
            combined = saved.loc[saved.experiment.eq('combined')].iloc[0]
            self.assertFalse(combined.passed)
            self.assertIn('trades', combined.failed_checks)
