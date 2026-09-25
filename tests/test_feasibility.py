"""Tests for einstein.feasibility: Idea + Audit -> go/no-go feasibility
assessment, no network and no real LLM (see module docstring in
einstein/feasibility.py).
"""

import unittest

from einstein.feasibility import (
    Feasibility,
    FeasibilityParseError,
    build_feasibility_agent_node,
    evaluate,
    evaluate_feasibility,
)
from einstein.graph import AgentState, initial_state
from einstein.ideator import Idea
from einstein.novelty_auditor import Audit, PriorArtMatch


class StubLLM:
    """Deterministic stand-in: returns whatever text was configured, and
    records every prompt it was called with."""

    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


IDEA = Idea(
    gap_key="true_invention_gap:paper:2508.00001",
    subject_id="2508.00001",
    subject_title="Tensor network contraction for attention",
    method="tensor network contraction for transformer attention",
    problem="protein folding structure prediction",
    context_equations=(r"H = \sum_i Z_i",),
    raw_response="",
)

GO_RESPONSE = (
    "COMPUTE: a single GPU is sufficient for a first prototype\n"
    "DATA: no public dataset exists for this exact combination; would need synthetic data\n"
    "CONSTRAINTS: numerical stability of the tensor contraction at scale is unproven\n"
    "VERDICT: GO\n"
    "BLUEPRINT:\n"
    "1. Implement a small tensor-network attention layer on a toy protein dataset.\n"
    "2. Benchmark contraction cost against a standard attention baseline.\n"
)

NO_GO_RESPONSE = (
    "COMPUTE: would require a dedicated HPC cluster for months\n"
    "DATA: no dataset exists and none could plausibly be built\n"
    "CONSTRAINTS: the underlying math is not known to converge\n"
    "VERDICT: NO_GO\n"
    "BLUEPRINT:\n"
    "Not recommended without a theoretical convergence proof first.\n"
)


def _match(against_type: str) -> PriorArtMatch:
    return PriorArtMatch(against_type=against_type, best_id=None, best_similarity=0.0, theta=0.85)


def _audit(gap_key: str = IDEA.gap_key, verdict: str = "pass") -> Audit:
    return Audit(
        gap_key=gap_key,
        subject_id=IDEA.subject_id,
        idea_method=IDEA.method,
        idea_problem=IDEA.problem,
        verdict=verdict,
        paper_match=_match("paper"),
        patent_match=_match("patent"),
        queried_paper_ids=(),
        queried_patent_ids=(),
    )


class EvaluateFeasibilityTest(unittest.TestCase):
    def test_parses_all_sections_on_a_go_verdict(self):
        result = evaluate_feasibility(IDEA, _audit(), llm=StubLLM(GO_RESPONSE))

        self.assertIsInstance(result, Feasibility)
        self.assertEqual(result.verdict, "go")
        self.assertEqual(result.gap_key, IDEA.gap_key)
        self.assertEqual(result.subject_id, IDEA.subject_id)
        self.assertIn("GPU", result.compute_requirement)
        self.assertIn("synthetic data", result.dataset_availability)
        self.assertIn("numerical stability", result.constraints)
        self.assertIn("1. Implement", result.blueprint)
        self.assertIn("2. Benchmark", result.blueprint)
        self.assertEqual(result.raw_response, GO_RESPONSE)

    def test_parses_no_go_verdict(self):
        result = evaluate_feasibility(IDEA, _audit(), llm=StubLLM(NO_GO_RESPONSE))
        self.assertEqual(result.verdict, "no_go")

    def test_verdict_parsing_is_case_insensitive(self):
        response = GO_RESPONSE.replace("VERDICT: GO", "verdict: go")
        result = evaluate_feasibility(IDEA, _audit(), llm=StubLLM(response))
        self.assertEqual(result.verdict, "go")

    def test_note_never_claims_the_assessment_is_measured(self):
        result = evaluate_feasibility(IDEA, _audit(), llm=StubLLM(GO_RESPONSE))
        self.assertIn("not measured", result.note)

    def test_prompt_carries_method_problem_and_context_equations(self):
        llm = StubLLM(GO_RESPONSE)
        evaluate_feasibility(IDEA, _audit(), llm=llm)

        self.assertIn(IDEA.method, llm.prompts[0])
        self.assertIn(IDEA.problem, llm.prompts[0])
        self.assertIn(r"H = \sum_i Z_i", llm.prompts[0])

    def test_force_pivot_audit_raises_assertion_error(self):
        with self.assertRaises(AssertionError):
            evaluate_feasibility(IDEA, _audit(verdict="force_pivot"), llm=StubLLM(GO_RESPONSE))

    def test_reject_audit_raises_assertion_error(self):
        with self.assertRaises(AssertionError):
            evaluate_feasibility(IDEA, _audit(verdict="reject"), llm=StubLLM(GO_RESPONSE))

    def test_unsearched_audit_raises_assertion_error(self):
        with self.assertRaises(AssertionError):
            evaluate_feasibility(IDEA, _audit(verdict="unsearched"), llm=StubLLM(GO_RESPONSE))

    def test_unsearched_audit_is_skipped_by_batch_with_a_note(self):
        llm = StubLLM(GO_RESPONSE)
        assessments, notes = evaluate([IDEA], [_audit(verdict="unsearched")], llm=llm)
        self.assertEqual(assessments, [])
        self.assertEqual(llm.prompts, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("unsearched", notes[0])

    def test_mismatched_gap_key_raises_assertion_error(self):
        mismatched = _audit(gap_key="true_invention_gap:paper:9999999")
        with self.assertRaises(AssertionError):
            evaluate_feasibility(IDEA, mismatched, llm=StubLLM(GO_RESPONSE))

    def test_missing_section_raises_feasibility_parse_error(self):
        broken = "COMPUTE: cheap\nVERDICT: GO\nBLUEPRINT:\ndo it\n"
        with self.assertRaises(FeasibilityParseError) as ctx:
            evaluate_feasibility(IDEA, _audit(), llm=StubLLM(broken))
        self.assertEqual(ctx.exception.gap_key, IDEA.gap_key)
        self.assertIn("do it", ctx.exception.raw_response)

    def test_unparseable_verdict_raises_feasibility_parse_error(self):
        broken = GO_RESPONSE.replace("VERDICT: GO", "VERDICT: MAYBE")
        with self.assertRaises(FeasibilityParseError):
            evaluate_feasibility(IDEA, _audit(), llm=StubLLM(broken))

    def test_missing_blueprint_section_raises_feasibility_parse_error(self):
        broken = "COMPUTE: cheap\nDATA: fine\nCONSTRAINTS: none\nVERDICT: GO\n"
        with self.assertRaises(FeasibilityParseError):
            evaluate_feasibility(IDEA, _audit(), llm=StubLLM(broken))


class EvaluateBatchTest(unittest.TestCase):
    def test_evaluates_for_passing_ideas_only(self):
        second_idea = Idea(
            gap_key="true_invention_gap:paper:2508.00002", subject_id="2508.00002",
            subject_title="Other paper", method="other method", problem="other problem",
            context_equations=(), raw_response="",
        )
        audits = [_audit(), _audit(gap_key=second_idea.gap_key, verdict="force_pivot")]

        assessments, notes = evaluate([IDEA, second_idea], audits, llm=StubLLM(GO_RESPONSE))

        self.assertEqual(len(assessments), 1)
        self.assertEqual(assessments[0].subject_id, IDEA.subject_id)
        self.assertEqual(len(notes), 1)
        self.assertIn("force_pivot", notes[0])
        self.assertIn(second_idea.subject_id, notes[0])

    def test_idea_with_no_matching_audit_is_skipped_with_a_note(self):
        assessments, notes = evaluate([IDEA], [], llm=StubLLM(GO_RESPONSE))

        self.assertEqual(assessments, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("no audit found", notes[0])
        self.assertIn(IDEA.subject_id, notes[0])

    def test_rejected_idea_is_skipped_with_a_note_not_treated_as_pass(self):
        assessments, notes = evaluate([IDEA], [_audit(verdict="reject")], llm=StubLLM(GO_RESPONSE))

        self.assertEqual(assessments, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("reject", notes[0])

    def test_parse_failure_on_one_idea_does_not_abort_the_batch(self):
        second_idea = Idea(
            gap_key="true_invention_gap:paper:2508.00002", subject_id="2508.00002",
            subject_title="Other paper", method="other method", problem="other problem",
            context_equations=(), raw_response="",
        )
        audits = [_audit(), _audit(gap_key=second_idea.gap_key)]

        calls = {"n": 0}

        class FlakyLLM:
            def generate(self, prompt: str) -> str:
                calls["n"] += 1
                return "no sections here at all" if calls["n"] == 1 else GO_RESPONSE

        assessments, notes = evaluate([IDEA, second_idea], audits, llm=FlakyLLM())

        self.assertEqual(len(assessments), 1)
        self.assertEqual(assessments[0].subject_id, second_idea.subject_id)
        self.assertEqual(len(notes), 1)
        self.assertIn("feasibility evaluation failed", notes[0])
        self.assertIn(IDEA.subject_id, notes[0])

    def test_empty_ideas_returns_empty(self):
        assessments, notes = evaluate([], [], llm=StubLLM(GO_RESPONSE))
        self.assertEqual(assessments, [])
        self.assertEqual(notes, [])


class BuildFeasibilityAgentNodeTest(unittest.TestCase):
    def test_node_returns_feasibility_and_agent_notes_matching_agent_state_shape(self):
        node = build_feasibility_agent_node(llm=StubLLM(GO_RESPONSE))
        state: AgentState = initial_state("quantum computing")
        state["ideas"] = [IDEA]
        state["audits"] = [_audit()]

        result = node(state)

        self.assertEqual(len(result["feasibility"]), 1)
        self.assertIsInstance(result["feasibility"][0], Feasibility)
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("1 assessment(s) from 1 audit(s)", result["agent_notes"][0])

    def test_node_on_zero_ideas_returns_empty_feasibility_and_summary_note(self):
        node = build_feasibility_agent_node(llm=StubLLM(GO_RESPONSE))
        state: AgentState = initial_state("quantum computing")

        result = node(state)

        self.assertEqual(result["feasibility"], [])
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("0 assessment(s) from 0 audit(s)", result["agent_notes"][0])


if __name__ == "__main__":
    unittest.main()
