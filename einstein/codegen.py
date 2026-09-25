"""Codegen agent node (einstein-18): synthesize a reference Python/Qiskit
implementation from an idea that survived novelty audit.

This is a later node in the agentic loop (ideator -> novelty auditor ->
feasibility -> codegen -> sandboxed validation, see `bd show einstein-0`) --
but per the bead graph (`bd show einstein-18`), this bead DEPENDS ON
einstein-16 (novelty auditor) only, not on einstein-17 (feasibility
evaluator, still open, a sibling of this bead under the same einstein-16
dependency, not a predecessor of it). So `generate_code` takes an `Idea`
(einstein-15) and its `Audit` (einstein-16) directly and does not wait on,
or invent, a feasibility go/no-go it has no input for.

"Synthesize a reference implementation from the surviving proposal" (the
bead's own description) is read literally: only an `Audit` with
verdict=="pass" has survived the adversarial novelty check. A
"force_pivot" idea was told its method is crowded by prior art and needs
to differentiate before it is worth building; a "reject" idea was flagged
as a candidate infringement risk; an "unsearched" idea (empty paper
re-query, einstein-av1) was never checked against prior art at all.
Generating code for any of them before that
is resolved would ship a reference implementation of exactly the thing
just flagged -- so `generate_code` asserts verdict == "pass" (same
defensive-assert pattern as `einstein.ideator.ideate_gap` asserting
`subject_type == "paper"`). The batch form, `generate`, skips a non-pass
or missing audit with a note instead of raising, same as `ideate` skips
repo-subject gaps and `audit` skips a failed re-query.

"Prompt carries extracted math" (bead): `Idea.context_equations` is
carried through unchanged from the ideator -- itself sourced from
`einstein.arxiv_source.SourceExtraction` (einstein-7) when one was
supplied -- and goes into the prompt verbatim, so the model is grounded in
the paper's own notation instead of whatever it infers from an English
restatement of the method.

"Target library version pinned explicitly" (bead) is the other half of
the same "deprecated-API drift is the top failure mode" sentence:
`DEFAULT_TARGET_LIBRARY` names one exact `qiskit==` version (pinned
against PyPI's published release at authoring time -- see the constant
below), and every prompt states it verbatim and instructs the model to
use only APIs available in that version. This repo does not add `qiskit`
as a project dependency for this -- same reasoning `novelty_auditor.py`'s
docstring gives for not adding a Semantic Scholar client: qiskit is a
library the *generated* code targets, not one this pipeline runs, so
pinning a version here is a documented prompt parameter, not an installed
constraint this repo could enforce even if it wanted to.
`target_library` is a keyword argument for the same reason
`novelty_auditor.DEFAULT_THETA` is: a knob, not a proven constant, and a
caller targeting a different library (or a different qiskit version)
overrides it.

Per this repo's standard for agent nodes ("the agent nodes need an LLM,
and the tests must not"): `LLMClient` (reused from `einstein.ideator`) is
the one seam a real model call goes behind; everything this module is
tested on -- prompt construction, code-fence extraction, which
ideas/audits get codegen'd vs. skipped, failure handling -- is
deterministic and model-free. Response format is deliberately one fenced
` ```python ... ``` ` block rather than "whatever text came back with the
prose stripped somehow" -- a response with no fenced block raises
`CodegenParseError` instead of guessing that the whole response (or some
substring of it) is code, per this repo's "prefer abstention to a
confident answer" standard.

What this module does NOT do: run the generated code, check that it
parses as Python, or check that it imports cleanly. `GeneratedCode.code`
is a string and nothing here executes it -- that is einstein-19's job
(sandboxed execution + self-heal loop), which this bead's own BLOCKS edge
hands off to. Calling that string "an implementation" outright would be
exactly the kind of confident, unmeasured claim this repo's standard
prohibits; `GeneratedCode.note` says "candidate reference implementation,
not run or validated" rather than asserting it works.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from einstein.ideator import Idea, LLMClient
from einstein.novelty_auditor import Audit, PriorArtMatch

# Pinned against PyPI's published `qiskit` release at authoring time
# (2026-09-24: `curl -s https://pypi.org/pypi/qiskit/json` -> "2.5.2").
# Not derived from anything else in this repo -- qiskit is not a project
# dependency (see module docstring) -- so this is a snapshot, not a
# guarantee it is still the current release; pass `target_library=` to
# pin a different version or library entirely.
DEFAULT_TARGET_LIBRARY = "qiskit==2.5.2"

_CODE_FENCE_RE = re.compile(r"```(?:python)?[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)


class CodegenParseError(Exception):
    """The LLM's response had no fenced code block to extract.

    Carries `raw_response` so a caller can log or retry with the original
    text -- this is a parse failure, not evidence the idea can't be
    implemented.
    """

    def __init__(self, message: str, *, gap_key: str, raw_response: str) -> None:
        self.gap_key = gap_key
        self.raw_response = raw_response
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class GeneratedCode:
    """A candidate reference implementation for one surviving `Idea`.

    Not run, not validated -- see module docstring's "what this module
    does NOT do". `context_equations` and `raw_response` are kept for the
    same provenance reason `Idea.raw_response` and
    `Audit.queried_paper_ids` are: a downstream consumer (einstein-19, a
    human reviewer) can see exactly what grounded this code and what the
    model actually said, not just the extracted snippet.
    """

    gap_key: str
    subject_id: str
    idea_method: str
    idea_problem: str
    target_library: str
    code: str
    context_equations: tuple[str, ...]
    raw_response: str
    note: str = ""


def _build_prompt(idea: Idea, target_library: str) -> str:
    lines = [
        "You are a senior software engineer synthesizing a reference implementation "
        "for an invention-ideation pipeline.",
        f"Target library, exact pinned version -- use ONLY APIs available in this "
        f"version; nothing deprecated as of it and nothing added after it: {target_library}",
        f"Method to implement: {idea.method}",
        f"Problem/domain it addresses: {idea.problem}",
        "",
    ]
    if idea.context_equations:
        lines.append("Key equations from the source paper (use this notation, do not re-derive it):")
        lines.extend(f"  {eq}" for eq in idea.context_equations)
        lines.append("")
    lines += [
        "Write a complete, runnable Python module implementing this method against the",
        "pinned library version above. Respond with exactly one fenced code block, "
        "language 'python', and no other text:",
        "```python",
        "<code>",
        "```",
    ]
    return "\n".join(lines)


def _extract_code(raw_response: str, *, gap_key: str) -> str:
    match = _CODE_FENCE_RE.search(raw_response)
    if match is None:
        raise CodegenParseError(
            f"could not find a fenced ```python code block in LLM response for gap {gap_key!r}",
            gap_key=gap_key,
            raw_response=raw_response,
        )
    return match.group(1).strip("\n")


def generate_code(
    idea: Idea,
    audit: Audit,
    *,
    llm: LLMClient,
    target_library: str = DEFAULT_TARGET_LIBRARY,
) -> GeneratedCode:
    """Synthesize a reference implementation for one idea that survived novelty audit.

    Raises `AssertionError` if `audit` does not belong to `idea` (a
    `gap_key` mismatch is a caller pairing bug, not a "no code" outcome)
    or if `audit.verdict != "pass"` (see module docstring on why only a
    surviving proposal is codegen'd). Raises `CodegenParseError` if the
    model's response has no fenced code block.
    """
    assert audit.gap_key == idea.gap_key, (
        f"audit {audit.gap_key!r} does not belong to idea {idea.gap_key!r}"
    )
    assert audit.verdict == "pass", (
        "generate_code only applies to a surviving (verdict='pass') audit, got "
        f"{audit.verdict!r} for gap {idea.gap_key!r}"
    )

    prompt = _build_prompt(idea, target_library)
    raw_response = llm.generate(prompt)
    code = _extract_code(raw_response, gap_key=idea.gap_key)

    return GeneratedCode(
        gap_key=idea.gap_key,
        subject_id=idea.subject_id,
        idea_method=idea.method,
        idea_problem=idea.problem,
        target_library=target_library,
        code=code,
        context_equations=idea.context_equations,
        raw_response=raw_response,
        note="candidate reference implementation, not run or validated -- see einstein-19",
    )


def generate(
    ideas: list[Idea],
    audits: list[Audit],
    *,
    llm: LLMClient,
    target_library: str = DEFAULT_TARGET_LIBRARY,
) -> tuple[list[GeneratedCode], list[str]]:
    """Generate code for every idea whose audit survived. Returns `(generated, notes)`.

    Pairs `ideas` to `audits` by `gap_key`. An idea with no matching audit
    (never audited -- e.g. its re-query transport failed, see
    `einstein.novelty_auditor.audit`'s own docstring) or a matching audit
    that is not "pass" is skipped with a note rather than silently dropped
    or guessed at. A single idea's codegen failure -- a parse failure, or
    whatever exception its own `llm.generate` raises -- is caught here and
    turned into a note, not a fabricated `GeneratedCode`; it does not
    abort the batch, same as `einstein.novelty_auditor.audit`.
    """
    audits_by_gap_key = {a.gap_key: a for a in audits}
    generated: list[GeneratedCode] = []
    notes: list[str] = []

    for idea in ideas:
        audit = audits_by_gap_key.get(idea.gap_key)
        if audit is None:
            notes.append(
                f"skipped {idea.subject_id!r} ({idea.gap_key!r}): no audit found -- "
                "not codegen'd, do not treat as a surviving proposal"
            )
            continue
        if audit.verdict != "pass":
            notes.append(
                f"skipped {idea.subject_id!r} ({idea.gap_key!r}): audit verdict "
                f"{audit.verdict!r}, not a surviving proposal"
            )
            continue
        try:
            generated.append(generate_code(idea, audit, llm=llm, target_library=target_library))
        except Exception as exc:  # noqa: BLE001 -- see docstring above
            notes.append(f"codegen failed for {idea.subject_id!r} ({idea.gap_key!r}): {exc!r}")

    return generated, notes


def build_codegen_agent_node(
    *,
    llm: LLMClient,
    target_library: str = DEFAULT_TARGET_LIBRARY,
):
    """Factory for `einstein.graph.build_graph`'s `agent_node=` slot.

    Returns a callable matching `AgentNodeFn` (`AgentState -> dict`) that
    codegens every surviving idea in `state["ideas"]`/`state["audits"]`
    and returns `{"generated_code": ..., "agent_notes": ...}`. Not wired
    as `build_graph`'s default -- same reasoning as the ideator/auditor
    nodes not being the default: chaining ideator -> auditor -> codegen
    into one graph is an integration decision for a later bead.
    """

    def _node(state) -> dict:
        ideas = state["ideas"]
        audits = state["audits"]
        generated, notes = generate(ideas, audits, llm=llm, target_library=target_library)
        summary = f"codegen: {len(generated)} implementation(s) from {len(audits)} audit(s)"
        return {"generated_code": generated, "agent_notes": [summary, *notes]}

    return _node


class _StubLLM:
    """Deterministic stand-in used by this module's own self-check."""

    def generate(self, prompt: str) -> str:
        return (
            "```python\n"
            "from qiskit import QuantumCircuit\n\n"
            "def build_circuit() -> QuantumCircuit:\n"
            "    qc = QuantumCircuit(2)\n"
            "    qc.h(0)\n"
            "    qc.cx(0, 1)\n"
            "    return qc\n"
            "```"
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

    def _match(against_type: str) -> PriorArtMatch:
        return PriorArtMatch(against_type=against_type, best_id=None, best_similarity=0.0, theta=0.85)

    passing_audit = Audit(
        gap_key=idea.gap_key, subject_id=idea.subject_id, idea_method=idea.method,
        idea_problem=idea.problem, verdict="pass",
        paper_match=_match("paper"), patent_match=_match("patent"),
        queried_paper_ids=(), queried_patent_ids=(),
    )

    result = generate_code(idea, passing_audit, llm=_StubLLM())
    assert "QuantumCircuit" in result.code, result
    assert "```" not in result.code, result
    assert result.target_library == DEFAULT_TARGET_LIBRARY, result
    assert "not run or validated" in result.note, result

    rejected_audit = Audit(
        gap_key=idea.gap_key, subject_id=idea.subject_id, idea_method=idea.method,
        idea_problem=idea.problem, verdict="reject",
        paper_match=_match("paper"), patent_match=_match("patent"),
        queried_paper_ids=(), queried_patent_ids=(),
    )
    try:
        generate_code(idea, rejected_audit, llm=_StubLLM())
        raise AssertionError("expected AssertionError for a non-pass audit")
    except AssertionError as exc:
        assert "reject" in str(exc), exc

    generated, notes = generate([idea], [passing_audit], llm=_StubLLM())
    assert len(generated) == 1 and notes == [], (generated, notes)

    generated, notes = generate([idea], [], llm=_StubLLM())
    assert generated == [] and len(notes) == 1 and "no audit found" in notes[0], notes

    generated, notes = generate([idea], [rejected_audit], llm=_StubLLM())
    assert generated == [] and len(notes) == 1 and "reject" in notes[0], notes

    class NoFenceLLM:
        def generate(self, prompt: str) -> str:
            return "here is some code: print('hi')"

    generated, notes = generate([idea], [passing_audit], llm=NoFenceLLM())
    assert generated == [] and len(notes) == 1 and "codegen failed" in notes[0], notes

    print("einstein.codegen self-check: OK")


if __name__ == "__main__":
    _self_check()
