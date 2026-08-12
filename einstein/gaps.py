"""Three-way gap detection: paper x repo and paper x patent cosine matrices.

This module answers one narrow question: for the papers, repos, and patents
handed to it, which ones have no close match in the other corpora *at the
given threshold, in the shared vector space `embed_groups` builds*? That is
the entire claim. It is not a claim that a match does not exist anywhere --
only that this run, over this index, found none above `threshold`. Downstream
consumers (the ideator, the report) must preserve that framing; nothing here
is "novel" or "unprecedented", only "unmatched in this index".

Three classes come out of the paper x repo and paper x patent matrices:

- ``true_invention_gap``: a paper with no close repo AND no close patent.
  Nobody has built it and nobody has filed on it -- the highest-value class,
  and the one most likely to be an artifact of a thin index rather than a
  real void (see einstein-0.1, which measures that).
- ``open_source_disruption_target``: a paper with no close repo but a close
  patent. Someone filed on the idea; nobody has shipped open code for it.
- ``unformalized_code``: a repo with no close paper. Someone built something
  with no theoretical grounding surfaced in this index -- could be original
  engineering, could be an unindexed paper.

Missing corpora are not evidence of a gap by omission -- they are evidence
this run had nothing to check against. A run with `patents=[]` still marks
every low-repo paper `true_invention_gap`, because "zero patents fetched"
and "zero patents found similar" are indistinguishable from inside this
function. Callers that fetched zero patents because of a rate limit, not
because none exist, must not present that output as if the patent space had
been searched. That is exactly the failure mode einstein-0.1 exists to
measure, not something this module can detect from its own inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from einstein.embedding import Embedder, embed_groups
from einstein.schema import Record, RecordType

GapKind = Literal["true_invention_gap", "open_source_disruption_target", "unformalized_code"]
GAP_KINDS: tuple[str, ...] = get_args(GapKind)

DEFAULT_REPO_THRESHOLD = 0.2
DEFAULT_PATENT_THRESHOLD = 0.2


@dataclass(frozen=True, slots=True)
class Match:
    """The closest record of `against_type` found for some subject, and the
    threshold that closeness was judged against.

    `best_id`/`best_similarity` describe the single nearest neighbor, not an
    aggregate -- one strong match is enough to disqualify a gap, so the max
    is the only number that matters for classification. `best_id` is None
    only when `against_type`'s corpus was empty for this run.
    """

    against_type: RecordType
    best_id: str | None
    best_similarity: float
    threshold: float

    def __post_init__(self) -> None:
        assert self.against_type in get_args(RecordType), self.against_type
        assert -1.0 <= self.threshold <= 1.0, self.threshold

    @property
    def below_threshold(self) -> bool:
        return self.best_similarity < self.threshold


@dataclass(frozen=True, slots=True)
class Gap:
    """A candidate structural gap. See module docstring: this is a candidate,
    not a claim that the gap is real or novel.
    """

    kind: GapKind
    subject_type: RecordType
    subject_id: str
    subject_title: str
    matches: tuple[Match, ...]

    def __post_init__(self) -> None:
        assert self.kind in GAP_KINDS, self.kind
        assert self.matches, "Gap must carry at least one Match as evidence"

    @property
    def gap_key(self) -> str:
        """Stable key for `Store.upsert_gap`: same subject + kind collapses
        to one row across repeat runs, per the store's upsert contract."""
        return f"{self.kind}:{self.subject_type}:{self.subject_id}"

    def to_payload(self) -> dict:
        """JSON-serializable payload for `Store.upsert_gap`."""
        return {
            "subject_type": self.subject_type,
            "subject_id": self.subject_id,
            "subject_title": self.subject_title,
            "matches": [
                {
                    "against_type": m.against_type,
                    "best_id": m.best_id,
                    "best_similarity": m.best_similarity,
                    "threshold": m.threshold,
                }
                for m in self.matches
            ],
        }


def _record_text(record: Record) -> str:
    return f"{record.title} {record.summary}".strip()


def _best_match(sim_row: np.ndarray, candidates: list[Record], against_type: RecordType, threshold: float) -> Match:
    if not candidates:
        return Match(against_type=against_type, best_id=None, best_similarity=0.0, threshold=threshold)
    best_idx = int(np.argmax(sim_row))
    return Match(
        against_type=against_type,
        best_id=candidates[best_idx].id,
        best_similarity=float(sim_row[best_idx]),
        threshold=threshold,
    )


def detect_gaps(
    papers: list[Record],
    repos: list[Record],
    patents: list[Record],
    *,
    repo_threshold: float = DEFAULT_REPO_THRESHOLD,
    patent_threshold: float = DEFAULT_PATENT_THRESHOLD,
    embedder: Embedder | None = None,
) -> list[Gap]:
    """Classify papers and repos into the three gap classes.

    Fits one embedder over the union of paper/repo/patent text (via
    `embed_groups`) so the three corpora share a vector space, then computes
    the paper x repo and paper x patent cosine similarity matrices.

    A paper is a gap when its best repo match is below `repo_threshold`:
    `true_invention_gap` if its best patent match is also below
    `patent_threshold`, `open_source_disruption_target` if not. A paper with
    a close repo match is not a gap regardless of patent status -- it has
    already been implemented.

    A repo is `unformalized_code` when its best paper match (the same
    paper x repo matrix, read by column) is below `repo_threshold`.

    Every asserted type must actually be that type -- callers passing a
    `repo` where a `paper` is expected get an assertion error, not a
    silently wrong matrix.
    """
    for records, expected in ((papers, "paper"), (repos, "repo"), (patents, "patent")):
        for record in records:
            assert record.type == expected, f"expected {expected} Record, got {record.type} ({record.id!r})"

    if not papers and not repos and not patents:
        return []

    groups = embed_groups(
        {
            "paper": [_record_text(r) for r in papers],
            "repo": [_record_text(r) for r in repos],
            "patent": [_record_text(r) for r in patents],
        },
        embedder=embedder,
    )

    paper_vs_repo = (
        cosine_similarity(groups["paper"], groups["repo"])
        if papers and repos
        else np.zeros((len(papers), len(repos)))
    )
    paper_vs_patent = (
        cosine_similarity(groups["paper"], groups["patent"])
        if papers and patents
        else np.zeros((len(papers), len(patents)))
    )

    gaps: list[Gap] = []

    for i, paper in enumerate(papers):
        repo_match = _best_match(paper_vs_repo[i], repos, "repo", repo_threshold)
        patent_match = _best_match(paper_vs_patent[i], patents, "patent", patent_threshold)

        if not repo_match.below_threshold:
            continue  # already implemented somewhere in this index -- not a gap

        kind: GapKind = "true_invention_gap" if patent_match.below_threshold else "open_source_disruption_target"
        gaps.append(
            Gap(
                kind=kind,
                subject_type="paper",
                subject_id=paper.id,
                subject_title=paper.title,
                matches=(repo_match, patent_match),
            )
        )

    for j, repo in enumerate(repos):
        paper_match = _best_match(paper_vs_repo[:, j], papers, "paper", repo_threshold)
        if paper_match.below_threshold:
            gaps.append(
                Gap(
                    kind="unformalized_code",
                    subject_type="repo",
                    subject_id=repo.id,
                    subject_title=repo.title,
                    matches=(paper_match,),
                )
            )

    return gaps


def _self_check() -> None:
    def paper(id_: str, title: str, summary: str) -> Record:
        return Record(
            type="paper", id=id_, title=title, summary=summary,
            url=f"https://arxiv.org/abs/{id_}", ts="2026-08-08T00:00:00+00:00", raw={},
        )

    def repo(id_: str, title: str, summary: str) -> Record:
        return Record(
            type="repo", id=id_, title=title, summary=summary,
            url=f"https://github.com/{id_}", ts="2026-08-08T00:00:00+00:00", raw={},
        )

    def patent(id_: str, title: str, summary: str) -> Record:
        return Record(
            type="patent", id=id_, title=title, summary=summary,
            url=f"https://patents.google.com/patent/{id_}", ts="2026-08-08T00:00:00+00:00", raw={},
        )

    papers = [
        paper("true-gap", "Tensor network contraction for attention", "novel tensor contraction algorithm for transformer attention mechanisms"),
        paper("disruption", "Quantum annealing for portfolio optimization", "quantum annealing schedule for combinatorial portfolio optimization"),
        paper("implemented", "Gradient descent convergence bounds", "convergence bounds analysis for stochastic gradient descent optimizers"),
    ]
    repos = [
        repo("someone/sgd-bounds", "sgd-bounds", "convergence bounds analysis for stochastic gradient descent optimizers"),
        repo("someone/orphan-tool", "orphan-tool", "a command line tool for managing kubernetes deployment pipelines"),
    ]
    patents = [
        patent("US001", "Quantum annealing portfolio method", "quantum annealing schedule for combinatorial portfolio optimization filed method"),
    ]

    gaps = detect_gaps(papers, repos, patents, repo_threshold=0.3, patent_threshold=0.3)
    by_kind = {g.subject_id: g.kind for g in gaps}

    assert by_kind.get("true-gap") == "true_invention_gap", by_kind
    assert by_kind.get("disruption") == "open_source_disruption_target", by_kind
    assert "implemented" not in by_kind, by_kind
    assert by_kind.get("someone/orphan-tool") == "unformalized_code", by_kind
    assert "someone/sgd-bounds" not in by_kind, by_kind
    assert {g.kind for g in gaps} == set(GAP_KINDS), {g.kind for g in gaps}

    print("einstein.gaps self-check: OK")


if __name__ == "__main__":
    _self_check()
