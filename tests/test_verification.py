import unittest

import numpy as np

from scripts.verify_simple_factors import Audit, ReferenceAccount, corr


class IndependentVerifierTests(unittest.TestCase):
    def day(self, close):
        return {key: np.array([value], dtype=float) for key, value in dict(
            fill=10., raw=10., volume=10000., complete=1., bad=0., suspended=0.,
            up=11., down=9., adjustment=1., close=close).items()}

    def test_cash_and_short_accounts_against_hand_calculation(self):
        for short in (False, True):
            with self.subTest(short=short):
                audit = Audit()
                account = ReferenceAccount(100., 1, short)
                row = account.step(np.array([-1. if short else 1.]), np.array([10.]),
                                   self.day(9. if short else 11.), 0, .01, 1., audit)
                units = 100. / 10.1  # 10 yuan purchase/short plus 1% fee.
                self.assertAlmostEqual(row['turnover'], units * 10.)
                self.assertAlmostEqual(row['tradecost'], units * .1)
                self.assertAlmostEqual(row['equity'], 100. + units - units * .1)
                self.assertFalse(audit.failures)

    def test_capacity_and_next_day_exit(self):
        audit = Audit()
        account = ReferenceAccount(100., 1)
        day = self.day(11.)
        day['volume'][:] = 100.
        first = account.step(np.array([1.]), np.array([10.]), day, 0, .01, .01, audit)
        self.assertAlmostEqual(account.units.item(), 1.)
        self.assertAlmostEqual(first['equity'], 100.9)
        day['close'][:] = 10.
        second = account.step(np.array([0.]), np.array([11.]), day, 1, .01, .01, audit)
        self.assertAlmostEqual(second['equity'], 99.8)
        self.assertAlmostEqual(second['age_notional'], 10.)
        self.assertAlmostEqual(account.units.item(), 0.)
        self.assertFalse(audit.failures)

    def test_checker_detects_nan_and_numeric_corruption(self):
        audit = Audit()
        audit.equal('altered', [1., np.nan], [1., 2.])
        audit.equal('altered', [1., 3.], [1., 2.])
        self.assertEqual(audit.checks['altered']['failed'], 2)
        self.assertEqual(len(audit.failures), 2)
        self.assertAlmostEqual(corr(np.array([1., 2., 3.]), np.array([4., 2., 1.]), True), -1.)
