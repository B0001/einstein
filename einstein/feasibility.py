"""Feasibility evaluator agent node (einstein-17): assess compute
requirements, dataset availability, and practical constraints for an idea
that survived novelty audit, and output a go/no-go verdict plus an
execution blueprint.

This is a node in the agentic loop (ideator -> novelty auditor ->
feasibility -> codegen -> sandboxed validation, see `bd show einstein-0`).
Per the bead graph, einstein-17 DEPENDS ON einstein-16 (novelty auditor)
only. einstein-18 (codegen) depends on einstein-16 too, not on this bead --
the two are siblings that both consume a passing `Audit` independently, not
a chain (see `einstein.codegen`'s own module docstring making the same
point in reverse: "this bead DEPENDS ON einstein-16 only ... not on
einstein-17"). So `evaluate_feasibility` takes an `Idea` (einstein-15) and
its `Audit` (einstein-16) directly -- same signature shape as
`einstein.codegen.generate_code` -- and asserts `audit.verdict == "pass"`
for the same reason codegen does: a force_pivot idea needs to differentiate
from prior art first, a reject idea is a candidate infringement risk, and
spending compute/data/blueprint judgment on either would be assessing the
feasibility of a proposal that is not going forward as-is. `gemini_convo.md`
draws the same edge in its own diagram: the Feasibility box only receives
the "Novel Concept" branch out of the Novelty Auditor, never the "Prior Art
Found" branch.

Unlike the novelty auditor (pure cosine-similarity math; an LLM there only
narrates a verdict the code already computed), "assess compute
requirements, dataset availability, practical constraints" has no numeric
threshold anywhere in the bead or in gemini_convo.md to compute against --
nothing in this repo measures what a method costs to run or where its
training data lives. So here the LLM performs the actual judgment, same
shape as `einstein.ideator` (a structured labeled response, deterministic
parse, `*ParseError` on a malformed reply) -- not the auditor's "LLM only
narrates a pre-computed verdict" shape, because there is no pre-computed
verdict here for it to narrate.

Response format is five labeled sections, all required, in this order:
COMPUTE / DATA / CONSTRAINTS / VERDICT / BLUEPRINT. VERDICT must be exactly
`GO` or `NO_GO` (case-insensitive); BLUEPRINT is free-form text running to
the end of the response. A response missing any section, or whose VERDICT
line is neither `GO` nor `NO_GO`, raises `FeasibilityParseError` instead of
guessing which way to round it -- same "prefer abstention to a confident
answer" reasoning `IdeationParseError` and `CodegenParseError` already
establish in this codebase, and its own exception type per this repo's
existing "one exception type per parse boundary" convention (see
`einstein.sandbox`'s `RepairParseError` for the same call).

Per this repo's standard that "a gap is a candidate until something
measured says otherwise": a "go" verdict here is the model's own
qualitative assessment from the method/problem text, not a measured fact --
no benchmark ran, no dataset was actually located, no compute was actually
provisioned. `Feasibility.note` says exactly that, so a downstream consumer
(einstein-20's report, a human reviewer) never reads "go" as "this will
work" -- the only *measured* evidence this pipeline produces is
einstein-19's sandboxed run.

Per this repo's standard for agent nodes ("the agent nodes need an LLM, and
the tests must not"): `LLMClient` (reused from `einstein.ideator`) is the
one seam a real model call goes behind; everything this module is tested on
-- prompt construction, section parsing, which idea/audit pairs get
evaluated vs. skipped, failure handling -- is deterministic and model-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

from einstein.ideator import Idea, LLMClient
from einstein.novelty_auditor import Audit

FeasibilityVerdict = Literal["go", "no_go"]
FEASIBILITY_VERDICTS: tuple[str, ...] = get_args(FeasibilityVerdict)

_COMPUTE_RE = re.compile(r"^\s*COMPUTE:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_DATA_RE = re.compile(r"^\s*DATA:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_CONSTRAINTS_RE = re.compile(r"^\s*CONSTRAINTS:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_VERDICT_RE = re.compile(r"^\s*VERDICT:\s*(GO|NO_GO)\s*$", re.IGNORECASE | re.MULTILINE)
_BLUEPRINT_RE = re.compile(r"^\s*BLUEPRINT:\s*\r?\n(.*)\Z", re.IGNORECASE | re.DOTALL | re.MULTILINE)


class FeasibilityParseError(Exception):
    """The LLM's response did not contain all five required sections, or its
    VERDICT line was neither GO nor NO_GO.

    Carries `raw_response` so a caller can log or retry with the original
    text -- this is a parse failure, not evidence the idea is infeasible.
    """

    def __init__(self, message: str, *, gap_key: str, raw_response: str) -> None:
        self.gap_key = gap_key
        self.raw_response = raw_response
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Feasibility:
    """The result of assessing one idea that survived novelty audit.

    `compute_requirement` / `dataset_availability` / `constraints` /
    `blueprint` are the model's own labeled sections, kept verbatim (not
    re-summarized) for the same provenance reason `Idea.raw_response` and
    `GeneratedCode.raw_response` are kept: a downstream consumer can see
    exactly what grounded the verdict, not just the verdict itself.
    `raw_response` is the full unparsed model output, for audit.
    """

    gap_key: str
    subject_id: str
    idea_method: str
    idea_problem: str
    verdict: FeasibilityVerdict
    compute_requirement: str
    dataset_availability: str
    constraints: str
    blueprint: str
    raw_response: str
    note: str = ""

    def __post_init__(self) -> None:
        assert self.verdict in FEASIBILITY_VERDICTS, self.verdict


def _build_prompt(idea: Idea) -> str:
    lines = [
        "You are the feasibility evaluator in an invention-ideation pipeline.",
        "This method already survived an adversarial novelty audit; your job is to",
        "assess whether actually building and running it is practical, not whether",
        "it is novel.",
        f"Candidate method: {idea.method}",
        f"Candidate problem/domain: {idea.problem}",
        "",
    ]
    if idea.context_equations:
        lines.append("Key equations from the source paper:")
        lines.extend(f"  {eq}" for eq in idea.context_equations)
        lines.append("")
    lines += [
        "Assess compute requirements, dataset availability, and practical",
        "constraints, then give a go/no-go verdict and a short execution",
        "blueprint (concrete steps to build and validate a first version).",
        "Respond with exactly these five labeled sections, in this order, and no",
        "other text:",
        "COMPUTE: <one sentence>",
        "DATA: <one sentence>",
        "CONSTRAINTS: <one sentence>",
        "VERDICT: GO or NO_GO",
        "BLUEPRINT:",
        "<the execution blueprint, one step per line>",
    ]
    return "\n".join(lines)


def evaluate_feasibility(idea: Idea, audit: Audit, *, llm: LLMClient) -> Feasibility:
    """Assess feasibility for one idea that survived novelty audit.

    Raises `AssertionError` if `audit` does not belong to `idea` (a
    `gap_key` mismatch is a caller pairing bug, not a "no-go" outcome) or if
    `audit.verdict != "pass"` (see module docstring on why only a surviving
    proposal is assessed). Raises `FeasibilityParseError` if the model's
    response is missing a required section or has an unparseable VERDICT.
    """
    assert audit.gap_key == idea.gap_key, (
        f"audit {audit.gap_key!r} does not belong to idea {idea.gap_key!r}"
    )
    assert audit.verdict == "pass", (
        "evaluate_feasibility only applies to a surviving (verdict='pass') audit, got "
        f"{audit.verdict!r} for gap {idea.gap_key!r}"
    )

    prompt = _build_prompt(idea)
    raw_response = llm.generate(prompt)

    compute_match = _COMPUTE_RE.search(raw_response)
    data_match = _DATA_RE.search(raw_response)
    constraints_match = _CONSTRAINTS_RE.search(raw_response)
    verdict_match = _VERDICT_RE.search(raw_response)
    blueprint_match = _BLUEPRINT_RE.search(raw_response)
    if not (compute_match and data_match and constraints_match and verdict_match and blueprint_match):
        raise FeasibilityParseError(
            "could not parse COMPUTE:/DATA:/CONSTRAINTS:/VERDICT:/BLUEPRINT: sections "
            f"from LLM response for gap {idea.gap_key!r}",
            gap_key=idea.gap_key,
            raw_response=raw_response,
        )

    verdict: FeasibilityVerdict = "go" if verdict_match.group(1).upper() == "GO" else "no_go"

    return Feasibility(
        gap_key=idea.gap_key,
        subject_id=idea.subject_id,
        idea_method=idea.method,
        idea_problem=idea.problem,
        verdict=verdict,
        compute_requirement=compute_match.group(1),
        dataset_availability=data_match.group(1),
        constraints=constraints_match.group(1),
        blueprint=blueprint_match.group(1).strip(),
        raw_response=raw_response,
        note=(
            "assessed by LLM from the stated method/problem text -- not measured; no "
            "compute was provisioned and no dataset was actually located -- see "
            "einstein-19 for the only measured run evidence this pipeline produces"
        ),
    )


def evaluate(
    ideas: list[Idea],
    audits: list[Audit],
    *,
    llm: LLMClient,
) -> tuple[list[Feasibility], list[str]]:
    """Evaluate feasibility for every idea whose audit survived. Returns
    `(assessments, notes)`.

    Pairs `ideas` to `audits` by `gap_key`, same as `einstein.codegen.generate`.
    An idea with no matching audit (never audited) or a matching audit that
    is not "pass" is skipped with a note rather than silently dropped or
    guessed at. A single idea's evaluation failure -- a parse failure, or
    whatever exception its own `llm.generate` raises -- is caught here and
    turned into a note, not a fabricated `Feasibility`; it does not abort
    the batch, same as `einstein.novelty_auditor.audit` and
    `einstein.codegen.generate`.
    """
    audits_by_gap_key = {a.gap_key: a for a in audits}
    assessments: list[Feasibility] = []
    notes: list[str] = []

    for idea in ideas:
        audit = audits_by_gap_key.get(idea.gap_key)
        if audit is None:
            notes.append(
                f"skipped {idea.subject_id!r} ({idea.gap_key!r}): no audit found -- "
                "not evaluated, do not treat as a surviving proposal"
            )
            continue
        if audit.verdict != "pass":
            notes.append(
                f"skipped {idea.subject_id!r} ({idea.gap_key!r}): audit verdict "
                f"{audit.verdict!r}, not a surviving proposal"
            )
            continue
        try:
            assessments.append(evaluate_feasibility(idea, audit, llm=llm))
        except Exception as exc:  # noqa: BLE001 -- see docstring above
            notes.append(f"feasibility evaluation failed for {idea.subject_id!r} ({idea.gap_key!r}): {exc!r}")

    return assessments, notes


def build_feasibility_agent_node(*, llm: LLMClient):
    """Factory for `einstein.graph.build_graph`'s `agent_node=` slot.

    Returns a callable matching `AgentNodeFn` (`AgentState -> dict`) that
    evaluates every surviving idea in `state["ideas"]`/`state["audits"]`
    and returns `{"feasibility": ..., "agent_notes": ...}`. Not wired as
    `build_graph`'s default -- same reasoning as the ideator/auditor/codegen
    nodes not being the default: chaining ideator -> auditor -> feasibility
    into one graph is an integration decision for a later bead.
    """

    def _node(state) -> dict:
        ideas = state["ideas"]
        audits = state["audits"]
        assessments, notes = evaluate(ideas, audits, llm=llm)
        summary = f"feasibility: {len(assessments)} assessment(s) from {len(audits)} audit(s)"
        return {"feasibility": assessments, "agent_notes": [summary, *notes]}

    return _node


class _StubLLM:
    """Deterministic stand-in used by this module's own self-check."""

    def generate(self, prompt: str) -> str:
        return (
            "COMPUTE: a single GPU is sufficient for a first prototype\n"
            "DATA: no public dataset exists for this exact combination; would need synthetic data\n"
            "CONSTRAINTS: numerical stability of the tensor contraction at scale is unproven\n"
            "VERDICT: GO\n"
            "BLUEPRINT:\n"
            "1. Implement a small tensor-network attention layer on a toy protein dataset.\n"
            "2. Benchmark contraction cost against a standard attention baseline.\n"
            "3. Validate structure-prediction accuracy on a held-out set.\n"
        )


def _self_check() -> None:
    idea = Idea(
        gap_key="true_invention_gap:paper:2508.00001",
        subject_id="2508.00001",
        subject_title="Tensor network contraction for attention",
        method="tensor network contraction for transformer attention",
        problem="protein folding structure prediction",
        context_equations=(r"H = \sum_i Z_i",),
        raw_response="",
    )

    def _match(against_type: str):
        from einstein.novelty_auditor import PriorArtMatch

        return PriorArtMatch(against_type=against_type, best_id=None, best_similarity=0.0, theta=0.85)

    passing_audit = Audit(
        gap_key=idea.gap_key, subject_id=idea.subject_id, idea_method=idea.method,
        idea_problem=idea.problem, verdict="pass",
        paper_match=_match("paper"), patent_match=_match("patent"),
        queried_paper_ids=(), queried_patent_ids=(),
    )

    result = evaluate_feasibility(idea, passing_audit, llm=_StubLLM())
    assert result.verdict == "go", result
    assert "GPU" in result.compute_requirement, result
    assert "1. Implement" in result.blueprint, result
    assert "not measured" in result.note, result

    rejected_audit = Audit(
        gap_key=idea.gap_key, subject_id=idea.subject_id, idea_method=idea.method,
        idea_problem=idea.problem, verdict="reject",
        paper_match=_match("paper"), patent_match=_match("patent"),
        queried_paper_ids=(), queried_patent_ids=(),
    )
    try:
        evaluate_feasibility(idea, rejected_audit, llm=_StubLLM())
        raise AssertionError("expected AssertionError for a non-pass audit")
    except AssertionError as exc:
        assert "reject" in str(exc), exc

    assessments, notes = evaluate([idea], [passing_audit], llm=_StubLLM())
    assert len(assessments) == 1 and notes == [], (assessments, notes)

    assessments, notes = evaluate([idea], [], llm=_StubLLM())
    assert assessments == [] and len(notes) == 1 and "no audit found" in notes[0], notes

    assessments, notes = evaluate([idea], [rejected_audit], llm=_StubLLM())
    assert assessments == [] and len(notes) == 1 and "reject" in notes[0], notes

    class NoGoLLM:
        def generate(self, prompt: str) -> str:
            return (
                "COMPUTE: would require a dedicated HPC cluster for months\n"
                "DATA: no dataset exists and none could plausibly be built\n"
                "CONSTRAINTS: the underlying math is not known to converge\n"
                "VERDICT: NO_GO\n"
                "BLUEPRINT:\n"
                "Not recommended without a theoretical convergence proof first.\n"
            )

    no_go = evaluate_feasibility(idea, passing_audit, llm=NoGoLLM())
    assert no_go.verdict == "no_go", no_go

    class MissingSectionLLM:
        def generate(self, prompt: str) -> str:
            return "COMPUTE: cheap\nVERDICT: GO\nBLUEPRINT:\ndo it\n"

    assessments, notes = evaluate([idea], [passing_audit], llm=MissingSectionLLM())
    assert assessments == [] and len(notes) == 1 and "feasibility evaluation failed" in notes[0], notes

    print("einstein.feasibility self-check: OK")


if __name__ == "__main__":
    _self_check()
