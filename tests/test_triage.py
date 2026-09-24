"""einstein-827: discovery triage. Offline -- search is stubbed."""

import tempfile
import unittest
from pathlib import Path

from einstein.schema import Record
from einstein.triage import Candidate, harvest, render, triage


def _rec(i, title, summary=""):
    return Record(type="paper", id=f"W{i}", title=title, summary=summary,
                  url=f"https://openalex.org/W{i}", ts="2021-01-01T00:00:00+00:00", raw={})


def _search(q):
    return {"q1": [_rec(1, "Hydrogen chain energy lower bound certified"), _rec(2, "Unrelated topic")],
            "q2": [_rec(1, "Hydrogen chain energy lower bound certified"), _rec(3, "chain energy")]}[q]


class TriageTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "edge.py").write_text("")

    def tearDown(self):
        self._tmp.cleanup()

    def cand(self, **kw):
        d = dict(id="c", question="certified lower bound hydrogen chain energy",
                 if_yes="publish the bracket", if_no="retire the claim", who_acts="us",
                 edge=[{"path": "edge.py", "why": "checker"}], queries=["q1", "q2"])
        d.update(kw)
        return Candidate.from_dict(d)

    def run1(self, search=_search, **kw):
        (r,) = triage([self.cand(**kw)], search=search, root=self.root)
        return r

    def test_survivor_gets_ranked_deduplicated_evidence(self):
        r = self.run1()
        self.assertEqual(r.status, "read")
        ids = [rec.id for _, rec in r.nearest]
        self.assertEqual(ids[0], "W1")  # best match first
        self.assertEqual(sorted(ids), ["W1", "W2", "W3"])  # union of both queries, deduplicated

    def test_ranking_is_deterministic(self):
        runs = [[rec.id for _, rec in self.run1().nearest] for _ in range(3)]
        self.assertTrue(runs[0] == runs[1] == runs[2])

    def test_same_action_both_ways_has_no_stakes(self):
        r = self.run1(if_no="Publish  the bracket")
        self.assertEqual(r.status, "no_stakes")
        self.assertEqual(r.nearest, [])

    def test_missing_who_acts_has_no_stakes(self):
        self.assertEqual(self.run1(who_acts=" ").status, "no_stakes")

    def test_edge_must_exist_on_disk(self):
        r = self.run1(edge=[{"path": "nope.py"}])
        self.assertEqual(r.status, "no_edge")
        self.assertIn("nope.py", r.reasons[0])
        self.assertEqual(self.run1(edge=[]).status, "no_edge")

    def test_search_failure_fails_closed(self):
        def broken(q):
            raise TimeoutError("openalex down")

        r = self.run1(search=broken)
        self.assertEqual(r.status, "search_failed")
        self.assertIn("openalex down", r.reasons[0])

    def test_empty_search_is_not_read_as_open(self):
        r = self.run1(search=lambda q: [])
        self.assertEqual(r.status, "search_empty")

    def test_render_never_claims_novelty(self):
        md = render([self.run1()]).lower()
        self.assertNotIn("novel", md)

    def test_harvest_finds_backlog_items_and_cannot_claim_bullets(self):
        (self.root / "SPEC.md").write_text(
            "# Spec\n- done thing\n## What we cannot claim\n- no sub-mHa agreement\n"
            "## Approach\n- not a seed\n- [ ] **Open backlog item**\n")
        seeds = [t for _, t in harvest([self.root])]
        self.assertEqual(seeds, ["no sub-mHa agreement", "**Open backlog item**"])


if __name__ == "__main__":
    unittest.main()
