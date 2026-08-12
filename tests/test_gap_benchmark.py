import unittest

from einstein.gap_benchmark import (
    TRUSTWORTHY_MAX_FDR,
    TRUSTWORTHY_MIN_FLAG_RATE,
    BenchmarkPair,
    classify,
    evaluate,
    load_fixture,
    score_pairs,
    sweep,
    _fit_shared_embedder,
)
from einstein.schema import Record


def _paper(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="paper", id=id_, title=title, summary=summary,
        url=f"https://arxiv.org/abs/{id_}", ts="2026-08-08T00:00:00+00:00", raw={},
    )


def _repo(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="repo", id=id_, title=title, summary=summary,
        url=f"https://github.com/{id_}", ts="2026-08-08T00:00:00+00:00", raw={},
    )


class ClassifyTest(unittest.TestCase):
    def test_below_threshold_minus_margin_is_gap(self):
        self.assertEqual(classify(0.05, threshold=0.2, margin=0.1), "gap")

    def test_above_threshold_plus_margin_is_match(self):
        self.assertEqual(classify(0.35, threshold=0.2, margin=0.1), "match")

    def test_inside_band_is_abstain(self):
        self.assertEqual(classify(0.2, threshold=0.2, margin=0.1), "abstain")
        self.assertEqual(classify(0.15, threshold=0.2, margin=0.1), "abstain")
        self.assertEqual(classify(0.25, threshold=0.2, margin=0.1), "abstain")

    def test_zero_margin_has_no_abstain_band(self):
        # Collapses to the same boundary einstein.gaps.Match.below_threshold draws.
        self.assertEqual(classify(0.19, threshold=0.2, margin=0.0), "gap")
        self.assertEqual(classify(0.2, threshold=0.2, margin=0.0), "match")
        self.assertEqual(classify(0.21, threshold=0.2, margin=0.0), "match")

    def test_negative_margin_rejected(self):
        with self.assertRaises(AssertionError):
            classify(0.2, threshold=0.2, margin=-0.01)


class ScorePairsTest(unittest.TestCase):
    def test_identical_text_scores_near_one(self):
        pairs = [
            BenchmarkPair(
                paper=_paper("p1", "quantum error correction codes", "a study of quantum error correction codes"),
                repo=_repo("r1", "quantum error correction codes", "a study of quantum error correction codes"),
                label="linked",
            )
        ]
        embedder = _fit_shared_embedder(pairs, pairs, None)
        scores = score_pairs(pairs, embedder)
        self.assertEqual(len(scores), 1)
        self.assertAlmostEqual(scores[0], 1.0, places=6)

    def test_disjoint_vocabulary_scores_zero(self):
        positive = [
            BenchmarkPair(
                paper=_paper("p1", "quantum error correction", "stabilizer codes for fault tolerant computation"),
                repo=_repo("r1", "quantum error correction", "stabilizer codes for fault tolerant computation"),
                label="linked",
            )
        ]
        negative = [
            BenchmarkPair(
                paper=_paper("p1", "quantum error correction", "stabilizer codes for fault tolerant computation"),
                repo=_repo("r2", "sourdough bread baking guide", "kneading proofing and baking sourdough loaves"),
                label="unrelated",
            )
        ]
        embedder = _fit_shared_embedder(positive, negative, None)
        scores = score_pairs(negative, embedder)
        self.assertAlmostEqual(scores[0], 0.0, places=6)

    def test_empty_pairs_returns_empty_scores(self):
        pairs = [
            BenchmarkPair(paper=_paper("p1", "a topic", "some words here"), repo=_repo("r1", "a topic", "some words here"), label="linked"),
        ]
        embedder = _fit_shared_embedder(pairs, [], None)
        self.assertEqual(score_pairs([], embedder), [])


class EvaluateTest(unittest.TestCase):
    def test_rates_computed_from_outcomes(self):
        # 2 of 4 positive scores below threshold -> FDR 0.5;
        # 3 of 4 negative scores below threshold -> flag_rate 0.75.
        report = evaluate([0.0, 0.05, 0.3, 0.4], [0.0, 0.0, 0.05, 0.3], threshold=0.1, margin=0.0)
        self.assertAlmostEqual(report.false_discovery_rate, 0.5)
        self.assertAlmostEqual(report.flag_rate, 0.75)
        self.assertEqual(report.n_positive, 4)
        self.assertEqual(report.n_negative, 4)

    def test_abstention_band_removes_scores_from_both_confident_rates(self):
        report = evaluate([0.1, 0.1, 0.1, 0.1], [0.1, 0.1, 0.1, 0.1], threshold=0.1, margin=0.05)
        self.assertEqual(report.positive_abstention_rate, 1.0)
        self.assertEqual(report.negative_abstention_rate, 1.0)
        self.assertEqual(report.false_discovery_rate, 0.0)
        self.assertEqual(report.flag_rate, 0.0)

    def test_trustworthy_requires_both_bars_cleared(self):
        good = evaluate([0.5] * 10, [0.0] * 10, threshold=0.1, margin=0.0)
        self.assertEqual(good.false_discovery_rate, 0.0)
        self.assertEqual(good.flag_rate, 1.0)
        self.assertTrue(good.trustworthy)

        bad_fdr = evaluate([0.0] * 10, [0.0] * 10, threshold=0.1, margin=0.0)
        self.assertGreater(bad_fdr.false_discovery_rate, TRUSTWORTHY_MAX_FDR)
        self.assertFalse(bad_fdr.trustworthy)

        bad_flag = evaluate([0.5] * 10, [0.5] * 10, threshold=0.1, margin=0.0)
        self.assertLess(bad_flag.flag_rate, TRUSTWORTHY_MIN_FLAG_RATE)
        self.assertFalse(bad_flag.trustworthy)

    def test_rejects_empty_arm(self):
        with self.assertRaises(AssertionError):
            evaluate([], [0.1], threshold=0.1, margin=0.0)
        with self.assertRaises(AssertionError):
            evaluate([0.1], [], threshold=0.1, margin=0.0)


class SweepTest(unittest.TestCase):
    def test_returns_one_report_per_threshold_margin_combination(self):
        positive = [
            BenchmarkPair(
                paper=_paper("p1", "quantum codes", "stabilizer quantum codes"),
                repo=_repo("r1", "quantum codes", "stabilizer quantum codes"),
                label="linked",
            ),
        ]
        negative = [
            BenchmarkPair(
                paper=_paper("p1", "quantum codes", "stabilizer quantum codes"),
                repo=_repo("r2", "sourdough bread", "kneading sourdough bread"),
                label="unrelated",
            ),
        ]
        reports = sweep(positive, negative, thresholds=[0.1, 0.2, 0.3], margins=[0.0, 0.05])
        self.assertEqual(len(reports), 6)


class LoadFixtureTest(unittest.TestCase):
    """The checked-in benchmark corpus itself -- no network, reads the fixture
    file `scripts/harvest_gap_benchmark.py` produced from live APIs once."""

    def test_loads_nonempty_arms_with_correct_labels(self):
        positive, negative = load_fixture()
        self.assertGreaterEqual(len(positive), 15)
        self.assertGreaterEqual(len(negative), 15)
        self.assertTrue(all(p.label == "linked" for p in positive))
        self.assertTrue(all(p.label == "unrelated" for p in negative))
        self.assertTrue(all(p.paper.type == "paper" and p.repo.type == "repo" for p in positive + negative))

    def test_positive_pairs_reuse_the_same_papers_as_negative_pairs(self):
        # Both arms are built from the same paper set (see harvest script) --
        # a real difference in outcome must come from the repo, not the paper.
        positive, negative = load_fixture()
        self.assertEqual({p.paper.id for p in positive}, {p.paper.id for p in negative})


class RealCorpusMeasurementTest(unittest.TestCase):
    """Locks in the actual measured numbers from the checked-in corpus as of
    this bead -- see sandbox-handoffs/einstein-0.1.md and README.md for the
    command (`uv run python -m einstein.gap_benchmark`) that reproduces this.
    If this test starts failing, the corpus or the embedder changed and the
    README's reported numbers are stale, not this test being wrong.
    """

    @classmethod
    def setUpClass(cls):
        cls.positive, cls.negative = load_fixture()

    def test_no_trustworthy_operating_point_in_swept_grid(self):
        thresholds = [round(0.01 * i, 2) for i in range(6)] + [round(0.05 * i, 2) for i in range(2, 11)]
        reports = sweep(self.positive, self.negative, thresholds=thresholds, margins=[0.0, 0.02])
        self.assertFalse(any(r.trustworthy for r in reports))

    def test_floor_false_discovery_rate_at_threshold_0_02(self):
        # 3 of 19 known paper<->repo links score exactly 0 similarity against
        # their own repo -- zero lexical overlap between abstract and README
        # -- which sets a floor no threshold above 0 can clear.
        reports = sweep(self.positive, self.negative, thresholds=[0.02], margins=[0.0])
        report = reports[0]
        self.assertAlmostEqual(report.false_discovery_rate, 3 / 19, places=4)
        self.assertAlmostEqual(report.flag_rate, 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
