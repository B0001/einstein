"""Proposal report output (einstein-20): correlate every stage of the
agentic loop -- gap (einstein-3) -> idea (einstein-15) -> novelty audit
(einstein-16) -> feasibility (einstein-17, optional) -> codegen (einstein-18)
-> sandbox self-heal (einstein-19) -- by `gap_key` into one `Proposal` per
candidate that actually reached a sandbox run, and render the result as
structured JSON and Markdown.

This module does not run anything and does not call an LLM. It is pure
aggregation and rendering over objects the earlier stages already produced.
There is no `LLMClient` parameter anywhere in this file -- unlike every
other agent-node module in this pipeline, a report has nothing to narrate
that the upstream stages haven't already narrated.

What counts as a reportable "proposal", and what does not
------------------------------------------------------------
Per the bead's own list -- "gap classification, novelty verdict,
feasibility, working code, run evidence" -- a `Proposal` requires actual run
evidence. A `GeneratedCode` that was never handed to `einstein.sandbox.
self_heal` has no run evidence and is not "working code", just a candidate
string nobody has executed -- so `build_proposals` builds one `Proposal` per
`SandboxOutcome`, not per `GeneratedCode` or per surviving `Audit`. A
`GeneratedCode`/`Idea`/`Audit`/`Gap` that never reached the sandbox stage
is absent from `proposals` and recorded in `notes` instead (same "note,
don't silently drop" pattern every prior stage in this pipeline uses) --
never fabricated into a `Proposal` with an invented run outcome.

The bead's title says "validated proposals" (singular framing), but this
module does NOT filter out sandbox failures. `Proposal.sandbox_status`
carries `"validated"` / `"repaired"` / `"failed"` verbatim from
`SandboxOutcome.verdict`, and a `"failed"` proposal is rendered with exactly
as much detail (including the real traceback) as a successful one. Quietly
quietly dropping the failures would violate this repo's own standard --
"if you measure the pipeline and the result is bad, report the bad
number" -- for exactly the artifact a human is most likely to read end to
end. "Validated" is read here as "went through the validation pipeline",
not as a filter that hides what that pipeline found.

Feasibility (einstein-17) is optional, not missing-by-bug: `einstein.
feasibility`'s own module docstring establishes that feasibility and
codegen are *siblings* that both consume a passing `Audit` independently,
not a chain -- a caller may have run codegen/sandbox without ever running
feasibility for the same idea. `Proposal.feasibility_verdict` and its
sibling fields are `None`, not a fabricated "not assessed" verdict value,
when no matching `Feasibility` exists for that `gap_key`.

Every field in `Proposal` that carries forward a prior stage's own "this is
not a claim" language is copied verbatim from that stage (`novelty_note`
from `Audit.note`, `feasibility_note` from `Feasibility.note`,
`sandbox_note` from `SandboxOutcome.note`) rather than re-summarized --
paraphrasing a caveat risks losing the caveat.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Literal, get_args

from einstein.codegen import GeneratedCode
from einstein.feasibility import Feasibility
from einstein.gaps import Gap
from einstein.ideator import Idea
from einstein.novelty_auditor import Audit
from einstein.sandbox import SandboxOutcome

SandboxStatus = Literal["validated", "repaired", "failed"]
SANDBOX_STATUSES: tuple[str, ...] = get_args(SandboxStatus)

REPORT_NOTE = (
    "Each proposal below is a candidate that survived an adversarial novelty audit "
    "and was actually run in a sandboxed subprocess against a pinned library "
    "version -- see each proposal's novelty_note/sandbox_note for exactly what was "
    "searched and run, and what was not. 'validated'/'repaired' means the code "
    "executed without raising, this one time, against this one pinned version; it "
    "is not a correctness, performance, or novelty claim. 'failed' means it did "
    "not run clean after every repair attempt -- see traceback. Nothing in this "
    "report is 'novel' or 'unprecedented'; every absence-of-prior-art statement is "
    "scoped to the index, sources, and threshold that produced it."
)


@dataclass(frozen=True, slots=True)
class Proposal:
    """One candidate that was ideated, audited, (optionally) assessed for
    feasibility, codegen'd, and run through the sandbox self-heal loop.

    See module docstring for why this is built per-`SandboxOutcome`, why a
    `"failed"` sandbox status is still reported in full, and why the
    `feasibility_*` fields are `None` (not a fabricated verdict) when no
    `Feasibility` was ever run for this `gap_key`.
    """

    gap_key: str
    subject_type: str
    subject_id: str
    subject_title: str
    gap_kind: str
    gap_matches: tuple[dict, ...]
    idea_method: str
    idea_problem: str
    idea_context_equations: tuple[str, ...]
    novelty_verdict: str
    novelty_note: str
    novelty_rationale: str
    feasibility_verdict: str | None
    feasibility_compute: str | None
    feasibility_data: str | None
    feasibility_constraints: str | None
    feasibility_blueprint: str | None
    feasibility_note: str | None
    target_library: str
    code: str
    codegen_note: str
    sandbox_status: SandboxStatus
    sandbox_attempts: int
    sandbox_note: str
    traceback: str
    note: str = REPORT_NOTE

    def __post_init__(self) -> None:
        assert self.sandbox_status in SANDBOX_STATUSES, self.sandbox_status

    def to_payload(self) -> dict:
        """JSON-serializable payload -- every field is already a primitive,
        tuple, or `None`, so plain `dataclasses.asdict` is sufficient."""
        return asdict(self)


def _build_proposal(
    outcome: SandboxOutcome,
    *,
    gap: Gap,
    idea: Idea,
    audit: Audit,
    generated: GeneratedCode,
    feasibility: Feasibility | None,
) -> Proposal:
    assert idea.gap_key == outcome.gap_key, (idea.gap_key, outcome.gap_key)
    assert audit.gap_key == outcome.gap_key, (audit.gap_key, outcome.gap_key)
    assert generated.gap_key == outcome.gap_key, (generated.gap_key, outcome.gap_key)
    assert audit.verdict == "pass", (
        f"proposal for {outcome.gap_key!r} pairs a sandbox outcome with a non-passing "
        f"audit ({audit.verdict!r}) -- this is a caller pairing bug, not a real chain "
        "(only a passing audit's idea is ever codegen'd, see einstein.codegen)"
    )
    if feasibility is not None:
        assert feasibility.gap_key == outcome.gap_key, (feasibility.gap_key, outcome.gap_key)

    return Proposal(
        gap_key=outcome.gap_key,
        subject_type=gap.subject_type,
        subject_id=gap.subject_id,
        subject_title=gap.subject_title,
        gap_kind=gap.kind,
        gap_matches=tuple(gap.to_payload()["matches"]),
        idea_method=idea.method,
        idea_problem=idea.problem,
        idea_context_equations=idea.context_equations,
        novelty_verdict=audit.verdict,
        novelty_note=audit.note,
        novelty_rationale=audit.rationale,
        feasibility_verdict=feasibility.verdict if feasibility else None,
        feasibility_compute=feasibility.compute_requirement if feasibility else None,
        feasibility_data=feasibility.dataset_availability if feasibility else None,
        feasibility_constraints=feasibility.constraints if feasibility else None,
        feasibility_blueprint=feasibility.blueprint if feasibility else None,
        feasibility_note=feasibility.note if feasibility else None,
        target_library=generated.target_library,
        code=outcome.final_code,
        codegen_note=generated.note,
        sandbox_status=outcome.verdict,  # type: ignore[arg-type]
        sandbox_attempts=len(outcome.attempts),
        sandbox_note=outcome.note,
        traceback=outcome.traceback,
    )


def build_proposals(
    gaps: list[Gap],
    ideas: list[Idea],
    audits: list[Audit],
    feasibility: list[Feasibility],
    generated_code: list[GeneratedCode],
    sandbox_outcomes: list[SandboxOutcome],
) -> tuple[list[Proposal], list[str]]:
    """Correlate every stage's output by `gap_key` into `(proposals, notes)`.

    One `Proposal` per entry in `sandbox_outcomes` whose full chain (`Gap`,
    `Idea`, `Audit`, `GeneratedCode`) can be found by `gap_key` -- see module
    docstring for why the sandbox outcome, not the audit or the generated
    code, is what this function iterates over. `feasibility` is looked up
    the same way but is never required; a missing match yields `None`
    fields on the `Proposal`, not a skip. A `SandboxOutcome` missing any
    required piece of its chain is skipped and recorded in `notes` with
    exactly which piece was missing -- never fabricated. A single bad
    pairing (a caller-supplied audit/idea/generated_code that doesn't
    actually belong to this gap_key, or a non-passing audit) is caught and
    turned into a note rather than aborting the batch, same as every
    upstream stage in this pipeline.
    """
    gaps_by_key = {g.gap_key: g for g in gaps}
    ideas_by_key = {i.gap_key: i for i in ideas}
    audits_by_key = {a.gap_key: a for a in audits}
    feasibility_by_key = {f.gap_key: f for f in feasibility}
    generated_by_key = {c.gap_key: c for c in generated_code}

    proposals: list[Proposal] = []
    notes: list[str] = []

    for outcome in sandbox_outcomes:
        key = outcome.gap_key
        gap = gaps_by_key.get(key)
        idea = ideas_by_key.get(key)
        audit = audits_by_key.get(key)
        generated = generated_by_key.get(key)

        missing = [
            name
            for name, value in (("gap", gap), ("idea", idea), ("audit", audit), ("generated_code", generated))
            if value is None
        ]
        if missing:
            notes.append(
                f"skipped sandbox outcome for {outcome.subject_id!r} ({key!r}): missing "
                f"{', '.join(missing)} in the supplied inputs -- not enough of the chain to "
                "report, not fabricated"
            )
            continue

        try:
            proposals.append(
                _build_proposal(
                    outcome,
                    gap=gap,
                    idea=idea,
                    audit=audit,
                    generated=generated,
                    feasibility=feasibility_by_key.get(key),
                )
            )
        except AssertionError as exc:
            notes.append(f"skipped sandbox outcome for {outcome.subject_id!r} ({key!r}): {exc}")

    return proposals, notes


def render_json(proposals: list[Proposal], notes: list[str]) -> str:
    """Structured JSON report. See `REPORT_NOTE` for the framing every
    consumer of this output must preserve."""
    payload = {
        "note": REPORT_NOTE,
        "counts": {status: sum(1 for p in proposals if p.sandbox_status == status) for status in SANDBOX_STATUSES},
        "proposals": [p.to_payload() for p in proposals],
        "notes": notes,
    }
    return json.dumps(payload, indent=2) + "\n"


def _render_gap_matches(gap_matches: tuple[dict, ...]) -> str:
    if not gap_matches:
        return "(no matches recorded)"
    return "; ".join(
        f"{m['against_type']}~{m['best_id'] or 'none'}={m['best_similarity']:.3f}(thr={m['threshold']:.3f})"
        for m in gap_matches
    )


def _render_proposal_markdown(p: Proposal) -> list[str]:
    lines = [f"## {p.subject_title} (`{p.subject_id}`)", ""]
    lines.append(
        f"- **gap classification:** `{p.gap_kind}` -- {_render_gap_matches(p.gap_matches)} "
        "(candidate only: unmatched in this index at this threshold, not a novelty claim)"
    )
    lines.append(f"- **method:** {p.idea_method}")
    lines.append(f"- **problem/domain:** {p.idea_problem}")
    lines.append(f"- **novelty verdict:** `{p.novelty_verdict}` -- {p.novelty_note}")
    if p.novelty_rationale:
        lines.append(f"- **novelty rationale:** {p.novelty_rationale}")
    if p.feasibility_verdict is not None:
        lines.append(f"- **feasibility verdict:** `{p.feasibility_verdict}` -- {p.feasibility_note}")
        lines.append(f"  - compute: {p.feasibility_compute}")
        lines.append(f"  - data: {p.feasibility_data}")
        lines.append(f"  - constraints: {p.feasibility_constraints}")
        lines.append(f"  - blueprint: {p.feasibility_blueprint}")
    else:
        lines.append(
            "- **feasibility:** not assessed for this idea (feasibility and codegen run "
            "independently -- see einstein.feasibility module docstring)"
        )
    lines.append(f"- **sandbox status:** `{p.sandbox_status}` ({p.sandbox_attempts} attempt(s)) -- {p.sandbox_note}")
    lines.append("")
    lines.append(f"Target library: `{p.target_library}`")
    lines.append("")
    lines.append("```python")
    lines.append(p.code)
    lines.append("```")
    if p.traceback:
        lines.append("")
        lines.append("Traceback from the final attempt:")
        lines.append("```")
        lines.append(p.traceback)
        lines.append("```")
    lines.append("")
    return lines


def render_markdown(proposals: list[Proposal], notes: list[str]) -> str:
    """Structured Markdown report -- same data as `render_json`, formatted
    for a human reviewer. Carries `REPORT_NOTE` as its own preamble."""
    counts = {status: sum(1 for p in proposals if p.sandbox_status == status) for status in SANDBOX_STATUSES}
    lines = [
        "# Proposal report",
        "",
        REPORT_NOTE,
        "",
        f"**{len(proposals)} candidate(s)** reached a sandbox run -- "
        f"{counts['validated']} validated, {counts['repaired']} repaired, {counts['failed']} failed.",
        "",
    ]
    if not proposals:
        lines.append("(no candidates reached a sandbox run)")
        lines.append("")
    for p in proposals:
        lines.extend(_render_proposal_markdown(p))

    if notes:
        lines.append("## Notes")
        lines.append("")
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def build_report_agent_node():
    """Factory for `einstein.graph.build_graph`'s `agent_node=` slot.

    Returns a callable matching `AgentNodeFn` (`AgentState -> dict`) that
    correlates `state["gaps"]`/`["ideas"]`/`["audits"]`/`["feasibility"]`/
    `["generated_code"]`/`["sandbox_outcomes"]` into `{"proposals": ...,
    "agent_notes": ...}`. Unlike every other `build_*_agent_node` factory in
    this pipeline, this one takes no `llm` argument -- see module docstring:
    this is pure aggregation, nothing here narrates anything a model hasn't
    already narrated upstream. Not wired as `build_graph`'s default -- same
    reasoning as the ideator/auditor/feasibility/codegen/sandbox nodes:
    chaining the full loop into one graph is an integration decision for a
    later bead.
    """

    def _node(state) -> dict:
        proposals, notes = build_proposals(
            state["gaps"],
            state["ideas"],
            state["audits"],
            state["feasibility"],
            state["generated_code"],
            state["sandbox_outcomes"],
        )
        summary = f"report: {len(proposals)} proposal(s) from {len(state['sandbox_outcomes'])} sandbox outcome(s)"
        return {"proposals": proposals, "agent_notes": [summary, *notes]}

    return _node


def _self_check() -> None:
    from einstein.gaps import Match

    gap = Gap(
        kind="true_invention_gap",
        subject_type="paper",
        subject_id="2508.00001",
        subject_title="Tensor network contraction for attention",
        matches=(
            Match(against_type="repo", best_id=None, best_similarity=0.0, threshold=0.2),
            Match(against_type="patent", best_id=None, best_similarity=0.0, threshold=0.2),
        ),
    )
    idea = Idea(
        gap_key=gap.gap_key,
        subject_id=gap.subject_id,
        subject_title=gap.subject_title,
        method="tensor network contraction for transformer attention",
        problem="protein folding structure prediction",
        context_equations=(r"H = \sum_i Z_i",),
        raw_response="",
    )

    def _prior_art_match(against_type: str):
        from einstein.novelty_auditor import PriorArtMatch

        return PriorArtMatch(against_type=against_type, best_id=None, best_similarity=0.0, theta=0.85)

    audit = Audit(
        gap_key=gap.gap_key, subject_id=gap.subject_id, idea_method=idea.method,
        idea_problem=idea.problem, verdict="pass",
        paper_match=_prior_art_match("paper"), patent_match=_prior_art_match("patent"),
        queried_paper_ids=(), queried_patent_ids=(),
        note="no match >= theta=0.85 among 0 re-queried paper(s) and 0 re-queried patent(s)",
    )
    feasibility = Feasibility(
        gap_key=gap.gap_key, subject_id=gap.subject_id, idea_method=idea.method,
        idea_problem=idea.problem, verdict="go",
        compute_requirement="a single GPU", dataset_availability="none, needs synthetic data",
        constraints="numerical stability unproven", blueprint="1. prototype\n2. benchmark",
        raw_response="", note="assessed by LLM -- not measured",
    )
    generated = GeneratedCode(
        gap_key=gap.gap_key, subject_id=gap.subject_id, idea_method=idea.method,
        idea_problem=idea.problem, target_library="qiskit==2.5.2",
        code="print('hello')", context_equations=idea.context_equations,
        raw_response="", note="candidate reference implementation, not run or validated",
    )
    validated_outcome = SandboxOutcome(
        gap_key=gap.gap_key, subject_id=gap.subject_id, verdict="validated",
        attempts=(), final_code="print('hello')", traceback="",
        note="ran clean on the first attempt, no repair needed",
    )
    failed_outcome = SandboxOutcome(
        gap_key="unformalized_code:paper:9999.99999", subject_id="9999.99999", verdict="failed",
        attempts=(), final_code="raise RuntimeError('boom')",
        traceback="RuntimeError: boom", note="failed after 1 attempt(s)",
    )

    proposals, notes = build_proposals([gap], [idea], [audit], [feasibility], [generated], [validated_outcome, failed_outcome])
    assert len(proposals) == 1, proposals
    assert len(notes) == 1 and "missing" in notes[0], notes
    p = proposals[0]
    assert p.sandbox_status == "validated", p
    assert p.feasibility_verdict == "go", p
    assert p.gap_kind == "true_invention_gap", p
    assert p.code == "print('hello')", p

    # feasibility-absent case
    proposals_no_feas, _ = build_proposals([gap], [idea], [audit], [], [generated], [validated_outcome])
    assert proposals_no_feas[0].feasibility_verdict is None, proposals_no_feas[0]

    rendered_json = render_json(proposals, notes)
    parsed = json.loads(rendered_json)
    assert parsed["counts"] == {"validated": 1, "repaired": 0, "failed": 0}, parsed["counts"]
    assert len(parsed["proposals"]) == 1, parsed
    assert "not a novelty claim" not in parsed["note"]  # framing lives in REPORT_NOTE's own wording
    assert "'validated'/'repaired' means" in parsed["note"], parsed["note"]

    rendered_md = render_markdown(proposals, notes)
    assert "# Proposal report" in rendered_md, rendered_md
    assert "```python" in rendered_md, rendered_md
    assert "not assessed" not in rendered_md  # feasibility WAS present in this fixture
    assert "## Notes" in rendered_md, rendered_md

    proposals_no_feas_md = render_markdown(proposals_no_feas, [])
    assert "not assessed" in proposals_no_feas_md, proposals_no_feas_md

    node = build_report_agent_node()
    state = {
        "gaps": [gap], "ideas": [idea], "audits": [audit], "feasibility": [feasibility],
        "generated_code": [generated], "sandbox_outcomes": [validated_outcome, failed_outcome],
    }
    result = node(state)
    assert len(result["proposals"]) == 1, result
    assert result["agent_notes"][0].startswith("report: 1 proposal(s) from 2 sandbox outcome(s)"), result

    empty_result = node(
        {"gaps": [], "ideas": [], "audits": [], "feasibility": [], "generated_code": [], "sandbox_outcomes": []}
    )
    assert empty_result == {"proposals": [], "agent_notes": ["report: 0 proposal(s) from 0 sandbox outcome(s)"]}, empty_result

    print("einstein.report self-check: OK")


if __name__ == "__main__":
    _self_check()
