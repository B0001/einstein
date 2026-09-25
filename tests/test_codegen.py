"""Tests for einstein.codegen: Idea + Audit -> candidate reference
implementation, no network and no real LLM (see module docstring in
einstein/codegen.py).
"""

import unittest

from einstein.codegen import (
    CodegenParseError,
    DEFAULT_TARGET_LIBRARY,
    GeneratedCode,
    build_codegen_agent_node,
    generate,
    generate_code,
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

WELL_FORMED_RESPONSE = (
    "```python\n"
    "from qiskit import QuantumCircuit\n\n"
    "def build_circuit() -> QuantumCircuit:\n"
    "    qc = QuantumCircuit(2)\n"
    "    qc.h(0)\n"
    "    return qc\n"
    "```"
)


def _match(against_type: str, best_similarity: float = 0.0) -> PriorArtMatch:
    return PriorArtMatch(against_type=against_type, best_id=None, best_similarity=best_similarity, theta=0.85)


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
        unsearched_against=("paper",) if verdict == "unsearched" else (),
    )


class GenerateCodeTest(unittest.TestCase):
    def test_extracts_code_from_fenced_block_and_strips_fences(self):
        result = generate_code(IDEA, _audit(), llm=StubLLM(WELL_FORMED_RESPONSE))

        self.assertIsInstance(result, GeneratedCode)
        self.assertIn("QuantumCircuit", result.code)
        self.assertNotIn("```", result.code)
        self.assertEqual(result.gap_key, IDEA.gap_key)
        self.assertEqual(result.subject_id, IDEA.subject_id)
        self.assertEqual(result.raw_response, WELL_FORMED_RESPONSE)
        self.assertEqual(result.context_equations, IDEA.context_equations)

    def test_default_target_library_is_pinned_and_stated_in_prompt(self):
        llm = StubLLM(WELL_FORMED_RESPONSE)
        result = generate_code(IDEA, _audit(), llm=llm)

        self.assertEqual(result.target_library, DEFAULT_TARGET_LIBRARY)
        self.assertIn(DEFAULT_TARGET_LIBRARY, llm.prompts[0])

    def test_custom_target_library_overrides_default_and_prompt(self):
        llm = StubLLM(WELL_FORMED_RESPONSE)
        result = generate_code(IDEA, _audit(), llm=llm, target_library="pytorch==2.9.0")

        self.assertEqual(result.target_library, "pytorch==2.9.0")
        self.assertIn("pytorch==2.9.0", llm.prompts[0])
        self.assertNotIn(DEFAULT_TARGET_LIBRARY, llm.prompts[0])

    def test_prompt_carries_context_equations(self):
        llm = StubLLM(WELL_FORMED_RESPONSE)
        generate_code(IDEA, _audit(), llm=llm)

        self.assertIn(r"H = \sum_i Z_i", llm.prompts[0])

    def test_prompt_carries_method_and_problem(self):
        llm = StubLLM(WELL_FORMED_RESPONSE)
        generate_code(IDEA, _audit(), llm=llm)

        self.assertIn(IDEA.method, llm.prompts[0])
        self.assertIn(IDEA.problem, llm.prompts[0])

    def test_note_never_claims_the_code_was_run_or_validated(self):
        result = generate_code(IDEA, _audit(), llm=StubLLM(WELL_FORMED_RESPONSE))
        self.assertIn("not run or validated", result.note)

    def test_force_pivot_audit_raises_assertion_error(self):
        with self.assertRaises(AssertionError):
            generate_code(IDEA, _audit(verdict="force_pivot"), llm=StubLLM(WELL_FORMED_RESPONSE))

    def test_reject_audit_raises_assertion_error(self):
        with self.assertRaises(AssertionError):
            generate_code(IDEA, _audit(verdict="reject"), llm=StubLLM(WELL_FORMED_RESPONSE))

    def test_unsearched_audit_raises_assertion_error(self):
        with self.assertRaises(AssertionError):
            generate_code(IDEA, _audit(verdict="unsearched"), llm=StubLLM(WELL_FORMED_RESPONSE))

    def test_unsearched_audit_is_skipped_by_batch_with_a_note(self):
        llm = StubLLM(WELL_FORMED_RESPONSE)
        generated, notes = generate([IDEA], [_audit(verdict="unsearched")], llm=llm)
        self.assertEqual(generated, [])
        self.assertEqual(llm.prompts, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("unsearched", notes[0])

    def test_mismatched_gap_key_raises_assertion_error(self):
        mismatched = _audit(gap_key="true_invention_gap:paper:9999999")
        with self.assertRaises(AssertionError):
            generate_code(IDEA, mismatched, llm=StubLLM(WELL_FORMED_RESPONSE))

    def test_response_with_no_fenced_block_raises_codegen_parse_error(self):
        with self.assertRaises(CodegenParseError) as ctx:
            generate_code(IDEA, _audit(), llm=StubLLM("here is some code: print('hi')"))
        self.assertEqual(ctx.exception.gap_key, IDEA.gap_key)
        self.assertIn("print", ctx.exception.raw_response)

    def test_non_python_fence_is_still_accepted(self):
        response = "```\nprint('no language tag')\n```"
        result = generate_code(IDEA, _audit(), llm=StubLLM(response))
        self.assertEqual(result.code, "print('no language tag')")


class GenerateBatchTest(unittest.TestCase):
    def test_generates_for_passing_ideas_only(self):
        second_idea = Idea(
            gap_key="true_invention_gap:paper:2508.00002", subject_id="2508.00002",
            subject_title="Other paper", method="other method", problem="other problem",
            context_equations=(), raw_response="",
        )
        audits = [_audit(), _audit(gap_key=second_idea.gap_key, verdict="force_pivot")]

        generated, notes = generate([IDEA, second_idea], audits, llm=StubLLM(WELL_FORMED_RESPONSE))

        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0].subject_id, IDEA.subject_id)
        self.assertEqual(len(notes), 1)
        self.assertIn("force_pivot", notes[0])
        self.assertIn(second_idea.subject_id, notes[0])

    def test_idea_with_no_matching_audit_is_skipped_with_a_note(self):
        generated, notes = generate([IDEA], [], llm=StubLLM(WELL_FORMED_RESPONSE))

        self.assertEqual(generated, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("no audit found", notes[0])
        self.assertIn(IDEA.subject_id, notes[0])

    def test_rejected_idea_is_skipped_with_a_note_not_treated_as_pass(self):
        generated, notes = generate([IDEA], [_audit(verdict="reject")], llm=StubLLM(WELL_FORMED_RESPONSE))

        self.assertEqual(generated, [])
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
                return "no fence here at all" if calls["n"] == 1 else WELL_FORMED_RESPONSE

        generated, notes = generate([IDEA, second_idea], audits, llm=FlakyLLM())

        self.assertEqual(len(generated), 1)
        self.assertEqual(generated[0].subject_id, second_idea.subject_id)
        self.assertEqual(len(notes), 1)
        self.assertIn("codegen failed", notes[0])
        self.assertIn(IDEA.subject_id, notes[0])

    def test_empty_ideas_returns_empty(self):
        generated, notes = generate([], [], llm=StubLLM(WELL_FORMED_RESPONSE))
        self.assertEqual(generated, [])
        self.assertEqual(notes, [])


class BuildCodegenAgentNodeTest(unittest.TestCase):
    def test_node_returns_generated_code_and_agent_notes_matching_agent_state_shape(self):
        node = build_codegen_agent_node(llm=StubLLM(WELL_FORMED_RESPONSE))
        state: AgentState = initial_state("quantum computing")
        state["ideas"] = [IDEA]
        state["audits"] = [_audit()]

        result = node(state)

        self.assertEqual(len(result["generated_code"]), 1)
        self.assertIsInstance(result["generated_code"][0], GeneratedCode)
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("1 implementation(s) from 1 audit(s)", result["agent_notes"][0])

    def test_node_on_zero_ideas_returns_empty_generated_code_and_summary_note(self):
        node = build_codegen_agent_node(llm=StubLLM(WELL_FORMED_RESPONSE))
        state: AgentState = initial_state("quantum computing")

        result = node(state)

        self.assertEqual(result["generated_code"], [])
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("0 implementation(s) from 0 audit(s)", result["agent_notes"][0])


if __name__ == "__main__":
    unittest.main()
