"""Tests for einstein.ideator: Gap -> structured Method/Problem pair, no
network and no real LLM (see module docstring in einstein/ideator.py).
"""

import unittest

from einstein.arxiv_source import Equation, Section, SourceExtraction
from einstein.gaps import Gap, Match
from einstein.graph import AgentState, initial_state
from einstein.ideator import (
    Idea,
    IdeationParseError,
    build_ideator_agent_node,
    ideate,
    ideate_gap,
)


class StubLLM:
    """Deterministic stand-in: returns whatever text was configured, and
    records every prompt it was called with."""

    def __init__(self, response: str):
        self.response = response
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


def _paper_gap(subject_id: str = "2508.00001", subject_title: str = "Tensor network attention") -> Gap:
    return Gap(
        kind="true_invention_gap",
        subject_type="paper",
        subject_id=subject_id,
        subject_title=subject_title,
        matches=(
            Match(against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2),
            Match(against_type="patent", best_id=None, best_similarity=0.0, threshold=0.2),
        ),
    )


def _repo_gap() -> Gap:
    return Gap(
        kind="unformalized_code",
        subject_type="repo",
        subject_id="owner/repo",
        subject_title="repo",
        matches=(Match(against_type="paper", best_id=None, best_similarity=0.0, threshold=0.2),),
    )


WELL_FORMED_RESPONSE = "METHOD: tensor network contraction\nPROBLEM: protein folding structure prediction\n"


class IdeateGapTest(unittest.TestCase):
    def test_parses_method_and_problem_from_well_formed_response(self):
        gap = _paper_gap()
        idea = ideate_gap(gap, llm=StubLLM(WELL_FORMED_RESPONSE))

        self.assertIsInstance(idea, Idea)
        self.assertEqual(idea.method, "tensor network contraction")
        self.assertEqual(idea.problem, "protein folding structure prediction")
        self.assertEqual(idea.gap_key, gap.gap_key)
        self.assertEqual(idea.subject_id, gap.subject_id)
        self.assertEqual(idea.subject_title, gap.subject_title)
        self.assertEqual(idea.raw_response, WELL_FORMED_RESPONSE)
        self.assertEqual(idea.context_equations, ())

    def test_case_insensitive_and_extra_whitespace_tolerated(self):
        response = "  method:   Foo bar  \nproblem:  Baz qux  \n"
        idea = ideate_gap(_paper_gap(), llm=StubLLM(response))

        self.assertEqual(idea.method, "Foo bar")
        self.assertEqual(idea.problem, "Baz qux")

    def test_repo_subject_gap_raises_assertion_error(self):
        with self.assertRaises(AssertionError):
            ideate_gap(_repo_gap(), llm=StubLLM(WELL_FORMED_RESPONSE))

    def test_missing_method_line_raises_ideation_parse_error(self):
        with self.assertRaises(IdeationParseError) as ctx:
            ideate_gap(_paper_gap(), llm=StubLLM("PROBLEM: only a problem, no method\n"))
        self.assertEqual(ctx.exception.gap_key, _paper_gap().gap_key)
        self.assertIn("only a problem", ctx.exception.raw_response)

    def test_missing_problem_line_raises_ideation_parse_error(self):
        with self.assertRaises(IdeationParseError):
            ideate_gap(_paper_gap(), llm=StubLLM("METHOD: only a method, no problem\n"))

    def test_freeform_prose_with_no_markers_raises_ideation_parse_error(self):
        with self.assertRaises(IdeationParseError):
            ideate_gap(_paper_gap(), llm=StubLLM("This could use tensor networks for protein folding."))

    def test_source_extraction_folds_equations_and_sections_into_prompt(self):
        extraction = SourceExtraction(
            arxiv_id="2508.00001",
            tex_filenames=("paper.tex",),
            equations=(Equation(latex=r"E = mc^2", tags=("energy",)),),
            limitations=(Section(heading="Limitations", text="Does not scale past N=100."),),
            future_work=(Section(heading="Future Work", text="Sparse variants are left for future work."),),
        )
        llm = StubLLM(WELL_FORMED_RESPONSE)
        idea = ideate_gap(_paper_gap(), llm=llm, source_extraction=extraction)

        self.assertEqual(idea.context_equations, (r"E = mc^2",))
        prompt = llm.prompts[0]
        self.assertIn(r"E = mc^2", prompt)
        self.assertIn("Does not scale past N=100.", prompt)
        self.assertIn("Sparse variants are left for future work.", prompt)

    def test_equations_truncated_to_max_context_equations(self):
        many_equations = tuple(Equation(latex=f"eq_{i}", tags=()) for i in range(10))
        extraction = SourceExtraction(
            arxiv_id="2508.00001", tex_filenames=("paper.tex",),
            equations=many_equations, limitations=(), future_work=(),
        )
        idea = ideate_gap(_paper_gap(), llm=StubLLM(WELL_FORMED_RESPONSE), source_extraction=extraction)

        self.assertEqual(len(idea.context_equations), 5)
        self.assertEqual(idea.context_equations, tuple(f"eq_{i}" for i in range(5)))

    def test_no_source_extraction_still_works_from_title_alone(self):
        idea = ideate_gap(_paper_gap(), llm=StubLLM(WELL_FORMED_RESPONSE), source_extraction=None)
        self.assertEqual(idea.context_equations, ())


class IdeateBatchTest(unittest.TestCase):
    def test_paper_gaps_are_ideated_repo_gaps_are_skipped_with_a_note(self):
        gaps = [_paper_gap("p1", "Paper one"), _repo_gap()]
        ideas, notes = ideate(gaps, llm=StubLLM(WELL_FORMED_RESPONSE))

        self.assertEqual(len(ideas), 1)
        self.assertEqual(ideas[0].subject_id, "p1")
        self.assertEqual(len(notes), 1)
        self.assertIn("owner/repo", notes[0])
        self.assertIn("skipped", notes[0])

    def test_parse_failure_on_one_gap_does_not_abort_the_batch(self):
        gaps = [_paper_gap("p1", "Paper one"), _paper_gap("p2", "Paper two")]

        calls = {"n": 0}

        class FlakyLLM:
            def generate(self, prompt: str) -> str:
                calls["n"] += 1
                return "garbage, no markers here" if calls["n"] == 1 else WELL_FORMED_RESPONSE

        ideas, notes = ideate(gaps, llm=FlakyLLM())

        self.assertEqual(len(ideas), 1)
        self.assertEqual(ideas[0].subject_id, "p2")
        self.assertEqual(len(notes), 1)
        self.assertIn("p1", notes[0])
        self.assertIn("parse failure", notes[0])

    def test_source_extractions_matched_by_subject_id(self):
        extraction = SourceExtraction(
            arxiv_id="p1", tex_filenames=("paper.tex",),
            equations=(Equation(latex="X = Y", tags=()),), limitations=(), future_work=(),
        )
        gaps = [_paper_gap("p1", "Paper one"), _paper_gap("p2", "Paper two")]

        ideas, notes = ideate(gaps, llm=StubLLM(WELL_FORMED_RESPONSE), source_extractions={"p1": extraction})
        by_id = {idea.subject_id: idea for idea in ideas}

        self.assertEqual(by_id["p1"].context_equations, ("X = Y",))
        self.assertEqual(by_id["p2"].context_equations, ())

    def test_empty_gaps_returns_empty(self):
        ideas, notes = ideate([], llm=StubLLM(WELL_FORMED_RESPONSE))
        self.assertEqual(ideas, [])
        self.assertEqual(notes, [])


class BuildIdeatorAgentNodeTest(unittest.TestCase):
    def test_node_returns_ideas_and_agent_notes_matching_agent_state_shape(self):
        node = build_ideator_agent_node(StubLLM(WELL_FORMED_RESPONSE))
        state: AgentState = initial_state("optimization")
        state["gaps"] = [_paper_gap("p1", "Paper one"), _repo_gap()]

        result = node(state)

        self.assertEqual(len(result["ideas"]), 1)
        self.assertEqual(result["ideas"][0].subject_id, "p1")
        self.assertEqual(len(result["agent_notes"]), 2)
        self.assertIn("1 idea(s) from 2 gap(s)", result["agent_notes"][0])

    def test_node_on_zero_gaps_returns_empty_ideas_and_summary_note(self):
        node = build_ideator_agent_node(StubLLM(WELL_FORMED_RESPONSE))
        state: AgentState = initial_state("optimization")

        result = node(state)

        self.assertEqual(result["ideas"], [])
        self.assertEqual(len(result["agent_notes"]), 1)
        self.assertIn("0 idea(s) from 0 gap(s)", result["agent_notes"][0])


if __name__ == "__main__":
    unittest.main()
