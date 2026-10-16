import unittest

from einstein_agents.novelty_auditor import DEFAULT_THETA
from einstein_agents.novelty_calibration import GRID, TARGET_CATCH_RATE, pick_theta, score_arms, sweep

# Tolerance band, not a hard pin (einstein-1af pattern). sklearn is pinned,
# so this guards against fixture/embedder/pool changes, not float drift.
BAND = 0.03


class NoveltyCalibrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = {r.theta: r for r in sweep(score_arms())}

    def test_shipped_default_is_the_calibrated_pick(self):
        self.assertEqual(pick_theta(list(self.rows.values())).theta, DEFAULT_THETA)

    def test_default_rates_stay_in_band(self):
        row = self.rows[DEFAULT_THETA]
        self.assertGreaterEqual(row.catch_rate, TARGET_CATCH_RATE)
        self.assertAlmostEqual(row.catch_rate, 0.966, delta=BAND)
        self.assertAlmostEqual(row.false_flag_rate, 0.065, delta=BAND)
        self.assertAlmostEqual(row.neighbor_false_flag_rate, 0.211, delta=BAND)

    def test_old_default_caught_nothing(self):
        self.assertIn(0.85, GRID)
        self.assertEqual(self.rows[0.85].catch_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
