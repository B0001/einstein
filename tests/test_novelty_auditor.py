"""Tests for einstein.novelty_auditor: adversarial re-check of an Idea
against re-queried papers and patent claims, no network and no real LLM
(see module docstring in einstein/novelty_auditor.py).
"""

import unittest

from einstein.gaps import Gap, Match
from einstein.graph import AgentState, initial_state
from einstein.ideator import Idea
from einstein.novelty_auditor import (
    Audit,
    PriorArtMatch,
    audit,
    audit_idea,
    build_novelty_auditor_agent_node,
)
from einstein.schema import Record

IDEA = Idea(
    gap_key="true_invention_gap:paper:2508.00001",
    subject_id="2508.00001",
    subject_title="Tensor network contraction for attention",
    method="tensor network contraction for transformer attention",
    problem="protein folding structure prediction",
    context_equations=(),
    raw_response="METHOD: tensor network contraction for transformer attention\nPROBLEM: protein folding structure prediction\n",
)


class StubLLM:
    """Deterministic stand-in: returns whatever text was configured, and
    records every prompt it was called with."""

    def __init__(self, response: str = "prior art overlaps substantially"):
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def _paper(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="paper", id=id_, title=title, summary=summary,
        url=f"https://arxiv.org/abs/{id_}", ts="2026-08-12T00:00:00+00:00", raw={},
    )


def _patent(id_: str, claims_text: str, title: str = "A patent") -> Record:
    return Record(
        type="patent", id=id_, title=title, summary="",
        url=f"https://patents.google.com/patent/{id_}", ts="2026-08-12T00:00:00+00:00",
        raw={"claimsText": claims_text},
    )


UNRELATED_PAPER = _paper("p1", "Kubernetes deployment pipelines", "a command line tool for managing container orchestration")
UNRELATED_PATENT = _patent("US001", "1. A fastener comprising a widget and a clip.", title="Widget fastener")

CLOSE_PAPER = _paper(
    "p2", "Tensor network contraction for transformer attention",
    "tensor network contraction for transformer attention applied to protein folding structure prediction",
)
CONFLICTING_PATENT_CLAIMS = (
    "1. A method comprising tensor network contraction for transformer attention applied "
    "to protein folding structure prediction.\n"
    "2. The method of claim 1, wherein the tensors are sparse."
)
CONFLICTING_PATENT = _patent("US002", CONFLICTING_PATENT_CLAIMS, title="Attention method")

# Only the dependent claim (claim 2) is near-verbatim; the independent claim
# (claim 1) is unrelated filler. Used to assert independent-claims-only
# comparison, not "closest claim of any kind".
DEPENDENT_ONLY_MATCH_CLAIMS = (
    "1. A widget fastener comprising a clip and a spring.\n"
    "2. The widget fastener of claim 1, further used for tensor network contraction for "
    "transformer attention applied to protein folding structure prediction."
)
DEPENDENT_ONLY_MATCH_PATENT = _patent("US003", DEPENDENT_ONLY_MATCH_CLAIMS, title="Widget with software note")


def _one(record: Record):
    return lambda query: [record]


def _none(query: str):
    return []


class AuditIdeaVerdictTest(unittest.TestCase):
    def test_pass_when_nothing_scores_above_theta(self):
        result = audit_idea(IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(UNRELATED_PATENT))

        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.rationale, "")
        self.assertIn("not a novelty claim", result.note)
        self.assertNotIn("is novel", result.note)

    def test_force_pivot_when_paper_above_theta_and_patent_below(self):
        result = audit_idea(
            IDEA, search_papers=_one(CLOSE_PAPER), search_patents=_one(UNRELATED_PATENT), theta=0.5
        )

        self.assertEqual(result.verdict, "force_pivot")
        self.assertEqual(result.paper_match.best_id, "p2")
        self.assertIsNone(result.paper_match.claim_number)

    def test_reject_when_patent_independent_claim_above_theta(self):
        result = audit_idea(
            IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(CONFLICTING_PATENT), theta=0.6
        )

        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.patent_match.best_id, "US002")
        self.assertEqual(result.patent_match.claim_number, 1)

    def test_reject_takes_precedence_over_force_pivot(self):
        # Both the paper and the patent score above theta; a legal
        # infringement risk must win over a mere prior-art crowding signal.
        result = audit_idea(
            IDEA, search_papers=_one(CLOSE_PAPER), search_patents=_one(CONFLICTING_PATENT), theta=0.5
        )

        self.assertEqual(result.verdict, "reject")

    def test_theta_boundary_is_inclusive(self):
        # Similarity == theta must count as "above" -- PriorArtMatch.above_theta uses >=.
        probe = audit_idea(IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(CONFLICTING_PATENT), theta=0.0)
        exact_theta = probe.patent_match.best_similarity

        result = audit_idea(
            IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(CONFLICTING_PATENT), theta=exact_theta
        )
        self.assertEqual(result.verdict, "reject")

    def test_only_independent_claims_are_compared_not_dependent(self):
        # The near-verbatim text lives only in claim 2, which depends on
        # claim 1 -- independent_claims_for_record must exclude it, so this
        # must NOT reject despite the dependent claim's high similarity.
        result = audit_idea(
            IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(DEPENDENT_ONLY_MATCH_PATENT), theta=0.5
        )

        self.assertEqual(result.verdict, "pass")
        # Claim 1 (independent, unrelated filler) is the only thing compared;
        # its low similarity is what "best_id" reflects here -- claim 2's
        # near-verbatim text never entered the comparison at all.
        self.assertEqual(result.patent_match.claim_number, 1)
        self.assertFalse(result.patent_match.above_theta)

    def test_no_candidates_at_all_is_a_pass_with_empty_ids(self):
        result = audit_idea(IDEA, search_papers=_none, search_patents=_none)

        self.assertEqual(result.verdict, "pass")
        self.assertEqual(result.queried_paper_ids, ())
        self.assertEqual(result.queried_patent_ids, ())
        self.assertIsNone(result.paper_match.best_id)
        self.assertIsNone(result.patent_match.best_id)

    def test_patent_with_no_claim_text_is_skipped_and_warned_not_silently_dropped(self):
        no_claims_patent = Record(
            type="patent", id="US004", title="No claims on file", summary="",
            url="https://patents.google.com/patent/US004", ts="2026-08-12T00:00:00+00:00", raw={},
        )
        result = audit_idea(IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(no_claims_patent))

        self.assertEqual(result.verdict, "pass")
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("US004", result.warnings[0])
        self.assertIn("skipped for lack of usable claim text", result.note)


class AuditIdeaRationaleTest(unittest.TestCase):
    def test_llm_not_called_on_pass(self):
        llm = StubLLM()
        audit_idea(IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(UNRELATED_PATENT), llm=llm)
        self.assertEqual(llm.prompts, [])

    def test_llm_called_with_prompt_naming_the_conflicting_claim_on_reject(self):
        llm = StubLLM("infringement risk explanation")
        result = audit_idea(
            IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(CONFLICTING_PATENT), theta=0.6, llm=llm
        )

        self.assertEqual(result.rationale, "infringement risk explanation")
        self.assertEqual(len(llm.prompts), 1)
        self.assertIn("US002", llm.prompts[0])
        self.assertIn(IDEA.method, llm.prompts[0])

    def test_no_llm_supplied_reject_still_works_with_empty_rationale(self):
        result = audit_idea(
            IDEA, search_papers=_one(UNRELATED_PAPER), search_patents=_one(CONFLICTING_PATENT), theta=0.6, llm=None
        )
        self.assertEqual(result.verdict, "reject")
        self.assertEqual(result.rationale, "")


class AuditBatchTest(unittest.TestCase):
    def test_search_functions_called_once_per_idea_with_method_as_query(self):
        queries: list[str] = []

        def recording_search(query: str) -> list[Record]:
            queries.append(query)
            return [UNRELATED_PAPER]

        second_idea = Idea(
            gap_key="true_invention_gap:paper:2508.00002", subject_id="2508.00002",
            subject_title="Other paper", method="other method", problem="other problem",
            context_equations=(), raw_response="",
        )
        audits, notes = audit(
            [IDEA, second_idea], search_papers=recording_search, search_patents=_one(UNRELATED_PATENT)
        )

        self.assertEqual(len(audits), 2)
        self.assertEqual(notes, [])
        self.assertEqual(queries, [IDEA.method, "other method"])

    def test_search_failure_for_one_idea_is_noted_not_treated_as_a_pass(self):
        def failing_search(query: str) -> list[Record]:
            raise RuntimeError("simulated transport failure")

        audits, notes = audit([IDEA], search_papers=failing_search, search_patents=_one(UNRELATED_PATENT))

        self.assertEqual(audits, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("not audited", notes[0])
        self.assertIn(IDEA.subject_id, notes[0])

    def test_one_failure_does_not_abort_the_batch(self):
        calls = {"n": 0}

        def flaky_search(query: str) -> list[Record]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated transport failure")
            return [UNRELATED_PAPER]

        second_idea = Idea(
            gap_key="true_invention_gap:paper:2508.00002", subject_id="2508.00002",
            subject_title="Other paper", method="other method", problem="other problem",
            context_equations=(), raw_response="",
        )
        audits, notes = audit([IDEA, second_idea], search_papers=flaky_search, search_patents=_one(UNRELATED_PATENT))

        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].subject_id, "2508.00002")
        self.assertEqual(len(notes), 1)

    def test_empty_ideas_returns_empty(self):
        audits, notes = audit([], search_papers=_none, search_patents=_none)
        self.assertEqual(audits, [])
        self.assertEqual(notes, [])


class BuildNoveltyAuditorAgentNodeTest(unittest.TestCase):
    def test_node_returns_audits_and_agent_notes_matching_agent_state_shape(self):
        node = build_novelty_auditor_agent_node(search_papers=_one(UNRELATED_PAPER), search_patents=_one(UNRELATED_PATENT))
        state: AgentState = initial_state("optimization")
        state["ideas"] = [IDEA]

        result = node(state)

        self.assertEqual(len(result["audits"]), 1)
        self.assertIsInstance(result["audits"][0], Audit)
        self.assertEqual(result["audits"][0].verdict, "pass")
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("1 audit(s) from 1 idea(s)", result["agent_notes"][0])

    def test_node_on_zero_ideas_returns_empty_audits_and_summary_note(self):
        node = build_novelty_auditor_agent_node(search_papers=_one(UNRELATED_PAPER), search_patents=_one(UNRELATED_PATENT))
        state: AgentState = initial_state("optimization")

        result = node(state)

        self.assertEqual(result["audits"], [])
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("0 audit(s) from 0 idea(s)", result["agent_notes"][0])


class PriorArtMatchTest(unittest.TestCase):
    def test_paper_match_rejects_a_claim_number(self):
        with self.assertRaises(AssertionError):
            PriorArtMatch(against_type="paper", best_id="p1", best_similarity=0.9, theta=0.85, claim_number=1)

    def test_invalid_against_type_rejected(self):
        with self.assertRaises(AssertionError):
            PriorArtMatch(against_type="repo", best_id=None, best_similarity=0.0, theta=0.85)

    def test_above_theta_is_inclusive(self):
        match = PriorArtMatch(against_type="paper", best_id="p1", best_similarity=0.85, theta=0.85)
        self.assertTrue(match.above_theta)


if __name__ == "__main__":
    unittest.main()
