import tempfile
import unittest
from pathlib import Path

from einstein.gaps import Gap, Match
from einstein.store import Store
from einstein.velocity import (
    DORMANT_AGE_DAYS,
    ScoredGap,
    score_gaps,
    snapshot_gaps,
)


def _repo_match(similarity: float, *, best_id: str | None = "someone/x") -> Match:
    return Match(against_type="repo", best_id=best_id, best_similarity=similarity, threshold=0.2)


def _patent_match(similarity: float = 0.0, *, best_id: str | None = None) -> Match:
    return Match(against_type="patent", best_id=best_id, best_similarity=similarity, threshold=0.2)


def _paper_match(similarity: float = 0.1, *, best_id: str | None = "2508.00001") -> Match:
    return Match(against_type="paper", best_id=best_id, best_similarity=similarity, threshold=0.2)


def _invention_gap(subject_id: str, repo_similarity: float, *, best_id: str | None = "someone/x") -> Gap:
    return Gap(
        kind="true_invention_gap",
        subject_type="paper",
        subject_id=subject_id,
        subject_title="A Paper",
        matches=(_repo_match(repo_similarity, best_id=best_id), _patent_match()),
    )


def _unformalized_code_gap(subject_id: str) -> Gap:
    return Gap(
        kind="unformalized_code",
        subject_type="repo",
        subject_id=subject_id,
        subject_title="a-repo",
        matches=(_paper_match(),),
    )


class SnapshotGapsTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = Path(self._tmpdir.name) / "einstein.db"
        self.store = Store(self.db_path, clock=lambda: "2026-08-08T00:00:00+00:00")
        self.addCleanup(self.store.close)

    def test_empty_store_snapshots_empty(self):
        self.assertEqual(snapshot_gaps(self.store), {})

    def test_snapshot_keyed_by_gap_key(self):
        self.store.upsert_gap("true_invention_gap:paper:p1", "true_invention_gap", {"score": 0.9})
        snap = snapshot_gaps(self.store)
        self.assertEqual(set(snap), {"true_invention_gap:paper:p1"})
        self.assertEqual(snap["true_invention_gap:paper:p1"]["payload"], {"score": 0.9})


class ScoreGapsTest(unittest.TestCase):
    def test_gap_absent_from_previous_is_new(self):
        gap = _invention_gap("p1", 0.05)
        scored = score_gaps({}, [gap], now="2026-08-08T00:00:00+00:00")

        self.assertEqual(len(scored), 1)
        self.assertTrue(scored[0].is_new)
        self.assertEqual(scored[0].trend, "new")
        self.assertIsNone(scored[0].age_days)
        self.assertIsNone(scored[0].similarity_delta)

    def test_dedup_splits_new_from_seen(self):
        seen_gap = _invention_gap("seen", 0.05)
        new_gap = _invention_gap("new", 0.05)
        previous = {
            seen_gap.gap_key: {
                "first_seen": "2026-08-08T00:00:00+00:00",
                "payload": seen_gap.to_payload(),
            }
        }

        scored = score_gaps(previous, [seen_gap, new_gap], now="2026-08-09T00:00:00+00:00")
        by_id = {s.gap.subject_id: s for s in scored}

        self.assertFalse(by_id["seen"].is_new)
        self.assertTrue(by_id["new"].is_new)

    def test_young_unchanged_gap_is_stable(self):
        gap = _invention_gap("p1", 0.05)
        previous = {
            gap.gap_key: {
                "first_seen": "2026-08-08T00:00:00+00:00",
                "payload": gap.to_payload(),
            }
        }

        scored = score_gaps(previous, [gap], now="2026-08-15T00:00:00+00:00")

        self.assertFalse(scored[0].is_new)
        self.assertEqual(scored[0].trend, "stable")
        self.assertAlmostEqual(scored[0].age_days, 7.0)
        self.assertEqual(scored[0].similarity_delta, 0.0)

    def test_old_unchanged_gap_is_dormant(self):
        gap_then = _invention_gap("p1", 0.05)
        gap_now = _invention_gap("p1", 0.05)
        previous = {
            gap_then.gap_key: {
                "first_seen": "2026-08-08T00:00:00+00:00",
                "payload": gap_then.to_payload(),
            }
        }

        scored = score_gaps(previous, [gap_now], now="2028-09-15T00:00:00+00:00")

        self.assertEqual(scored[0].trend, "dormant")
        self.assertGreaterEqual(scored[0].age_days, DORMANT_AGE_DAYS)
        self.assertEqual(scored[0].similarity_delta, 0.0)

    def test_rising_repo_similarity_overrides_dormant_age(self):
        gap_then = _invention_gap("p1", 0.05)
        gap_now = _invention_gap("p1", 0.18)  # closer to threshold, still below it
        previous = {
            gap_then.gap_key: {
                "first_seen": "2026-08-08T00:00:00+00:00",
                "payload": gap_then.to_payload(),
            }
        }

        scored = score_gaps(previous, [gap_now], now="2028-09-15T00:00:00+00:00")

        self.assertEqual(scored[0].trend, "rising")
        self.assertGreaterEqual(scored[0].age_days, DORMANT_AGE_DAYS)  # still old
        self.assertAlmostEqual(scored[0].similarity_delta, 0.13)

    def test_falling_repo_similarity_is_not_rising(self):
        gap_then = _invention_gap("p1", 0.15)
        gap_now = _invention_gap("p1", 0.05)
        previous = {
            gap_then.gap_key: {
                "first_seen": "2026-08-08T00:00:00+00:00",
                "payload": gap_then.to_payload(),
            }
        }

        scored = score_gaps(previous, [gap_now], now="2026-08-09T00:00:00+00:00")

        self.assertEqual(scored[0].trend, "stable")
        self.assertAlmostEqual(scored[0].similarity_delta, -0.10)

    def test_no_repo_match_in_current_gap_yields_no_similarity_delta(self):
        gap = _unformalized_code_gap("someone/x")
        previous = {
            gap.gap_key: {
                "first_seen": "2026-08-08T00:00:00+00:00",
                "payload": gap.to_payload(),
            }
        }

        scored = score_gaps(previous, [gap], now="2028-09-15T00:00:00+00:00")

        self.assertIsNone(scored[0].similarity_delta)
        self.assertEqual(scored[0].trend, "dormant")  # falls back to age alone

    def test_prior_gap_had_no_repo_match_yields_no_similarity_delta(self):
        gap_then = _invention_gap("p1", 0.0, best_id=None)  # empty repo corpus that run
        gap_now = _invention_gap("p1", 0.05)  # a repo now exists, still below threshold
        previous = {
            gap_then.gap_key: {
                "first_seen": "2026-08-08T00:00:00+00:00",
                "payload": gap_then.to_payload(),
            }
        }

        scored = score_gaps(previous, [gap_now], now="2026-08-09T00:00:00+00:00")

        self.assertIsNone(scored[0].similarity_delta)
        self.assertEqual(scored[0].trend, "stable")

    def test_current_gap_lost_its_repo_match_yields_no_similarity_delta(self):
        gap_then = _invention_gap("p1", 0.05)
        gap_now = _invention_gap("p1", 0.0, best_id=None)  # repo corpus now empty
        previous = {
            gap_then.gap_key: {
                "first_seen": "2026-08-08T00:00:00+00:00",
                "payload": gap_then.to_payload(),
            }
        }

        scored = score_gaps(previous, [gap_now], now="2026-08-09T00:00:00+00:00")

        self.assertIsNone(scored[0].similarity_delta)

    def test_score_gaps_against_store_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            ticks = iter(["2026-08-08T00:00:00+00:00", "2028-09-15T00:00:00+00:00"])
            with Store(Path(tmp) / "einstein.db", clock=lambda: next(ticks)) as store:
                gap = _invention_gap("p1", 0.05)

                previous = snapshot_gaps(store)
                scored = score_gaps(previous, [gap], now="2026-08-08T00:00:00+00:00")
                self.assertTrue(scored[0].is_new)
                store.upsert_gap(gap.gap_key, gap.kind, gap.to_payload())

                previous = snapshot_gaps(store)
                scored = score_gaps(previous, [gap], now="2028-09-15T00:00:00+00:00")
                self.assertFalse(scored[0].is_new)
                self.assertEqual(scored[0].trend, "dormant")


class ScoredGapInvariantsTest(unittest.TestCase):
    def test_is_new_and_trend_must_agree(self):
        gap = _invention_gap("p1", 0.05)
        with self.assertRaises(AssertionError):
            ScoredGap(gap=gap, is_new=True, age_days=None, similarity_delta=None, trend="dormant")

    def test_new_gap_cannot_carry_an_age(self):
        gap = _invention_gap("p1", 0.05)
        with self.assertRaises(AssertionError):
            ScoredGap(gap=gap, is_new=True, age_days=5.0, similarity_delta=None, trend="new")

    def test_bad_trend_literal_is_rejected(self):
        gap = _invention_gap("p1", 0.05)
        with self.assertRaises(AssertionError):
            ScoredGap(gap=gap, is_new=False, age_days=1.0, similarity_delta=None, trend="bogus")


if __name__ == "__main__":
    unittest.main()
