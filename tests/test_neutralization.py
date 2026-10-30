"""Prepared exposures must preserve the independent, per-factor OLS reference."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from qsim.alpha_replay import AlphaReplay
from qsim.livetest import run_livetest
from qsim.operations import NeutralizationContext, neutralize, process
from test_livetest import fixture


def reference_process(*args, **kwargs):
    kwargs.pop('neutralization', None)
    return process(*args, **kwargs)


class NeutralizationTests(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(319)
        self.n = 90
        x = self.rng.normal(size=self.n)
        self.styles = pd.DataFrame({'size': x, 'duplicate': x, 'constant': 1.})
        self.industries = pd.DataFrame({'industry': np.tile(['a', 'b', 'c'], self.n // 3)})

    def test_exact_reference_with_missing_rank_deficient_and_constant_inputs(self):
        self.styles.loc[0, 'size'] = np.inf
        self.styles.loc[1, 'duplicate'] = np.nan
        self.industries.loc[2, 'industry'] = None
        styles = self.styles.copy(deep=True)
        industries = self.industries.copy(deep=True)
        context = NeutralizationContext(self.styles, self.industries)
        for count in (0, 1, 7, 90):
            for constant in (False, True):
                y = np.ones(self.n) if constant else self.rng.normal(size=self.n)
                y[:count] = np.nan
                original = y.copy()
                np.testing.assert_array_equal(context.apply(y), neutralize(y, self.styles, self.industries))
                np.testing.assert_array_equal(y, original)
        pd.testing.assert_frame_equal(self.styles, styles)
        pd.testing.assert_frame_equal(self.industries, industries)
        for styles, industries in ((None, None), (None, self.industries), (self.styles, None)):
            y = self.rng.normal(size=self.n)
            np.testing.assert_array_equal(NeutralizationContext(styles, industries).apply(y),
                                          neutralize(y, styles, industries))

    def test_threaded_preparation_once_and_distinct_finite_masks(self):
        values = [self.rng.normal(size=self.n) for _ in range(40)]
        for i, y in enumerate(values):
            y[:i % 3] = np.nan
        expected = [neutralize(y, self.styles, self.industries) for y in values]
        context = NeutralizationContext(self.styles, self.industries)
        with patch('qsim.operations.pd.get_dummies', wraps=pd.get_dummies) as dummies:
            with ThreadPoolExecutor(max_workers=2) as pool:
                actual = list(pool.map(context.apply, values))
            self.assertEqual(dummies.call_count, 1)
        self.assertEqual(len(context._designs), 3)
        self.assertFalse(context._design.flags.writeable)
        for a, b in zip(actual, expected):
            np.testing.assert_array_equal(a, b)

    def test_mask_cache_bounded_and_eviction_does_not_change_results(self):
        context = NeutralizationContext(self.styles, self.industries)
        context._max_bytes = 6000
        for i in range(25):
            y = self.rng.normal(size=self.n)
            y[i] = np.nan
            np.testing.assert_array_equal(context.apply(y), neutralize(y, self.styles, self.industries))
            self.assertLessEqual(context._cache_bytes, context._max_bytes)
            self.assertLessEqual(len(context._designs), context._max_masks)
        context._max_bytes = 1
        context._designs.clear()
        context._cache_bytes = 0
        y = self.rng.normal(size=self.n)
        np.testing.assert_array_equal(context.apply(y), neutralize(y, self.styles, self.industries))
        self.assertEqual(len(context._designs), 0)

    def test_operations_order_eligibility_and_lazy_insufficient_exit(self):
        exposures = pd.concat([self.styles, self.industries], axis=1)
        options = dict(min_cross_section=10, winsor_mad=3., standardize=True,
                       neutralize_styles=list(self.styles), neutralize_industries=list(self.industries))
        eligible = np.arange(self.n) % 5 != 0
        context = NeutralizationContext(self.styles, self.industries)
        for standardize in (False, True):
            options['standardize'] = standardize
            for i in range(5):
                y = self.rng.normal(size=self.n)
                y[i] = 1e8
                y[-i-1] = np.nan
                a = process(y, eligible, options, exposures)
                b = process(y, eligible, options, exposures, neutralization=context)
                np.testing.assert_array_equal(a, b)
        lazy = NeutralizationContext(self.styles, self.industries)
        result = process(np.full(self.n, np.nan), eligible, options, exposures, neutralization=lazy)
        self.assertTrue(np.isnan(result).all())
        self.assertIsNone(lazy._design)


class NeutralizationReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Retained beneath the caller's TMPDIR for inspection, like streaming tests.
        cls.root = Path(tempfile.mkdtemp(prefix='qsim-neutralization-'))
        cls.config = fixture(cls.root/'fixture', minute=True)
        rows = []
        for d, date in enumerate(pd.bdate_range('2022-12-19', periods=22)):
            for i, symbol in enumerate('ABCDEF'):
                rows.append(dict(order_book_id=symbol, available_at=str(date.date())+' 09:30',
                                 a=np.sin(i+d*.3), b=np.cos(i*2+d*.2),
                                 unused=np.nan if i == d % 6 else np.sin(i*3+d),
                                 size=float(i+(d % 3)*np.cos(i)), industry=str((i+d//3) % 2)))
        path = cls.root/'inputs.parquet'
        pd.DataFrame(rows).to_parquet(path, index=False)
        cls.config['factors'] = [dict(name=name, kind='table', path=str(path), column=name,
                                     weight=0. if name == 'unused' else 1.) for name in ('a', 'b', 'unused')]
        cls.config['operations'].update(exposures_path=str(path), neutralize_styles=['size'],
                                        neutralize_industries=['industry'])
        cls.config['data']['cache_directory'] = str(cls.root/'cache')
        cls.config['livetest']['samples'] = 2

    def test_reference_serial_parallel_and_new_decision_exposures(self):
        outputs = []
        processed = []
        for reference, pools in ((True, 1), (False, 1), (False, 2)):
            cfg = deepcopy(self.config)
            cfg['compute']['pools'] = pools
            with patch('qsim.factors.allowed_workers', return_value=(pools, {})):
                replay = AlphaReplay(cfg)
            try:
                if reference:
                    with patch('qsim.factors.process', side_effect=reference_process):
                        outputs.append(replay.run())
                else:
                    outputs.append(replay.run())
                processed.append(deepcopy(replay.model.last_processed))
                self.assertEqual(replay.model.effective_pools, pools)
                self.assertEqual(set(replay.model.state_dict()), {'history', 'symbols', 'tables'})
            finally:
                replay.model.close()
        self.assertGreater(np.isfinite(outputs[0].value).sum(), 0)
        for candidate in outputs[1:]:
            pd.testing.assert_frame_equal(outputs[0], candidate, check_exact=True)
        for candidate in processed[1:]:
            for name in processed[0]:
                np.testing.assert_array_equal(processed[0][name], candidate[name])

    def test_future_past_and_real_subprocess_checkpoint_with_exposures(self):
        report = run_livetest(deepcopy(self.config), 'all', checkpoint_process=True)
        self.assertEqual(report['status'], 'PASS', report)
        for case in report['checks']['c']['cases']:
            self.assertEqual(case['isolation'], 'subprocess')
            self.assertGreater(case['worker']['first_event'], 0)


if __name__ == '__main__':
    unittest.main()
