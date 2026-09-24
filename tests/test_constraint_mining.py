import unittest

from einstein.arxiv_source import Section, SourceExtraction
from einstein.constraint_mining import (
    DEFAULT_SIMILARITY_THRESHOLD,
    PainPoint,
    PainPointCluster,
    cluster_pain_points,
    pain_points_from_issues,
    pain_points_from_source,
    widespread_clusters,
)
from einstein.github_fetcher import Issue


def _extraction(arxiv_id, *, limitations=(), future_work=()):
    return SourceExtraction(
        arxiv_id=arxiv_id,
        tex_filenames=(f"{arxiv_id}.tex",),
        equations=(),
        limitations=tuple(Section(heading="Limitations", text=t) for t in limitations),
        future_work=tuple(Section(heading="Future Work", text=t) for t in future_work),
    )


def _issue(repo, number, title, body, labels=("bug",)):
    return Issue(
        repo=repo, number=number, title=title, body=body, labels=labels,
        url=f"https://github.com/{repo}/issues/{number}", ts="2026-08-08T00:00:00+00:00",
    )


class PainPointsFromSourceTest(unittest.TestCase):
    def test_limitations_and_future_work_both_extracted(self):
        extraction = _extraction(
            "2101.00001",
            limitations=("does not scale past 8192 tokens",),
            future_work=("sparse attention variants are left for future work",),
        )
        points = pain_points_from_source(extraction, title="Paper A", url="https://arxiv.org/abs/2101.00001")

        self.assertEqual(len(points), 2)
        by_kind = {p.kind: p for p in points}
        self.assertEqual(by_kind["paper_limitation"].source_id, "2101.00001")
        self.assertIn("8192 tokens", by_kind["paper_limitation"].text)
        self.assertIn("Limitations:", by_kind["paper_limitation"].text)
        self.assertEqual(by_kind["paper_future_work"].source_title, "Paper A")
        self.assertIn("sparse attention", by_kind["paper_future_work"].text)

    def test_no_sections_yields_no_pain_points(self):
        extraction = _extraction("2101.00002")
        points = pain_points_from_source(extraction, title="Paper B", url="https://arxiv.org/abs/2101.00002")
        self.assertEqual(points, [])


class PainPointsFromIssuesTest(unittest.TestCase):
    def test_maps_issue_fields(self):
        issue = _issue("someone/lib", 42, "OOM above 8k context", "Running out of memory past 8192 tokens.")
        points = pain_points_from_issues([issue])

        self.assertEqual(len(points), 1)
        point = points[0]
        self.assertEqual(point.kind, "github_issue")
        self.assertEqual(point.source_id, "someone/lib")
        self.assertIn("OOM above 8k context", point.text)
        self.assertIn("Running out of memory", point.text)
        self.assertEqual(point.url, "https://github.com/someone/lib/issues/42")

    def test_empty_body_still_yields_nonempty_text(self):
        issue = _issue("someone/lib", 1, "Title only report", "")
        point = pain_points_from_issues([issue])[0]
        self.assertEqual(point.text, "Title only report")

    def test_empty_list_yields_no_pain_points(self):
        self.assertEqual(pain_points_from_issues([]), [])


class PainPointConstructionTest(unittest.TestCase):
    def test_rejects_empty_text(self):
        with self.assertRaises(AssertionError):
            PainPoint(kind="github_issue", source_id="a/b", source_title="a/b", text="   ", url="https://x")

    def test_rejects_unknown_kind(self):
        with self.assertRaises(AssertionError):
            PainPoint(kind="not_a_kind", source_id="a/b", source_title="a/b", text="x", url="https://x")


class ClusterPainPointsTest(unittest.TestCase):
    """einstein-9 acceptance: a paper's limitation and a GitHub issue,
    reporting the same underlying friction in different words, cluster
    together and the resulting cluster is flagged widespread (>1 distinct
    source) -- the "cluster pain points across repos/papers to find
    widespread bottlenecks" acceptance for this bead.
    """

    def setUp(self):
        self.paper_limit = PainPoint(
            kind="paper_limitation",
            source_id="2101.00001",
            source_title="Paper A",
            text="The model cannot process sequences longer than 8192 tokens and runs out of memory beyond that context length.",
            url="https://arxiv.org/abs/2101.00001",
        )
        self.matching_issue = PainPoint(
            kind="github_issue",
            source_id="someone/transformer-lib",
            source_title="someone/transformer-lib",
            text="OOM above 8k context\nRunning out of memory whenever the input sequence exceeds 8192 tokens, same context length limit as the paper.",
            url="https://github.com/someone/transformer-lib/issues/42",
        )
        self.unrelated = PainPoint(
            kind="github_issue",
            source_id="someone/other-lib",
            source_title="someone/other-lib",
            text="Unrelated formatting bug: the CLI --help output has a typo in the usage string.",
            url="https://github.com/someone/other-lib/issues/7",
        )

    def test_similar_pain_points_across_sources_form_a_widespread_cluster(self):
        clusters = cluster_pain_points(
            [self.paper_limit, self.matching_issue, self.unrelated], similarity_threshold=0.15
        )
        widespread = widespread_clusters(clusters)

        self.assertEqual(len(widespread), 1)
        cluster = widespread[0]
        self.assertEqual(cluster.source_ids, frozenset({"2101.00001", "someone/transformer-lib"}))
        self.assertTrue(cluster.is_widespread)

    def test_unrelated_pain_point_is_excluded_from_widespread(self):
        clusters = cluster_pain_points(
            [self.paper_limit, self.matching_issue, self.unrelated], similarity_threshold=0.15
        )
        widespread_source_ids = {sid for c in widespread_clusters(clusters) for sid in c.source_ids}
        self.assertNotIn("someone/other-lib", widespread_source_ids)

    def test_high_threshold_yields_only_singletons(self):
        clusters = cluster_pain_points(
            [self.paper_limit, self.matching_issue, self.unrelated], similarity_threshold=0.99
        )
        self.assertEqual(len(clusters), 3)
        self.assertEqual(widespread_clusters(clusters), [])

    def test_every_pain_point_appears_in_exactly_one_cluster(self):
        points = [self.paper_limit, self.matching_issue, self.unrelated]
        clusters = cluster_pain_points(points, similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD)
        total_members = sum(len(c.members) for c in clusters)
        self.assertEqual(total_members, len(points))

    def test_empty_input_yields_no_clusters(self):
        self.assertEqual(cluster_pain_points([]), [])

    def test_single_pain_point_yields_one_singleton_cluster(self):
        clusters = cluster_pain_points([self.paper_limit])
        self.assertEqual(len(clusters), 1)
        self.assertFalse(clusters[0].is_widespread)

    def test_cluster_rejects_empty_members(self):
        with self.assertRaises(AssertionError):
            PainPointCluster(members=(), similarity_threshold=0.3)

    def test_to_payload_is_json_serializable_shape(self):
        clusters = cluster_pain_points(
            [self.paper_limit, self.matching_issue], similarity_threshold=0.15
        )
        payload = clusters[0].to_payload()
        self.assertIn("is_widespread", payload)
        self.assertIn("source_ids", payload)
        self.assertIsInstance(payload["members"], list)


if __name__ == "__main__":
    unittest.main()
