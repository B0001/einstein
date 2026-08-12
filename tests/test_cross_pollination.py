import unittest

from einstein.cross_pollination import (
    DEFAULT_SIMILARITY_THRESHOLD,
    CrossPollinationCandidate,
    detect_cross_pollination,
)
from einstein.gaps import Match
from einstein.openalex_fetcher import Edge
from einstein.schema import Record


def _paper(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="paper", id=id_, title=title, summary=summary,
        url=f"https://openalex.org/{id_}", ts="2026-08-08T00:00:00+00:00", raw={},
    )


def _repo(id_: str) -> Record:
    return Record(
        type="repo", id=id_, title=id_, summary="", url=f"https://github.com/{id_}",
        ts="2026-08-08T00:00:00+00:00", raw={},
    )


# Fixture built so tensor-network vocabulary overlaps heavily between a
# "method" paper and a "domain" paper, and gradient-boosting vocabulary
# overlaps between a different method/domain pair -- each pair unambiguously
# closest to the other under TF-IDF cosine similarity.
METHOD_TENSOR = _paper(
    "Wmethod-tensor",
    "Tensor network contraction algorithms",
    "efficient tensor network contraction algorithms for simulating quantum circuits at scale",
)
METHOD_BOOSTING = _paper(
    "Wmethod-boosting",
    "Gradient boosted decision trees",
    "gradient boosted decision tree ensembles for structured tabular prediction tasks",
)
DOMAIN_PROTEIN = _paper(
    "Wdomain-protein",
    "Tensor network methods for protein folding",
    "applying tensor network contraction algorithms to protein folding structure prediction",
)
DOMAIN_SCHEDULING = _paper(
    "Wdomain-scheduling",
    "Gradient boosted models for job scheduling",
    "gradient boosted decision tree ensembles for predicting container job scheduling latency",
)
UNRELATED_DOMAIN = _paper(
    "Wdomain-unrelated",
    "A history of Byzantine coinage",
    "numismatic survey of Byzantine-era coin minting practices across three centuries",
)


class DetectCrossPollinationTest(unittest.TestCase):
    def test_close_pair_with_no_edge_is_a_candidate(self):
        candidates = detect_cross_pollination(
            [METHOD_TENSOR], [DOMAIN_PROTEIN, UNRELATED_DOMAIN], edges=[], similarity_threshold=0.1
        )

        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertIsInstance(candidate, CrossPollinationCandidate)
        self.assertEqual(candidate.method_id, "Wmethod-tensor")
        self.assertEqual(candidate.match.best_id, "Wdomain-protein")
        self.assertGreaterEqual(candidate.match.best_similarity, 0.1)

    def test_existing_citation_edge_suppresses_the_candidate(self):
        edges = [Edge(from_id="Wmethod-tensor", to_id="Wdomain-protein")]

        candidates = detect_cross_pollination(
            [METHOD_TENSOR], [DOMAIN_PROTEIN, UNRELATED_DOMAIN], edges, similarity_threshold=0.1
        )

        self.assertEqual(candidates, [])

    def test_edge_direction_does_not_matter(self):
        edges = [Edge(from_id="Wdomain-protein", to_id="Wmethod-tensor")]

        candidates = detect_cross_pollination(
            [METHOD_TENSOR], [DOMAIN_PROTEIN, UNRELATED_DOMAIN], edges, similarity_threshold=0.1
        )

        self.assertEqual(candidates, [])

    def test_below_threshold_is_not_a_candidate(self):
        candidates = detect_cross_pollination(
            [METHOD_TENSOR], [UNRELATED_DOMAIN], edges=[], similarity_threshold=0.9
        )

        self.assertEqual(candidates, [])

    def test_multiple_independent_method_domain_pairs(self):
        candidates = detect_cross_pollination(
            [METHOD_TENSOR, METHOD_BOOSTING],
            [DOMAIN_PROTEIN, DOMAIN_SCHEDULING, UNRELATED_DOMAIN],
            edges=[],
            similarity_threshold=0.1,
        )

        by_method = {c.method_id: c.match.best_id for c in candidates}
        self.assertEqual(by_method.get("Wmethod-tensor"), "Wdomain-protein")
        self.assertEqual(by_method.get("Wmethod-boosting"), "Wdomain-scheduling")

    def test_edge_only_suppresses_the_specific_matched_pair(self):
        # Method has an edge to the unrelated domain, not to its actual
        # closest match -- must still surface as a candidate.
        edges = [Edge(from_id="Wmethod-tensor", to_id="Wdomain-unrelated")]

        candidates = detect_cross_pollination(
            [METHOD_TENSOR], [DOMAIN_PROTEIN, UNRELATED_DOMAIN], edges, similarity_threshold=0.1
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].match.best_id, "Wdomain-protein")

    def test_paper_never_matched_to_itself(self):
        candidates = detect_cross_pollination(
            [METHOD_TENSOR], [METHOD_TENSOR], edges=[], similarity_threshold=0.0
        )

        self.assertEqual(candidates, [])

    def test_empty_methods_or_domains_returns_empty(self):
        self.assertEqual(detect_cross_pollination([], [DOMAIN_PROTEIN], edges=[]), [])
        self.assertEqual(detect_cross_pollination([METHOD_TENSOR], [], edges=[]), [])

    def test_non_paper_record_in_methods_raises(self):
        with self.assertRaises(AssertionError):
            detect_cross_pollination([_repo("owner/repo")], [DOMAIN_PROTEIN], edges=[])

    def test_non_paper_record_in_domains_raises(self):
        with self.assertRaises(AssertionError):
            detect_cross_pollination([METHOD_TENSOR], [_repo("owner/repo")], edges=[])

    def test_default_threshold_is_importable_and_used_when_unspecified(self):
        candidates = detect_cross_pollination([METHOD_TENSOR], [DOMAIN_PROTEIN, UNRELATED_DOMAIN], edges=[])
        for c in candidates:
            self.assertGreaterEqual(c.match.best_similarity, DEFAULT_SIMILARITY_THRESHOLD)


class CrossPollinationCandidateTest(unittest.TestCase):
    def test_requires_paper_match_with_a_matched_id(self):
        with self.assertRaises(AssertionError):
            CrossPollinationCandidate(
                method_id="m1",
                method_title="Method",
                match=Match(against_type="repo", best_id="x", best_similarity=0.5, threshold=0.1),
            )
        with self.assertRaises(AssertionError):
            CrossPollinationCandidate(
                method_id="m1",
                method_title="Method",
                match=Match(against_type="paper", best_id=None, best_similarity=0.5, threshold=0.1),
            )

    def test_to_payload_round_trips_fields(self):
        candidate = CrossPollinationCandidate(
            method_id="m1",
            method_title="Method",
            match=Match(against_type="paper", best_id="d1", best_similarity=0.42, threshold=0.1),
        )
        payload = candidate.to_payload()
        self.assertEqual(payload["method_id"], "m1")
        self.assertEqual(payload["match"]["best_id"], "d1")
        self.assertEqual(payload["match"]["best_similarity"], 0.42)


if __name__ == "__main__":
    unittest.main()
