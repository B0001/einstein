import tempfile
import unittest
from pathlib import Path

from einstein.gaps import GAP_KINDS, Gap, Match, detect_gaps
from einstein.schema import Record
from einstein.store import Store


def _paper(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="paper",
        id=id_,
        title=title,
        summary=summary,
        url=f"https://arxiv.org/abs/{id_}",
        ts="2026-08-08T00:00:00+00:00",
        raw={},
    )


def _repo(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="repo",
        id=id_,
        title=title,
        summary=summary,
        url=f"https://github.com/{id_}",
        ts="2026-08-08T00:00:00+00:00",
        raw={},
    )


def _patent(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="patent",
        id=id_,
        title=title,
        summary=summary,
        url=f"https://patents.google.com/patent/{id_}",
        ts="2026-08-08T00:00:00+00:00",
        raw={},
    )


# Fixture corpus built so each paper/repo lands unambiguously in one bucket
# under a mid-range threshold: an "implemented" paper whose repo restates it
# near-verbatim, a "true gap" paper sharing no vocabulary with anything, a
# "disruption" paper that only a patent restates, and an "orphan" repo with
# no paper behind it.
PAPERS = [
    _paper(
        "true-gap",
        "Tensor network contraction for attention",
        "novel tensor network contraction algorithm applied to transformer attention mechanisms",
    ),
    _paper(
        "disruption",
        "Quantum annealing for portfolio optimization",
        "quantum annealing schedule for combinatorial portfolio optimization problems",
    ),
    _paper(
        "implemented",
        "Gradient descent convergence bounds",
        "convergence bounds analysis for stochastic gradient descent optimizers",
    ),
]
REPOS = [
    _repo(
        "someone/sgd-bounds",
        "sgd-bounds",
        "convergence bounds analysis for stochastic gradient descent optimizers",
    ),
    _repo(
        "someone/orphan-tool",
        "orphan-tool",
        "a command line tool for managing kubernetes deployment pipelines",
    ),
]
PATENTS = [
    _patent(
        "US001",
        "Quantum annealing portfolio method",
        "quantum annealing schedule for combinatorial portfolio optimization filed method",
    ),
]


class MatchTest(unittest.TestCase):
    def test_below_threshold(self):
        match = Match(against_type="repo", best_id="x", best_similarity=0.1, threshold=0.2)
        self.assertTrue(match.below_threshold)

    def test_at_or_above_threshold_is_not_below(self):
        match = Match(against_type="repo", best_id="x", best_similarity=0.2, threshold=0.2)
        self.assertFalse(match.below_threshold)

    def test_rejects_bad_against_type(self):
        with self.assertRaises(AssertionError):
            Match(against_type="song", best_id=None, best_similarity=0.0, threshold=0.2)


class GapTest(unittest.TestCase):
    def test_rejects_bad_kind(self):
        match = Match(against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2)
        with self.assertRaises(AssertionError):
            Gap(kind="not_a_kind", subject_type="paper", subject_id="x", subject_title="x", matches=(match,))

    def test_requires_at_least_one_match(self):
        with self.assertRaises(AssertionError):
            Gap(kind="true_invention_gap", subject_type="paper", subject_id="x", subject_title="x", matches=())

    def test_gap_key_is_stable_across_equal_gaps(self):
        match = Match(against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2)
        a = Gap(kind="unformalized_code", subject_type="repo", subject_id="x", subject_title="x", matches=(match,))
        b = Gap(kind="unformalized_code", subject_type="repo", subject_id="x", subject_title="x", matches=(match,))
        self.assertEqual(a.gap_key, b.gap_key)

    def test_gap_key_distinguishes_kind_and_subject(self):
        match = Match(against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2)
        a = Gap(kind="true_invention_gap", subject_type="paper", subject_id="x", subject_title="x", matches=(match,))
        b = Gap(
            kind="open_source_disruption_target",
            subject_type="paper",
            subject_id="x",
            subject_title="x",
            matches=(match,),
        )
        self.assertNotEqual(a.gap_key, b.gap_key)

    def test_to_payload_is_json_serializable(self):
        import json

        match = Match(against_type="repo", best_id="r1", best_similarity=0.12, threshold=0.2)
        gap = Gap(kind="true_invention_gap", subject_type="paper", subject_id="p1", subject_title="P", matches=(match,))
        json.dumps(gap.to_payload())  # raises if not serializable


class DetectGapsTest(unittest.TestCase):
    def _gaps_by_subject(self, **kwargs):
        gaps = detect_gaps(PAPERS, REPOS, PATENTS, repo_threshold=0.3, patent_threshold=0.3, **kwargs)
        return {g.subject_id: g for g in gaps}

    def test_yields_all_three_classes_on_fixture_data(self):
        by_subject = self._gaps_by_subject()
        self.assertEqual({g.kind for g in by_subject.values()}, set(GAP_KINDS))

    def test_paper_with_no_repo_or_patent_match_is_true_invention_gap(self):
        by_subject = self._gaps_by_subject()
        self.assertEqual(by_subject["true-gap"].kind, "true_invention_gap")

    def test_paper_with_no_repo_but_a_patent_match_is_open_source_disruption_target(self):
        by_subject = self._gaps_by_subject()
        self.assertEqual(by_subject["disruption"].kind, "open_source_disruption_target")

    def test_paper_with_a_repo_match_is_not_a_gap_regardless_of_patents(self):
        by_subject = self._gaps_by_subject()
        self.assertNotIn("implemented", by_subject)

    def test_repo_with_no_paper_match_is_unformalized_code(self):
        by_subject = self._gaps_by_subject()
        self.assertEqual(by_subject["someone/orphan-tool"].kind, "unformalized_code")

    def test_repo_with_a_paper_match_is_not_a_gap(self):
        by_subject = self._gaps_by_subject()
        self.assertNotIn("someone/sgd-bounds", by_subject)

    def test_true_invention_gap_carries_both_matches(self):
        by_subject = self._gaps_by_subject()
        gap = by_subject["true-gap"]
        against_types = {m.against_type for m in gap.matches}
        self.assertEqual(against_types, {"repo", "patent"})

    def test_unformalized_code_carries_paper_match(self):
        by_subject = self._gaps_by_subject()
        gap = by_subject["someone/orphan-tool"]
        self.assertEqual(gap.matches[0].against_type, "paper")

    def test_empty_repo_corpus_treats_every_low_patent_paper_as_true_invention_gap(self):
        gaps = detect_gaps(PAPERS, [], PATENTS, repo_threshold=0.3, patent_threshold=0.3)
        by_subject = {g.subject_id: g for g in gaps}
        self.assertEqual(by_subject["true-gap"].kind, "true_invention_gap")
        self.assertEqual(by_subject["implemented"].kind, "true_invention_gap")

    def test_empty_paper_corpus_makes_every_repo_unformalized(self):
        """No papers fetched means no paper can vouch for any repo -- every
        repo is `unformalized_code` in this run's index, same reasoning as
        the empty-patent-corpus case above."""
        gaps = detect_gaps([], REPOS, PATENTS, repo_threshold=0.3, patent_threshold=0.3)
        self.assertEqual({g.subject_id: g.kind for g in gaps}, {r.id: "unformalized_code" for r in REPOS})

    def test_empty_everything_yields_no_gaps(self):
        self.assertEqual(detect_gaps([], [], []), [])

    def test_rejects_wrong_record_type_in_papers(self):
        with self.assertRaises(AssertionError):
            detect_gaps(REPOS, REPOS, PATENTS)

    def test_max_threshold_makes_every_paper_a_gap(self):
        """threshold=1.0: TF-IDF cosine similarity between distinct documents
        never reaches exactly 1.0, so every paper's best match is below it."""
        gaps = detect_gaps(PAPERS, REPOS, PATENTS, repo_threshold=1.0, patent_threshold=1.0)
        subject_ids = {g.subject_id for g in gaps if g.subject_type == "paper"}
        self.assertEqual(subject_ids, {p.id for p in PAPERS})

    def test_threshold_of_zero_makes_no_paper_a_gap(self):
        gaps = detect_gaps(PAPERS, REPOS, PATENTS, repo_threshold=0.0, patent_threshold=0.0)
        subject_ids = {g.subject_id for g in gaps if g.subject_type == "paper"}
        self.assertEqual(subject_ids, set())


class DetectGapsStoreIntegrationTest(unittest.TestCase):
    """Gap records must round-trip through the Store this bead's fixture
    contract (einstein/store.py) already committed to: gap_key + kind +
    JSON payload, upsert-idempotent."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = Store(Path(self._tmpdir.name) / "einstein.db", clock=lambda: "2026-08-08T00:00:00+00:00")
        self.addCleanup(self.store.close)

    def test_detected_gaps_persist_through_store(self):
        gaps = detect_gaps(PAPERS, REPOS, PATENTS, repo_threshold=0.3, patent_threshold=0.3)
        for gap in gaps:
            self.store.upsert_gap(gap.gap_key, gap.kind, gap.to_payload())

        stored = self.store.all_gaps()
        self.assertEqual(len(stored), len(gaps))
        kinds = {row["kind"] for row in stored}
        self.assertEqual(kinds, {g.kind for g in gaps})

    def test_rerunning_detection_upserts_not_duplicates(self):
        gaps = detect_gaps(PAPERS, REPOS, PATENTS, repo_threshold=0.3, patent_threshold=0.3)
        for gap in gaps:
            self.store.upsert_gap(gap.gap_key, gap.kind, gap.to_payload())
        for gap in gaps:
            self.store.upsert_gap(gap.gap_key, gap.kind, gap.to_payload())

        self.assertEqual(len(self.store.all_gaps()), len(gaps))


if __name__ == "__main__":
    unittest.main()
