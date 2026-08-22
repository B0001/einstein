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

    def test_adjacent_arm_included_in_shared_fit(self):
        # _fit_shared_embedder's shared-vector-space contract must cover all
        # three arms, not just positive/negative -- a term that only appears
        # in the adjacent arm still needs to be in the shared vocabulary.
        positive = [
            BenchmarkPair(paper=_paper("p1", "alpha topic", "alpha words"), repo=_repo("r1", "alpha topic", "alpha words"), label="linked"),
        ]
        negative = [
            BenchmarkPair(paper=_paper("p1", "alpha topic", "alpha words"), repo=_repo("r2", "beta topic", "beta words"), label="unrelated"),
        ]
        adjacent = [
            BenchmarkPair(paper=_paper("p1", "alpha topic", "alpha words"), repo=_repo("r3", "gamma topic", "gamma words"), label="adjacent"),
        ]
        embedder = _fit_shared_embedder(positive, negative, None, adjacent)
        # "gamma" only appears in the adjacent arm -- if it weren't in the
        # fit vocabulary, transforming it would silently drop the term.
        self.assertIn("gamma", embedder._vectorizer.vocabulary_)


class EvaluateTest(unittest.TestCase):
    def test_rates_computed_from_outcomes(self):
        # 2 of 4 positive scores below threshold -> FDR 0.5;
        # 3 of 4 negative scores below threshold -> flag_rate 0.75;
        # 1 of 4 adjacent scores below threshold -> adjacent_flag_rate 0.25.
        report = evaluate(
            [0.0, 0.05, 0.3, 0.4], [0.0, 0.0, 0.05, 0.3], [0.0, 0.3, 0.4, 0.5], threshold=0.1, margin=0.0
        )
        self.assertAlmostEqual(report.false_discovery_rate, 0.5)
        self.assertAlmostEqual(report.flag_rate, 0.75)
        self.assertAlmostEqual(report.adjacent_flag_rate, 0.25)
        self.assertEqual(report.n_positive, 4)
        self.assertEqual(report.n_negative, 4)
        self.assertEqual(report.n_adjacent, 4)

    def test_abstention_band_removes_scores_from_both_confident_rates(self):
        report = evaluate([0.1] * 4, [0.1] * 4, [0.1] * 4, threshold=0.1, margin=0.05)
        self.assertEqual(report.positive_abstention_rate, 1.0)
        self.assertEqual(report.negative_abstention_rate, 1.0)
        self.assertEqual(report.adjacent_abstention_rate, 1.0)
        self.assertEqual(report.false_discovery_rate, 0.0)
        self.assertEqual(report.flag_rate, 0.0)
        self.assertEqual(report.adjacent_flag_rate, 0.0)

    def test_trustworthy_requires_positive_and_negative_bars_only(self):
        # trustworthy is deliberately unchanged by einstein-0.4 -- it still
        # reads false_discovery_rate and flag_rate (the negative/cooking
        # arm), not adjacent_flag_rate. A bad adjacent_flag_rate does not by
        # itself flip trustworthy to False; the README states that number
        # separately instead.
        good = evaluate([0.5] * 10, [0.0] * 10, [0.5] * 10, threshold=0.1, margin=0.0)
        self.assertEqual(good.false_discovery_rate, 0.0)
        self.assertEqual(good.flag_rate, 1.0)
        self.assertEqual(good.adjacent_flag_rate, 0.0)
        self.assertTrue(good.trustworthy)

        bad_fdr = evaluate([0.0] * 10, [0.0] * 10, [0.0] * 10, threshold=0.1, margin=0.0)
        self.assertGreater(bad_fdr.false_discovery_rate, TRUSTWORTHY_MAX_FDR)
        self.assertFalse(bad_fdr.trustworthy)

        bad_flag = evaluate([0.5] * 10, [0.5] * 10, [0.0] * 10, threshold=0.1, margin=0.0)
        self.assertLess(bad_flag.flag_rate, TRUSTWORTHY_MIN_FLAG_RATE)
        self.assertFalse(bad_flag.trustworthy)

    def test_rejects_empty_arm(self):
        with self.assertRaises(AssertionError):
            evaluate([], [0.1], [0.1], threshold=0.1, margin=0.0)
        with self.assertRaises(AssertionError):
            evaluate([0.1], [], [0.1], threshold=0.1, margin=0.0)
        with self.assertRaises(AssertionError):
            evaluate([0.1], [0.1], [], threshold=0.1, margin=0.0)


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
        adjacent = [
            BenchmarkPair(
                paper=_paper("p1", "quantum codes", "stabilizer quantum codes"),
                repo=_repo("r3", "reinforcement learning gym", "policy gradient reinforcement learning"),
                label="adjacent",
            ),
        ]
        reports = sweep(positive, negative, adjacent, thresholds=[0.1, 0.2, 0.3], margins=[0.0, 0.05])
        self.assertEqual(len(reports), 6)
        self.assertTrue(all(r.n_adjacent == 1 for r in reports))


class LoadFixtureTest(unittest.TestCase):
    """The checked-in benchmark corpus itself -- no network, reads the fixture
    file `scripts/harvest_gap_benchmark.py` produced from live APIs once."""

    def test_loads_nonempty_arms_with_correct_labels(self):
        positive, negative, adjacent = load_fixture()
        self.assertGreaterEqual(len(positive), 15)
        self.assertGreaterEqual(len(negative), 15)
        self.assertGreaterEqual(len(adjacent), 15)
        self.assertTrue(all(p.label == "linked" for p in positive))
        self.assertTrue(all(p.label == "unrelated" for p in negative))
        self.assertTrue(all(p.label == "adjacent" for p in adjacent))
        self.assertTrue(
            all(p.paper.type == "paper" and p.repo.type == "repo" for p in positive + negative + adjacent)
        )

    def test_positive_pairs_reuse_the_same_papers_as_other_arms(self):
        # All three arms are built from the same paper set (see harvest
        # script) -- a real difference in outcome must come from the repo,
        # not the paper.
        positive, negative, adjacent = load_fixture()
        paper_ids = {p.paper.id for p in positive}
        self.assertEqual(paper_ids, {p.paper.id for p in negative})
        self.assertEqual(paper_ids, {p.paper.id for p in adjacent})

    def test_adjacent_repos_are_not_any_positive_pairs_implementation(self):
        # The adjacent arm's whole point is repos that are genuinely
        # unconnected -- if one happened to equal a POSITIVE_PAIRS repo, a
        # "gap" call there would be a false discovery, not a correct flag,
        # silently corrupting adjacent_flag_rate.
        positive, negative, adjacent = load_fixture()
        implementation_ids = {p.repo.id for p in positive}
        adjacent_repo_ids = {p.repo.id for p in adjacent}
        self.assertEqual(implementation_ids & adjacent_repo_ids, set())


class RealCorpusMeasurementTest(unittest.TestCase):
    """Locks in the actual measured numbers from the checked-in corpus as of
    this bead -- see sandbox-handoffs/einstein-0.1.md, sandbox-handoffs/
    einstein-0.4.md, and README.md for the command
    (`uv run python -m einstein.gap_benchmark`) that reproduces this. If this
    test starts failing, the corpus or the embedder changed and the README's
    reported numbers are stale, not this test being wrong.
    """

    @classmethod
    def setUpClass(cls):
        cls.positive, cls.negative, cls.adjacent = load_fixture()

    def test_no_trustworthy_operating_point_in_swept_grid(self):
        thresholds = [round(0.01 * i, 2) for i in range(6)] + [round(0.05 * i, 2) for i in range(2, 11)]
        reports = sweep(self.positive, self.negative, self.adjacent, thresholds=thresholds, margins=[0.0, 0.02])
        self.assertFalse(any(r.trustworthy for r in reports))

    def test_floor_false_discovery_rate_at_threshold_0_02(self):
        # 3 of 19 known paper<->repo links score exactly 0 similarity against
        # their own repo -- zero lexical overlap between abstract and README
        # -- which sets a floor no threshold above 0 can clear. At this same
        # threshold, the cooking arm is already saturated (flag_rate=1.0)
        # while the adjacent-domain arm is nowhere close (7/19 = 0.368) --
        # this is the collapse einstein-0.4 was filed to look for: the
        # cooking-only arm's "flag_rate=1.0 at threshold=0.02" was measuring
        # topic distance, not opportunity.
        reports = sweep(self.positive, self.negative, self.adjacent, thresholds=[0.02], margins=[0.0])
        report = reports[0]
        self.assertAlmostEqual(report.false_discovery_rate, 3 / 19, places=4)
        self.assertAlmostEqual(report.flag_rate, 1.0, places=4)
        self.assertAlmostEqual(report.adjacent_flag_rate, 7 / 19, places=4)

    def test_adjacent_arm_reaches_saturation_at_shipped_default_threshold(self):
        # At einstein.gaps.DEFAULT_REPO_THRESHOLD (0.2), the adjacent arm has
        # caught up to the cooking arm: both flag_rate and adjacent_flag_rate
        # are 1.0. So at the *shipped* operating point the two arms do not
        # diverge -- the divergence einstein-0.4 measured is concentrated in
        # the low-threshold region (0.02-0.04) the original einstein-0.1
        # README highlighted as the "floor FDR" zone. FDR at this threshold
        # (7/19 known implementations wrongly called a gap) is unchanged from
        # einstein-0.1 -- the positive arm and its embedder inputs did not
        # change.
        reports = sweep(self.positive, self.negative, self.adjacent, thresholds=[0.2], margins=[0.0])
        report = reports[0]
        self.assertAlmostEqual(report.false_discovery_rate, 7 / 19, places=4)
        self.assertAlmostEqual(report.flag_rate, 1.0, places=4)
        self.assertAlmostEqual(report.adjacent_flag_rate, 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
