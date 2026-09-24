"""Tests for einstein.report: correlate gap/idea/audit/feasibility/codegen/
sandbox output by gap_key into Proposal objects, and render JSON/Markdown.
No network, no LLM -- this module never calls one (see module docstring in
einstein/report.py).
"""

import json
import unittest

from einstein.codegen import GeneratedCode
from einstein.feasibility import Feasibility
from einstein.gaps import Gap, Match
from einstein.graph import AgentState, initial_state
from einstein.ideator import Idea
from einstein.novelty_auditor import Audit, PriorArtMatch
from einstein.report import (
    Proposal,
    build_proposals,
    build_report_agent_node,
    render_json,
    render_markdown,
)
from einstein.sandbox import Attempt, RunResult, SandboxOutcome

GAP = Gap(
    kind="true_invention_gap",
    subject_type="paper",
    subject_id="2508.00001",
    subject_title="Tensor network contraction for attention",
    matches=(
        Match(against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2),
        Match(against_type="patent", best_id=None, best_similarity=0.0, threshold=0.2),
    ),
)

IDEA = Idea(
    gap_key=GAP.gap_key,
    subject_id=GAP.subject_id,
    subject_title=GAP.subject_title,
    method="tensor network contraction for transformer attention",
    problem="protein folding structure prediction",
    context_equations=(r"H = \sum_i Z_i",),
    raw_response="METHOD: ...\nPROBLEM: ...\n",
)


def _prior_art_match(against_type: str) -> PriorArtMatch:
    return PriorArtMatch(against_type=against_type, best_id=None, best_similarity=0.0, theta=0.85)


def _audit(gap_key: str = IDEA.gap_key, verdict: str = "pass") -> Audit:
    return Audit(
        gap_key=gap_key,
        subject_id=IDEA.subject_id,
        idea_method=IDEA.method,
        idea_problem=IDEA.problem,
        verdict=verdict,
        paper_match=_prior_art_match("paper"),
        patent_match=_prior_art_match("patent"),
        queried_paper_ids=("p1",),
        queried_patent_ids=(),
        note="no match >= theta=0.85 among 1 re-queried paper(s) and 0 re-queried patent(s)",
    )


def _feasibility(gap_key: str = IDEA.gap_key) -> Feasibility:
    return Feasibility(
        gap_key=gap_key,
        subject_id=IDEA.subject_id,
        idea_method=IDEA.method,
        idea_problem=IDEA.problem,
        verdict="go",
        compute_requirement="a single GPU",
        dataset_availability="none, needs synthetic data",
        constraints="numerical stability unproven",
        blueprint="1. prototype\n2. benchmark",
        raw_response="",
        note="assessed by LLM -- not measured",
    )


def _generated(gap_key: str = IDEA.gap_key) -> GeneratedCode:
    return GeneratedCode(
        gap_key=gap_key,
        subject_id=IDEA.subject_id,
        idea_method=IDEA.method,
        idea_problem=IDEA.problem,
        target_library="qiskit==2.5.2",
        code="print('hello')",
        context_equations=IDEA.context_equations,
        raw_response="",
        note="candidate reference implementation, not run or validated -- see einstein-19",
    )


def _run_result(succeeded: bool) -> RunResult:
    return RunResult(
        code="print('hello')" if succeeded else "raise RuntimeError('boom')",
        returncode=0 if succeeded else 1,
        stdout="hello\n" if succeeded else "",
        stderr="" if succeeded else "RuntimeError: boom",
        timed_out=False,
    )


def _outcome(gap_key: str = IDEA.gap_key, verdict: str = "validated") -> SandboxOutcome:
    if verdict == "validated":
        return SandboxOutcome(
            gap_key=gap_key, subject_id=IDEA.subject_id, verdict="validated",
            attempts=(Attempt(code="print('hello')", result=_run_result(True), is_repair=False),),
            final_code="print('hello')", traceback="",
            note="ran clean on the first attempt, no repair needed",
        )
    if verdict == "repaired":
        return SandboxOutcome(
            gap_key=gap_key, subject_id=IDEA.subject_id, verdict="repaired",
            attempts=(
                Attempt(code="raise RuntimeError('boom')", result=_run_result(False), is_repair=False),
                Attempt(code="print('hello')", result=_run_result(True), is_repair=True),
            ),
            final_code="print('hello')", traceback="",
            note="ran clean after 1 repair attempt(s)",
        )
    return SandboxOutcome(
        gap_key=gap_key, subject_id=IDEA.subject_id, verdict="failed",
        attempts=(Attempt(code="raise RuntimeError('boom')", result=_run_result(False), is_repair=False),),
        final_code="raise RuntimeError('boom')", traceback="RuntimeError: boom",
        note="failed after 1 attempt(s) (0 repair retries used of 2 allowed) -- see traceback, not run successfully",
    )


class BuildProposalsTest(unittest.TestCase):
    def test_full_chain_produces_one_proposal_with_every_field_populated(self):
        proposals, notes = build_proposals(
            [GAP], [IDEA], [_audit()], [_feasibility()], [_generated()], [_outcome()]
        )

        self.assertEqual(notes, [])
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertIsInstance(p, Proposal)
        self.assertEqual(p.gap_key, GAP.gap_key)
        self.assertEqual(p.subject_id, GAP.subject_id)
        self.assertEqual(p.subject_title, GAP.subject_title)
        self.assertEqual(p.gap_kind, "true_invention_gap")
        self.assertEqual(len(p.gap_matches), 2)
        self.assertEqual(p.idea_method, IDEA.method)
        self.assertEqual(p.idea_problem, IDEA.problem)
        self.assertEqual(p.idea_context_equations, IDEA.context_equations)
        self.assertEqual(p.novelty_verdict, "pass")
        self.assertIn("no match", p.novelty_note)
        self.assertEqual(p.feasibility_verdict, "go")
        self.assertIn("GPU", p.feasibility_compute)
        self.assertEqual(p.target_library, "qiskit==2.5.2")
        self.assertEqual(p.code, "print('hello')")
        self.assertEqual(p.sandbox_status, "validated")
        self.assertEqual(p.sandbox_attempts, 1)
        self.assertEqual(p.traceback, "")

    def test_repaired_and_failed_outcomes_are_both_reported_not_just_successes(self):
        second_idea = Idea(
            gap_key="unformalized_code:paper:9999999", subject_id="9999999",
            subject_title="Other", method="other method", problem="other problem",
            context_equations=(), raw_response="",
        )
        second_gap = Gap(
            kind="unformalized_code", subject_type="paper", subject_id="9999999",
            subject_title="Other",
            matches=(Match(against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2),),
        )
        proposals, notes = build_proposals(
            [GAP, second_gap],
            [IDEA, second_idea],
            [_audit(), _audit(gap_key=second_idea.gap_key)],
            [],
            [_generated(), _generated(gap_key=second_idea.gap_key)],
            [_outcome(verdict="repaired"), _outcome(gap_key=second_idea.gap_key, verdict="failed")],
        )

        self.assertEqual(notes, [])
        statuses = {p.gap_key: p.sandbox_status for p in proposals}
        self.assertEqual(statuses, {GAP.gap_key: "repaired", second_idea.gap_key: "failed"})
        failed = next(p for p in proposals if p.sandbox_status == "failed")
        self.assertEqual(failed.traceback, "RuntimeError: boom")
        self.assertEqual(failed.code, "raise RuntimeError('boom')")

    def test_feasibility_absent_yields_none_fields_not_a_fabricated_verdict(self):
        proposals, notes = build_proposals([GAP], [IDEA], [_audit()], [], [_generated()], [_outcome()])

        self.assertEqual(notes, [])
        p = proposals[0]
        self.assertIsNone(p.feasibility_verdict)
        self.assertIsNone(p.feasibility_compute)
        self.assertIsNone(p.feasibility_data)
        self.assertIsNone(p.feasibility_constraints)
        self.assertIsNone(p.feasibility_blueprint)
        self.assertIsNone(p.feasibility_note)

    def test_sandbox_outcome_with_no_matching_generated_code_is_skipped_with_a_note(self):
        proposals, notes = build_proposals([GAP], [IDEA], [_audit()], [], [], [_outcome()])

        self.assertEqual(proposals, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("generated_code", notes[0])
        self.assertIn(IDEA.subject_id, notes[0])

    def test_sandbox_outcome_with_no_matching_gap_idea_or_audit_is_skipped_with_a_note(self):
        proposals, notes = build_proposals([], [], [], [], [], [_outcome()])

        self.assertEqual(proposals, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("gap", notes[0])
        self.assertIn("idea", notes[0])
        self.assertIn("audit", notes[0])
        self.assertIn("generated_code", notes[0])

    def test_non_passing_audit_paired_with_an_outcome_is_a_pairing_bug_not_a_proposal(self):
        proposals, notes = build_proposals(
            [GAP], [IDEA], [_audit(verdict="reject")], [], [_generated()], [_outcome()]
        )

        self.assertEqual(proposals, [])
        self.assertEqual(len(notes), 1)
        self.assertIn("pairing bug", notes[0])

    def test_empty_inputs_returns_empty(self):
        proposals, notes = build_proposals([], [], [], [], [], [])
        self.assertEqual(proposals, [])
        self.assertEqual(notes, [])


class RenderJsonTest(unittest.TestCase):
    def test_output_is_valid_json_with_expected_shape(self):
        proposals, notes = build_proposals(
            [GAP], [IDEA], [_audit()], [_feasibility()], [_generated()], [_outcome()]
        )
        rendered = render_json(proposals, notes)
        parsed = json.loads(rendered)

        self.assertEqual(parsed["counts"], {"validated": 1, "repaired": 0, "failed": 0})
        self.assertEqual(len(parsed["proposals"]), 1)
        self.assertEqual(parsed["proposals"][0]["gap_key"], GAP.gap_key)
        self.assertEqual(parsed["notes"], [])
        self.assertIn("this one time", parsed["note"])

    def test_counts_reflect_mixed_statuses(self):
        second_idea = Idea(
            gap_key="unformalized_code:paper:9999999", subject_id="9999999",
            subject_title="Other", method="other method", problem="other problem",
            context_equations=(), raw_response="",
        )
        second_gap = Gap(
            kind="unformalized_code", subject_type="paper", subject_id="9999999",
            subject_title="Other",
            matches=(Match(against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2),),
        )
        proposals, _ = build_proposals(
            [GAP, second_gap],
            [IDEA, second_idea],
            [_audit(), _audit(gap_key=second_idea.gap_key)],
            [],
            [_generated(), _generated(gap_key=second_idea.gap_key)],
            [_outcome(verdict="validated"), _outcome(gap_key=second_idea.gap_key, verdict="failed")],
        )
        parsed = json.loads(render_json(proposals, []))
        self.assertEqual(parsed["counts"], {"validated": 1, "repaired": 0, "failed": 1})


class RenderMarkdownTest(unittest.TestCase):
    def test_includes_code_fence_verdicts_and_counts(self):
        proposals, notes = build_proposals(
            [GAP], [IDEA], [_audit()], [_feasibility()], [_generated()], [_outcome()]
        )
        rendered = render_markdown(proposals, notes)

        self.assertIn("# Proposal report", rendered)
        self.assertIn("```python", rendered)
        self.assertIn("print('hello')", rendered)
        self.assertIn("`validated`", rendered)
        self.assertIn("1 validated, 0 repaired, 0 failed", rendered)
        self.assertNotIn("not assessed", rendered)

    def test_missing_feasibility_is_rendered_as_not_assessed(self):
        proposals, _ = build_proposals([GAP], [IDEA], [_audit()], [], [_generated()], [_outcome()])
        rendered = render_markdown(proposals, [])
        self.assertIn("not assessed", rendered)

    def test_failed_outcome_renders_traceback(self):
        proposals, _ = build_proposals(
            [GAP], [IDEA], [_audit()], [], [_generated()], [_outcome(verdict="failed")]
        )
        rendered = render_markdown(proposals, [])
        self.assertIn("`failed`", rendered)
        self.assertIn("Traceback from the final attempt", rendered)
        self.assertIn("RuntimeError: boom", rendered)

    def test_notes_section_present_only_when_notes_exist(self):
        proposals, notes = build_proposals([GAP], [IDEA], [_audit()], [], [], [_outcome()])
        rendered = render_markdown(proposals, notes)
        self.assertIn("## Notes", rendered)

        no_notes_rendered = render_markdown([], [])
        self.assertNotIn("## Notes", no_notes_rendered)

    def test_empty_proposals_says_so_explicitly(self):
        rendered = render_markdown([], [])
        self.assertIn("no candidates reached a sandbox run", rendered)


class BuildReportAgentNodeTest(unittest.TestCase):
    def test_node_returns_proposals_and_agent_notes_matching_agent_state_shape(self):
        node = build_report_agent_node()
        state: AgentState = initial_state("quantum computing")
        state["gaps"] = [GAP]
        state["ideas"] = [IDEA]
        state["audits"] = [_audit()]
        state["feasibility"] = [_feasibility()]
        state["generated_code"] = [_generated()]
        state["sandbox_outcomes"] = [_outcome()]

        result = node(state)

        self.assertEqual(len(result["proposals"]), 1)
        self.assertIsInstance(result["proposals"][0], Proposal)
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("1 proposal(s) from 1 sandbox outcome(s)", result["agent_notes"][0])

    def test_node_on_zero_outcomes_returns_empty_proposals_and_summary_note(self):
        node = build_report_agent_node()
        state: AgentState = initial_state("quantum computing")

        result = node(state)

        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("0 proposal(s) from 0 sandbox outcome(s)", result["agent_notes"][0])


if __name__ == "__main__":
    unittest.main()
