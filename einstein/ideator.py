"""Ideator agent node (einstein-15): turn a `Gap` into a structured
Method-M / Problem-P pair.

This is the first node in the agentic loop (ideator -> novelty auditor ->
feasibility -> codegen -> sandboxed validation, see `bd show einstein-0`).
Per this repo's standard for agent nodes: "the agent nodes need an LLM, and
the tests must not" -- `LLMClient` is the one small interface a real model
call goes behind (`generate(prompt) -> raw text`), and everything this
module is actually tested on -- prompt construction, response parsing,
which gaps get ideated vs. skipped, failure handling -- is deterministic and
model-free. No LLM SDK is a project dependency; a caller wires a real one in
by implementing `LLMClient`, this module never imports one.

Only `subject_type == "paper"` gaps (`true_invention_gap` /
`open_source_disruption_target`) are ideated. A `repo`-subject
`unformalized_code` gap has no Problem/Method framing to invert -- someone
already built the thing, there is no candidate method to extract from a
`Gap` alone (that would require reading the repo's own source, out of this
module's scope) -- so `ideate` skips those explicitly rather than guessing
at a Method from a title string.

Context beyond the paper's title/abstract (the extracted equations and
Limitations/Future Work sections from `einstein.arxiv_source`, einstein-7)
is optional and additive: `ideate_gap` works from `gap` alone if no
`SourceExtraction` is supplied, and folds in up to `MAX_CONTEXT_EQUATIONS`
raw-LaTeX equations and the Limitations/Future-Work text when one is.

Response format is deliberately rigid (`METHOD: ...` / `PROBLEM: ...` lines)
rather than free-form prose parsed by best-effort regex-guessing: a
malformed response raises `IdeationParseError` instead of returning a
half-populated `Idea`, per the repo's "prefer abstention to a confident
answer" standard -- a wrong guess at what the model meant is worse than
admitting parsing failed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from einstein.arxiv_source import SourceExtraction
from einstein.gaps import Gap

MAX_CONTEXT_EQUATIONS = 5

_METHOD_RE = re.compile(r"^\s*METHOD:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_PROBLEM_RE = re.compile(r"^\s*PROBLEM:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)


class LLMClient(Protocol):
    """Just enough surface for one text-in, text-out model call."""

    def generate(self, prompt: str) -> str: ...


class IdeationParseError(Exception):
    """The LLM's response did not contain a parseable METHOD:/PROBLEM: pair.

    Carries `raw_response` so a caller can log or retry with the original
    text -- this is a parse failure, not evidence the gap has no idea.
    """

    def __init__(self, message: str, *, gap_key: str, raw_response: str) -> None:
        self.gap_key = gap_key
        self.raw_response = raw_response
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Idea:
    """One structured problem-solution pair ideated from a `Gap`.

    `method` / `problem` are the parsed Method-M / Problem-P pair.
    `context_equations` is the raw LaTeX actually shown to the model (empty
    if no `SourceExtraction` was supplied) -- kept for provenance, so a
    downstream auditor can see exactly what grounded this idea.
    `raw_response` is the full unparsed model output, for audit.
    """

    gap_key: str
    subject_id: str
    subject_title: str
    method: str
    problem: str
    context_equations: tuple[str, ...]
    raw_response: str


def _build_prompt(gap: Gap, source_extraction: SourceExtraction | None) -> tuple[str, tuple[str, ...]]:
    lines = [
        "You are analyzing a candidate research gap for an invention-ideation pipeline.",
        f"Paper title: {gap.subject_title}",
        f"Gap kind: {gap.kind} (candidate only -- unmatched in this index at this threshold, not a novelty claim)",
        "",
    ]

    equations: tuple[str, ...] = ()
    if source_extraction is not None:
        equations = tuple(eq.latex for eq in source_extraction.equations[:MAX_CONTEXT_EQUATIONS])
        if equations:
            lines.append("Key equations from the paper's LaTeX source:")
            lines.extend(f"  {eq}" for eq in equations)
            lines.append("")
        if source_extraction.limitations:
            lines.append("Author-stated limitations:")
            lines.extend(f"  {s.text}" for s in source_extraction.limitations)
            lines.append("")
        if source_extraction.future_work:
            lines.append("Author-stated future work:")
            lines.extend(f"  {s.text}" for s in source_extraction.future_work)
            lines.append("")

    lines += [
        "Extract exactly one Method (the core technique, M) and one Problem",
        "(the domain or application it could newly address, P) this gap suggests.",
        "Respond with exactly two lines, no other text:",
        "METHOD: <the method, one sentence>",
        "PROBLEM: <the problem/domain it could address, one sentence>",
    ]
    return "\n".join(lines), equations


def ideate_gap(gap: Gap, *, llm: LLMClient, source_extraction: SourceExtraction | None = None) -> Idea:
    """Ideate a single paper-subject `Gap`. Raises `AssertionError` for a
    repo-subject gap (see module docstring) and `IdeationParseError` if the
    model's response cannot be parsed into a METHOD:/PROBLEM: pair.
    """
    assert gap.subject_type == "paper", (
        f"ideate_gap only applies to paper-subject gaps, got {gap.subject_type} ({gap.subject_id!r})"
    )

    prompt, equations = _build_prompt(gap, source_extraction)
    raw_response = llm.generate(prompt)

    method_match = _METHOD_RE.search(raw_response)
    problem_match = _PROBLEM_RE.search(raw_response)
    if method_match is None or problem_match is None:
        raise IdeationParseError(
            f"could not parse METHOD:/PROBLEM: pair from LLM response for gap {gap.gap_key!r}",
            gap_key=gap.gap_key,
            raw_response=raw_response,
        )

    return Idea(
        gap_key=gap.gap_key,
        subject_id=gap.subject_id,
        subject_title=gap.subject_title,
        method=method_match.group(1),
        problem=problem_match.group(1),
        context_equations=equations,
        raw_response=raw_response,
    )


def ideate(
    gaps: list[Gap],
    *,
    llm: LLMClient,
    source_extractions: dict[str, SourceExtraction] | None = None,
) -> tuple[list[Idea], list[str]]:
    """Ideate every paper-subject gap in `gaps`.

    `source_extractions` maps `Gap.subject_id` -> `SourceExtraction` for
    gaps where one was fetched (einstein-7); a gap with no entry is ideated
    from its title/kind alone. Returns `(ideas, notes)` -- `notes` records
    every gap that was skipped (non-paper subject) or failed to parse, so a
    caller building `agent_notes` never silently drops a gap with no trace.
    A single bad LLM response does not abort the batch.
    """
    source_extractions = source_extractions or {}
    ideas: list[Idea] = []
    notes: list[str] = []

    for gap in gaps:
        if gap.subject_type != "paper":
            notes.append(f"skipped {gap.subject_id!r}: ideation only applies to paper-subject gaps, got {gap.subject_type!r}")
            continue
        try:
            ideas.append(
                ideate_gap(gap, llm=llm, source_extraction=source_extractions.get(gap.subject_id))
            )
        except IdeationParseError as exc:
            notes.append(f"ideation parse failure for {gap.subject_id!r}: {exc}")

    return ideas, notes


def build_ideator_agent_node(
    llm: LLMClient,
    *,
    source_extractions: dict[str, SourceExtraction] | None = None,
):
    """Factory for `einstein.graph.build_graph`'s `agent_node=` slot.

    Returns a callable matching `AgentNodeFn` (`AgentState -> dict`) that
    ideates every gap in `state["gaps"]` and returns `{"ideas": ..., "agent_notes": ...}`.
    """

    def _node(state) -> dict:
        ideas, notes = ideate(state["gaps"], llm=llm, source_extractions=source_extractions)
        summary = f"ideator: {len(ideas)} idea(s) from {len(state['gaps'])} gap(s)"
        return {"ideas": ideas, "agent_notes": [summary, *notes]}

    return _node


class _StubLLM:
    """Deterministic stand-in used by this module's own self-check."""

    def generate(self, prompt: str) -> str:
        return "METHOD: tensor network contraction\nPROBLEM: protein folding structure prediction\n"


def _self_check() -> None:
    gap = Gap(
        kind="true_invention_gap",
        subject_type="paper",
        subject_id="2508.00001",
        subject_title="Tensor network contraction for attention",
        matches=(
            __import__("einstein.gaps", fromlist=["Match"]).Match(
                against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2
            ),
            __import__("einstein.gaps", fromlist=["Match"]).Match(
                against_type="patent", best_id=None, best_similarity=0.0, threshold=0.2
            ),
        ),
    )

    idea = ideate_gap(gap, llm=_StubLLM())
    assert idea.method == "tensor network contraction", idea
    assert idea.problem == "protein folding structure prediction", idea
    assert idea.gap_key == gap.gap_key, idea

    ideas, notes = ideate([gap], llm=_StubLLM())
    assert len(ideas) == 1 and notes == [], (ideas, notes)

    repo_gap = Gap(
        kind="unformalized_code",
        subject_type="repo",
        subject_id="owner/repo",
        subject_title="repo",
        matches=(
            __import__("einstein.gaps", fromlist=["Match"]).Match(
                against_type="paper", best_id=None, best_similarity=0.0, threshold=0.2
            ),
        ),
    )
    ideas, notes = ideate([repo_gap], llm=_StubLLM())
    assert ideas == [] and len(notes) == 1, (ideas, notes)

    print("einstein.ideator self-check: OK")


if __name__ == "__main__":
    _self_check()
