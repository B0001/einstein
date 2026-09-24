"""Tests for einstein.sandbox: run GeneratedCode, self-heal on failure.

No network, no real subprocess, no real LLM -- every `run` here is a fake
`RunFn` (see module docstring in einstein/sandbox.py for why `_default_run`
itself, which shells out to `uv run --with qiskit... --with qiskit-aer...`,
is never exercised by this file).
"""

import unittest

from einstein.codegen import DEFAULT_TARGET_LIBRARY, GeneratedCode
from einstein.graph import AgentState, initial_state
from einstein.sandbox import (
    DEFAULT_MAX_RETRIES,
    RepairParseError,
    RunResult,
    SandboxOutcome,
    build_sandbox_agent_node,
    self_heal,
)

GENERATED = GeneratedCode(
    gap_key="true_invention_gap:paper:2508.00001",
    subject_id="2508.00001",
    idea_method="tensor network contraction for transformer attention",
    idea_problem="protein folding structure prediction",
    target_library=DEFAULT_TARGET_LIBRARY,
    code="raise RuntimeError('boom')\n",
    context_equations=(r"H = \sum_i Z_i",),
    raw_response="",
    note="candidate reference implementation, not run or validated -- see einstein-19",
)

FIXED_RESPONSE = "```python\nprint('fixed')\n```"


class StubLLM:
    """Deterministic stand-in: returns each configured response in order,
    and records every prompt it was called with."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0)


def _ok(code: str) -> RunResult:
    return RunResult(code=code, returncode=0, stdout="ok\n", stderr="", timed_out=False)


def _fail(code: str, stderr: str = "RuntimeError: boom") -> RunResult:
    return RunResult(code=code, returncode=1, stdout="", stderr=stderr, timed_out=False)


def _timeout(code: str) -> RunResult:
    return RunResult(code=code, returncode=-9, stdout="", stderr="timed out", timed_out=True)


class RunResultTest(unittest.TestCase):
    def test_succeeded_true_on_returncode_zero_no_timeout(self):
        self.assertTrue(_ok("x").succeeded)

    def test_succeeded_false_on_nonzero_returncode(self):
        self.assertFalse(_fail("x").succeeded)

    def test_succeeded_false_when_timed_out_even_if_returncode_zero(self):
        result = RunResult(code="x", returncode=0, stdout="", stderr="", timed_out=True)
        self.assertFalse(result.succeeded)


class SelfHealFirstTryTest(unittest.TestCase):
    def test_first_attempt_success_is_validated_not_repaired(self):
        llm = StubLLM([])
        outcome = self_heal(GENERATED, llm=llm, run=_ok)

        self.assertIsInstance(outcome, SandboxOutcome)
        self.assertEqual(outcome.verdict, "validated")
        self.assertEqual(len(outcome.attempts), 1)
        self.assertFalse(outcome.attempts[0].is_repair)
        self.assertEqual(outcome.final_code, GENERATED.code)
        self.assertEqual(outcome.traceback, "")
        self.assertEqual(llm.prompts, [], "llm must not be called when the first attempt succeeds")

    def test_gap_key_and_subject_id_carried_through(self):
        outcome = self_heal(GENERATED, llm=StubLLM([]), run=_ok)
        self.assertEqual(outcome.gap_key, GENERATED.gap_key)
        self.assertEqual(outcome.subject_id, GENERATED.subject_id)


class SelfHealRepairTest(unittest.TestCase):
    def _flaky_run(self, code: str) -> RunResult:
        return _ok(code) if "fixed" in code else _fail(code)

    def test_repaired_after_one_failed_attempt(self):
        llm = StubLLM([FIXED_RESPONSE])
        outcome = self_heal(GENERATED, llm=llm, run=self._flaky_run, max_retries=2)

        self.assertEqual(outcome.verdict, "repaired")
        self.assertEqual(len(outcome.attempts), 2)
        self.assertFalse(outcome.attempts[0].is_repair)
        self.assertTrue(outcome.attempts[1].is_repair)
        self.assertEqual(outcome.final_code, "print('fixed')")
        self.assertEqual(outcome.traceback, "")
        self.assertIn("1 repair attempt", outcome.note)

    def test_repair_prompt_carries_failing_code_and_stderr_and_target_library(self):
        llm = StubLLM([FIXED_RESPONSE])
        self_heal(GENERATED, llm=llm, run=self._flaky_run, max_retries=2)

        prompt = llm.prompts[0]
        self.assertIn("boom", prompt)
        self.assertIn(GENERATED.code.strip(), prompt)
        self.assertIn(GENERATED.target_library, prompt)
        self.assertIn(GENERATED.idea_method, prompt)

    def test_second_repair_uses_the_first_repairs_stderr_not_the_original(self):
        attempts_seen: list[str] = []

        def run(code: str) -> RunResult:
            attempts_seen.append(code)
            if len(attempts_seen) < 3:
                return _fail(code, stderr=f"attempt {len(attempts_seen)} failed")
            return _ok(code)

        llm = StubLLM(["```python\nstill broken\n```", FIXED_RESPONSE])
        outcome = self_heal(GENERATED, llm=llm, run=run, max_retries=2)

        self.assertEqual(outcome.verdict, "repaired")
        self.assertIn("attempt 2 failed", llm.prompts[1])
        self.assertNotIn("attempt 1 failed", llm.prompts[1])


class SelfHealFailureTest(unittest.TestCase):
    def test_fails_after_n_retries_with_traceback_recorded(self):
        llm = StubLLM([FIXED_RESPONSE, FIXED_RESPONSE])
        outcome = self_heal(GENERATED, llm=llm, run=_fail, max_retries=2)

        self.assertEqual(outcome.verdict, "failed")
        self.assertEqual(len(outcome.attempts), 3)
        self.assertIn("boom", outcome.traceback)
        self.assertIn("2 repair retries used of 2 allowed", outcome.note)

    def test_default_max_retries_bounds_total_attempts(self):
        llm = StubLLM([FIXED_RESPONSE] * DEFAULT_MAX_RETRIES)
        outcome = self_heal(GENERATED, llm=llm, run=_fail)

        self.assertEqual(outcome.verdict, "failed")
        self.assertEqual(len(outcome.attempts), DEFAULT_MAX_RETRIES + 1)

    def test_max_retries_zero_means_exactly_one_attempt_and_no_llm_call(self):
        llm = StubLLM([])
        outcome = self_heal(GENERATED, llm=llm, run=_fail, max_retries=0)

        self.assertEqual(outcome.verdict, "failed")
        self.assertEqual(len(outcome.attempts), 1)
        self.assertEqual(llm.prompts, [])

    def test_unparseable_repair_response_stops_early_and_is_recorded_as_failed(self):
        llm = StubLLM(["no fenced code block here at all"])
        outcome = self_heal(GENERATED, llm=llm, run=_fail, max_retries=5)

        self.assertEqual(outcome.verdict, "failed")
        self.assertEqual(len(outcome.attempts), 1, "must not keep retrying past an unparseable repair")
        self.assertIn("boom", outcome.traceback)

    def test_timed_out_attempt_counts_as_failure(self):
        llm = StubLLM([FIXED_RESPONSE])
        outcome = self_heal(GENERATED, llm=llm, run=_timeout, max_retries=1)

        self.assertEqual(outcome.verdict, "failed")
        self.assertIn("timed out", outcome.traceback)


class RepairParseErrorTest(unittest.TestCase):
    def test_carries_gap_key_and_raw_response(self):
        from einstein.sandbox import _extract_repaired_code

        with self.assertRaises(RepairParseError) as ctx:
            _extract_repaired_code("no code here", gap_key="some-gap")
        self.assertEqual(ctx.exception.gap_key, "some-gap")
        self.assertIn("no code here", ctx.exception.raw_response)


class BuildSandboxAgentNodeTest(unittest.TestCase):
    def test_node_runs_every_generated_code_and_summarizes(self):
        second = GeneratedCode(
            gap_key="true_invention_gap:paper:2508.00002",
            subject_id="2508.00002",
            idea_method="other method",
            idea_problem="other problem",
            target_library=DEFAULT_TARGET_LIBRARY,
            code="print('already fine')",
            context_equations=(),
            raw_response="",
        )
        node = build_sandbox_agent_node(llm=StubLLM([]), run=_ok)
        state: AgentState = initial_state("quantum computing")
        state["generated_code"] = [GENERATED, second]

        result = node(state)

        self.assertEqual(len(result["sandbox_outcomes"]), 2)
        self.assertTrue(all(isinstance(o, SandboxOutcome) for o in result["sandbox_outcomes"]))
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("2 candidate(s) run", result["agent_notes"][0])
        self.assertIn("2 validated", result["agent_notes"][0])

    def test_node_on_empty_generated_code_returns_empty_outcomes(self):
        node = build_sandbox_agent_node(llm=StubLLM([]), run=_ok)
        state: AgentState = initial_state("quantum computing")

        result = node(state)

        self.assertEqual(result["sandbox_outcomes"], [])
        self.assertIn("0 candidate(s) run", result["agent_notes"][0])

    def test_node_counts_repaired_and_failed_separately(self):
        def run(code: str) -> RunResult:
            return _ok(code) if "fixed" in code else _fail(code)

        second = GeneratedCode(
            gap_key="true_invention_gap:paper:2508.00002",
            subject_id="2508.00002",
            idea_method="other method",
            idea_problem="other problem",
            target_library=DEFAULT_TARGET_LIBRARY,
            code="raise RuntimeError('nope')",
            context_equations=(),
            raw_response="",
        )
        llm = StubLLM([FIXED_RESPONSE, "```python\nraise RuntimeError('still nope')\n```"])
        node = build_sandbox_agent_node(llm=llm, run=run, max_retries=1)
        state: AgentState = initial_state("quantum computing")
        state["generated_code"] = [GENERATED, second]

        result = node(state)

        summary = result["agent_notes"][0]
        self.assertIn("1 repaired", summary)
        self.assertIn("1 failed", summary)


if __name__ == "__main__":
    unittest.main()
