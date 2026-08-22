"""Novelty auditor agent node (einstein-16): adversarial re-check of an
`Idea` (einstein-15) against independently re-queried paper and patent
corpora.

This is the second node in the agentic loop (ideator -> novelty auditor ->
feasibility -> codegen -> sandboxed validation, see `bd show einstein-0`).
Per this repo's standard: the ideator already asserted a `Gap` is only
"unmatched in *this* index, *these* sources, *this* threshold" -- the
auditor's whole job is to go re-check that against a fresh, independent
query before anyone acts on the idea. Reusing the same papers/patents the
gap detector already saw would not be an adversarial check, it would be
re-reading the same evidence twice. So `audit_idea` always calls the
injected `search_papers` / `search_patents` again with the idea's own
method text as the query, and never falls back to whatever the original
`Gap.matches` said.

Two design decisions this bead's spec left open, resolved here and recorded
so a reviewer does not have to reverse-engineer them:

1. **Reject vs. force-pivot.** `gemini_convo.md` (background, not spec --
   line ~92) says only "if prior art exists above similarity threshold
   theta>0.85, the idea is rejected or forced to pivot" -- one threshold,
   two outcomes, no rule for which. This module ties the split to *why* the
   bead asked for independent-claims comparison in the first place: a
   patent's independent claim is a legal scope statement (see
   `einstein/patent_claims.py`) -- scoring above theta against one is a
   candidate infringement risk, which is a hard blocker, so it REJECTs. A
   paper scoring above theta is prior art crowding the same method with no
   legal exposure -- the idea can still be pursued if it differentiates, so
   it FORCE_PIVOTs. A patent match takes precedence: infringement risk is
   checked first regardless of how similar the closest paper is.
2. **Where "Semantic Scholar" comes from.** No Semantic Scholar client
   exists anywhere in this repo, and this module does not add one -- the
   USPTO fetcher's docstring already sets the precedent for refusing to
   guess at an unverified external API's shape rather than fabricate a
   plausible-looking integration. `search_papers` is therefore a required
   keyword argument with NO default (same pattern as `einstein.ideator`'s
   `llm` argument): callers wire in whatever paper-search backend they
   have (Semantic Scholar, an OpenAlex text search once one exists,
   whatever). `search_patents` DOES default to the real
   `einstein.uspto_fetcher.fetch_patents` -- that fetcher already exists,
   is already tested against a recorded fixture, and needs no invented
   field names to use here, so unlike the paper side there is nothing to
   guess at.

Per the ideator's precedent ("the agent nodes need an LLM, and the tests
must not"): the theta comparison that decides pass/force_pivot/reject is
pure, deterministic code -- every number in an `Audit` comes from
`cosine_similarity`, never from the model. `llm` (an
`einstein.ideator.LLMClient`) is used only to narrate *why* a reject/
force_pivot fired, for a human reviewer -- and is never called on a "pass"
verdict, so a domain with nothing to flag burns no LLM tokens (same
reasoning as `einstein.graph`'s zero-gap route skipping the agents node
entirely).

A "pass" verdict is never phrased as novelty. It means: nothing in the
freshly re-queried papers/patents scored >= theta. That is "no match found
within this search, these sources, this threshold" -- see `Audit.note`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, get_args

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from einstein.embedding import Embedder, embed_groups
from einstein.ideator import Idea, LLMClient
from einstein.patent_claims import ClaimsNotFoundError, independent_claims_for_record
from einstein.schema import Record
from einstein.uspto_fetcher import fetch_patents as _fetch_patents

NoveltyVerdict = Literal["pass", "force_pivot", "reject"]
NOVELTY_VERDICTS: tuple[str, ...] = get_args(NoveltyVerdict)

# gemini_convo.md, "Novelty Auditor Agent": "If prior art exists above
# similarity threshold theta>0.85, the idea is rejected or forced to
# pivot." Background, not spec -- but the bead itself names no number, so
# this is the one place a number for theta is written down at all. Treat
# it as a knob (every call below accepts theta=), not a proven constant.
DEFAULT_THETA = 0.85

SearchFn = Callable[[str], list[Record]]


@dataclass(frozen=True, slots=True)
class PriorArtMatch:
    """The closest re-queried record found against an idea, and the theta
    it was judged against.

    `claim_number` is set only for a `against_type="patent"` match -- it is
    the specific independent claim number that scored highest, not the
    patent as a whole (see module docstring on why claims, not abstracts).
    """

    against_type: Literal["paper", "patent"]
    best_id: str | None
    best_similarity: float
    theta: float
    claim_number: int | None = None

    def __post_init__(self) -> None:
        assert self.against_type in ("paper", "patent"), self.against_type
        assert -1.0 <= self.theta <= 1.0, self.theta
        if self.against_type == "paper":
            assert self.claim_number is None, "a paper match has no claim number"

    @property
    def above_theta(self) -> bool:
        return self.best_similarity >= self.theta


@dataclass(frozen=True, slots=True)
class Audit:
    """The result of adversarially re-checking one `Idea`.

    `queried_paper_ids` / `queried_patent_ids` are exactly what
    `search_papers`/`search_patents` returned for this idea's query -- kept
    for provenance, same reason `Idea.raw_response` is kept: a downstream
    consumer of a "pass" verdict can see precisely what was (and was not)
    searched, not just trust the verdict. `warnings` records any candidate
    patent that had no usable independent-claim text (see
    `patent_claims.independent_claims_for_record`) and was therefore
    skipped rather than silently treated as "no claims to conflict with".
    """

    gap_key: str
    subject_id: str
    idea_method: str
    idea_problem: str
    verdict: NoveltyVerdict
    paper_match: PriorArtMatch
    patent_match: PriorArtMatch
    queried_paper_ids: tuple[str, ...]
    queried_patent_ids: tuple[str, ...]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    rationale: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        assert self.verdict in NOVELTY_VERDICTS, self.verdict


def _idea_text(idea: Idea) -> str:
    return f"{idea.method} {idea.problem}".strip()


def _record_text(record: Record) -> str:
    return f"{record.title} {record.summary}".strip()


def _best_paper_match(idea_text: str, papers: list[Record], theta: float, embedder: Embedder | None) -> PriorArtMatch:
    if not papers:
        return PriorArtMatch(against_type="paper", best_id=None, best_similarity=0.0, theta=theta)

    groups = embed_groups({"idea": [idea_text], "paper": [_record_text(p) for p in papers]}, embedder=embedder)
    sims = cosine_similarity(groups["idea"], groups["paper"])[0]
    best_idx = int(np.argmax(sims))
    return PriorArtMatch(
        against_type="paper", best_id=papers[best_idx].id, best_similarity=float(sims[best_idx]), theta=theta
    )


def _candidate_claims(
    patents: list[Record], claims_field: str | None
) -> tuple[list[tuple[str, int, str]], list[str]]:
    """Independent claims across every candidate patent, plus a warning for
    any patent with no usable claim text.

    A patent that `independent_claims_for_record` cannot find claim text
    for is skipped, not treated as "has no conflicting claims" -- those are
    different facts (see `patent_claims.py`'s own "does NOT fall back to
    the abstract" contract), so the distinction is preserved in `warnings`
    rather than collapsed away.
    """
    claims: list[tuple[str, int, str]] = []
    warnings: list[str] = []
    for patent in patents:
        try:
            independent = independent_claims_for_record(patent, claims_field=claims_field)
        except ClaimsNotFoundError as exc:
            warnings.append(f"patent {patent.id!r}: {exc}")
            continue
        claims.extend((patent.id, claim.number, claim.text) for claim in independent)
    return claims, warnings


def _best_patent_claim_match(
    idea_text: str, patents: list[Record], theta: float, embedder: Embedder | None, claims_field: str | None
) -> tuple[PriorArtMatch, list[str]]:
    claims, warnings = _candidate_claims(patents, claims_field)
    if not claims:
        return PriorArtMatch(against_type="patent", best_id=None, best_similarity=0.0, theta=theta), warnings

    groups = embed_groups({"idea": [idea_text], "claim": [text for _, _, text in claims]}, embedder=embedder)
    sims = cosine_similarity(groups["idea"], groups["claim"])[0]
    best_idx = int(np.argmax(sims))
    patent_id, claim_number, _ = claims[best_idx]
    match = PriorArtMatch(
        against_type="patent",
        best_id=patent_id,
        best_similarity=float(sims[best_idx]),
        theta=theta,
        claim_number=claim_number,
    )
    return match, warnings


def _note(
    verdict: NoveltyVerdict,
    paper_match: PriorArtMatch,
    patent_match: PriorArtMatch,
    n_papers: int,
    n_patents: int,
    warnings: list[str],
    theta: float,
) -> str:
    if verdict == "reject":
        return (
            f"independent claim {patent_match.claim_number} of patent {patent_match.best_id!r} "
            f"similarity={patent_match.best_similarity:.3f} >= theta={theta} -- candidate "
            "infringement risk (not a legal determination), reject"
        )
    if verdict == "force_pivot":
        return (
            f"paper {paper_match.best_id!r} similarity={paper_match.best_similarity:.3f} >= "
            f"theta={theta} -- prior art crowds this method (no patent-claim conflict found), "
            "force pivot"
        )
    note = (
        f"no match >= theta={theta} among {n_papers} re-queried paper(s) and {n_patents} "
        "re-queried patent(s) -- absence within this search only, not a novelty claim"
    )
    if warnings:
        note += f"; {len(warnings)} patent(s) skipped for lack of usable claim text"
    return note


def _rationale_prompt(idea: Idea, verdict: NoveltyVerdict, paper_match: PriorArtMatch, patent_match: PriorArtMatch) -> str:
    lines = [
        "You are the adversarial novelty auditor in an invention-ideation pipeline.",
        "Your job is to argue AGAINST novelty, not for it.",
        f"Candidate method: {idea.method}",
        f"Candidate problem/domain: {idea.problem}",
        "",
    ]
    if verdict == "reject":
        lines += [
            f"Independent claim {patent_match.claim_number} of patent {patent_match.best_id} scored "
            f"similarity={patent_match.best_similarity:.3f} against this method.",
            "In one paragraph, explain how this claim's scope could read on the candidate method, "
            "for a human reviewer doing infringement triage. State this as a risk to investigate, "
            "not as a legal conclusion.",
        ]
    else:
        lines += [
            f"Paper {paper_match.best_id} scored similarity={paper_match.best_similarity:.3f} against "
            "this method.",
            "In one paragraph, explain how this prior paper overlaps with the candidate method, and "
            "what would need to change for the idea to differentiate from it.",
        ]
    return "\n".join(lines)


def audit_idea(
    idea: Idea,
    *,
    search_papers: SearchFn,
    search_patents: SearchFn = _fetch_patents,
    theta: float = DEFAULT_THETA,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    claims_field: str | None = None,
) -> Audit:
    """Adversarially re-check a single `Idea`.

    Re-queries `search_papers`/`search_patents` with `idea.method` as the
    query -- the original `Gap.matches` that produced this idea are not
    consulted, per the module docstring's "re-check, not re-read" framing.
    `theta` gates both comparisons; see module docstring for why a patent
    match rejects and a paper match force-pivots. `llm` is only called (via
    its one `generate(prompt) -> str` method) when the verdict is not
    "pass" -- see module docstring.
    """
    candidate_papers = search_papers(idea.method)
    candidate_patents = search_patents(idea.method)
    idea_text = _idea_text(idea)

    paper_match = _best_paper_match(idea_text, candidate_papers, theta, embedder)
    patent_match, warnings = _best_patent_claim_match(idea_text, candidate_patents, theta, embedder, claims_field)

    verdict: NoveltyVerdict
    if patent_match.above_theta:
        verdict = "reject"
    elif paper_match.above_theta:
        verdict = "force_pivot"
    else:
        verdict = "pass"

    rationale = ""
    if verdict != "pass" and llm is not None:
        rationale = llm.generate(_rationale_prompt(idea, verdict, paper_match, patent_match))

    note = _note(verdict, paper_match, patent_match, len(candidate_papers), len(candidate_patents), warnings, theta)

    return Audit(
        gap_key=idea.gap_key,
        subject_id=idea.subject_id,
        idea_method=idea.method,
        idea_problem=idea.problem,
        verdict=verdict,
        paper_match=paper_match,
        patent_match=patent_match,
        queried_paper_ids=tuple(p.id for p in candidate_papers),
        queried_patent_ids=tuple(p.id for p in candidate_patents),
        warnings=tuple(warnings),
        rationale=rationale,
        note=note,
    )


def audit(
    ideas: list[Idea],
    *,
    search_papers: SearchFn,
    search_patents: SearchFn = _fetch_patents,
    theta: float = DEFAULT_THETA,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    claims_field: str | None = None,
) -> tuple[list[Audit], list[str]]:
    """Audit every `Idea` in `ideas`. Returns `(audits, notes)`.

    A `search_papers`/`search_patents` failure for one idea (whatever
    exception its own transport raises -- a fetcher's own error type, a
    bare `requests` exception, anything) is caught here and turned into a
    note, not a fabricated `Audit`: a re-query that never ran must never be
    read as "no prior art found", so that idea is left out of `audits`
    entirely rather than given a guessed verdict. A single failing idea
    does not abort the batch.
    """
    audits: list[Audit] = []
    notes: list[str] = []
    for idea in ideas:
        try:
            audits.append(
                audit_idea(
                    idea,
                    search_papers=search_papers,
                    search_patents=search_patents,
                    theta=theta,
                    embedder=embedder,
                    llm=llm,
                    claims_field=claims_field,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- see docstring above
            notes.append(
                f"audit failed for {idea.subject_id!r} ({idea.gap_key!r}): {exc!r} -- "
                "not audited, do not treat as a pass"
            )
    return audits, notes


def build_novelty_auditor_agent_node(
    *,
    search_papers: SearchFn,
    search_patents: SearchFn = _fetch_patents,
    theta: float = DEFAULT_THETA,
    embedder: Embedder | None = None,
    llm: LLMClient | None = None,
    claims_field: str | None = None,
):
    """Factory for `einstein.graph.build_graph`'s `agent_node=` slot.

    Returns a callable matching `AgentNodeFn` (`AgentState -> dict`) that
    audits every idea in `state["ideas"]` and returns
    `{"audits": ..., "agent_notes": ...}`. Not wired as `build_graph`'s
    default -- same reasoning as `einstein.ideator.build_ideator_agent_node`
    not being the default: chaining ideator -> auditor -> feasibility into
    one graph is an integration decision for a later bead, and
    `search_papers` has no safe default to wire in anyway (see module
    docstring).
    """

    def _node(state) -> dict:
        ideas = state["ideas"]
        audits, notes = audit(
            ideas,
            search_papers=search_papers,
            search_patents=search_patents,
            theta=theta,
            embedder=embedder,
            llm=llm,
            claims_field=claims_field,
        )
        summary = f"novelty auditor: {len(audits)} audit(s) from {len(ideas)} idea(s)"
        return {"audits": audits, "agent_notes": [summary, *notes]}

    return _node


class _StubLLM:
    """Deterministic stand-in used by this module's own self-check."""

    def generate(self, prompt: str) -> str:
        return "This candidate method substantially overlaps the cited prior art."


def _self_check() -> None:
    idea = Idea(
        gap_key="true_invention_gap:paper:2508.00001",
        subject_id="2508.00001",
        subject_title="Tensor network contraction for attention",
        method="tensor network contraction for transformer attention",
        problem="protein folding structure prediction",
        context_equations=(),
        raw_response="METHOD: tensor network contraction for transformer attention\nPROBLEM: protein folding structure prediction\n",
    )

    def unrelated_papers(query: str) -> list[Record]:
        return [
            Record(
                type="paper", id="p1", title="Kubernetes deployment pipelines",
                summary="a command line tool for managing container orchestration",
                url="https://arxiv.org/abs/p1", ts="2026-08-08T00:00:00+00:00", raw={},
            )
        ]

    def unrelated_patents(query: str) -> list[Record]:
        return [
            Record(
                type="patent", id="US001", title="Widget fastener",
                summary="a widget fastener", url="https://patents.google.com/patent/US001",
                ts="2026-08-08T00:00:00+00:00",
                raw={"claimsText": "1. A fastener comprising a widget and a clip."},
            )
        ]

    passing = audit_idea(idea, search_papers=unrelated_papers, search_patents=unrelated_patents, llm=_StubLLM())
    assert passing.verdict == "pass", passing
    assert passing.rationale == "", "LLM must not be called on a pass verdict"
    assert "not a novelty claim" in passing.note, passing.note

    def conflicting_patents(query: str) -> list[Record]:
        return [
            Record(
                type="patent", id="US002", title="Attention method",
                summary="attention method", url="https://patents.google.com/patent/US002",
                ts="2026-08-08T00:00:00+00:00",
                raw={
                    "claimsText": (
                        "1. A method comprising tensor network contraction for transformer "
                        "attention applied to protein folding structure prediction.\n"
                        "2. The method of claim 1, wherein the tensors are sparse."
                    )
                },
            )
        ]

    rejected = audit_idea(
        idea, search_papers=unrelated_papers, search_patents=conflicting_patents, theta=0.6, llm=_StubLLM()
    )
    assert rejected.verdict == "reject", rejected
    assert rejected.patent_match.claim_number == 1, rejected.patent_match
    assert rejected.rationale != "", "LLM must be called on a reject verdict"

    audits, notes = audit([idea], search_papers=unrelated_papers, search_patents=conflicting_patents, theta=0.6)
    assert len(audits) == 1 and audits[0].verdict == "reject", (audits, notes)

    def failing_search(query: str) -> list[Record]:
        raise RuntimeError("simulated transport failure")

    audits, notes = audit([idea], search_papers=failing_search, search_patents=unrelated_patents)
    assert audits == [], audits
    assert len(notes) == 1 and "not audited" in notes[0], notes

    print("einstein.novelty_auditor self-check: OK")


if __name__ == "__main__":
    _self_check()
