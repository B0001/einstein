"""Method-domain cross-pollination detection (Rule A).

gemini_convo.md's Rule A: a method paper M whose embedding sits close to a
domain B paper's embedding, but with no citation edge connecting them, is a
candidate for cross-pollination -- someone could apply M to B and, as far as
this run's citation index shows, nobody already has. That is a structurally
different signal from `einstein.gaps.detect_gaps`, which asks "has this been
*built*" (paper vs. repo/patent). This asks "has this been *connected*"
(paper vs. paper, via the citation graph) -- same-keyword similarity is not
enough; a method and a domain can share vocabulary without anyone having
actually applied one to the other, and citation is the closest proxy this
pipeline has for "somebody already tried this".

Same candidate-not-claim framing as `gaps.py` and the same corpus caveat:
`edges=[]` means nothing to check against, not "definitely no citation
exists" -- see `einstein.openalex_fetcher.fetch_citation_edges`'s own
documentation of that distinction. A caller who only fetched edges for a
handful of seed DOIs is measuring cross-pollination against a thin slice of
the citation graph, and that thinness does not show up in this module's
output; it must be carried forward by the caller, same as every other
absence-of-evidence claim in this repo.

Id space: `methods`, `domains`, and `edges` must all use the same identifier
scheme. In practice that means Records fetched via
`einstein.openalex_fetcher.fetch_work_by_doi` (whose `Record.id` is the
OpenAlex short id, e.g. `"W2741809807"`) and `Edge`s from
`fetch_citation_edges` (same short-id scheme) for the same corpus -- this
module does no id resolution between source-native id schemes (an arXiv id
and an OpenAlex short id for the same paper are different strings) and will
simply find no edge for a pair whose ids were never reconciled. That is a
caller-side data problem, not a bug this module can detect from its own
inputs, same as `gaps.py`'s own documented limits.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

from einstein.embedding import Embedder, embed_groups
from einstein.gaps import Match
from einstein.openalex_fetcher import Edge
from einstein.schema import Record

DEFAULT_SIMILARITY_THRESHOLD = 0.2  # same "close" floor gaps.py uses for a match


@dataclass(frozen=True, slots=True)
class CrossPollinationCandidate:
    """One method paper whose closest domain paper is embedding-close but
    has no known citation edge either direction, in the `edges` this run
    was given. See module docstring for what "no known edge" does and does
    not mean.
    """

    method_id: str
    method_title: str
    match: Match  # against_type="paper", best_id = the closest domain paper

    def __post_init__(self) -> None:
        assert self.match.against_type == "paper", self.match.against_type
        assert self.match.best_id is not None, "a candidate must have an actual matched domain paper"

    def to_payload(self) -> dict:
        return {
            "method_id": self.method_id,
            "method_title": self.method_title,
            "match": {
                "against_type": self.match.against_type,
                "best_id": self.match.best_id,
                "best_similarity": self.match.best_similarity,
                "threshold": self.match.threshold,
            },
        }


def _record_text(record: Record) -> str:
    return f"{record.title} {record.summary}".strip()


def _has_edge(edge_pairs: set[tuple[str, str]], a: str, b: str) -> bool:
    return (a, b) in edge_pairs or (b, a) in edge_pairs


def detect_cross_pollination(
    methods: list[Record],
    domains: list[Record],
    edges: list[Edge],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    embedder: Embedder | None = None,
) -> list[CrossPollinationCandidate]:
    """Rule A over `methods` x `domains`.

    Both groups must be `type == "paper"` Records -- this is a paper x paper
    rule, not `gaps.py`'s paper x repo/patent one. Fits one embedder over the
    union of method and domain text (via `embed_groups`, same pattern as
    `detect_gaps`) so similarity is computed in a shared space, then for each
    method paper finds its closest domain paper. A method is a candidate
    when that closeness is at or above `similarity_threshold` *and* no edge
    in `edges` connects the method paper's id and that specific domain
    paper's id, in either citation direction.

    `methods` and `domains` may overlap or even be the same list -- a paper
    is never matched against itself (self-pairs are excluded from the
    nearest-neighbor search).
    """
    for records, label in ((methods, "methods"), (domains, "domains")):
        for record in records:
            assert record.type == "paper", f"{label} must all be paper Records, got {record.type} ({record.id!r})"

    if not methods or not domains:
        return []

    edge_pairs = {(e.from_id, e.to_id) for e in edges}

    groups = embed_groups(
        {"method": [_record_text(r) for r in methods], "domain": [_record_text(r) for r in domains]},
        embedder=embedder,
    )
    sim = cosine_similarity(groups["method"], groups["domain"])

    candidates: list[CrossPollinationCandidate] = []
    for i, method in enumerate(methods):
        row = sim[i].copy()
        for j, domain in enumerate(domains):
            if domain.id == method.id:
                row[j] = -np.inf  # never match a paper to itself

        if not np.isfinite(row).any():
            continue  # only possible domain candidate was itself

        best_j = int(np.argmax(row))
        best_similarity = float(row[best_j])
        if best_similarity < similarity_threshold:
            continue

        domain = domains[best_j]
        if _has_edge(edge_pairs, method.id, domain.id):
            continue  # already connected in this run's citation index -- not a gap

        candidates.append(
            CrossPollinationCandidate(
                method_id=method.id,
                method_title=method.title,
                match=Match(
                    against_type="paper",
                    best_id=domain.id,
                    best_similarity=best_similarity,
                    threshold=similarity_threshold,
                ),
            )
        )

    return candidates


def _self_check() -> None:
    def paper(id_: str, title: str, summary: str) -> Record:
        return Record(
            type="paper", id=id_, title=title, summary=summary,
            url=f"https://openalex.org/{id_}", ts="2026-08-08T00:00:00+00:00", raw={},
        )

    methods = [
        paper("Wmethod1", "Tensor network contraction", "tensor network contraction algorithm for simulating quantum circuits"),
        paper("Wmethod2", "Gradient boosting trees", "gradient boosted decision tree ensemble method for tabular data"),
    ]
    domains = [
        paper("Wdomain1", "Protein folding prediction", "predicting protein folding structures from amino acid sequences"),
        paper("Wdomain2", "Kubernetes scheduling", "scheduling algorithms for container orchestration in kubernetes"),
    ]
    # Wmethod1 (tensor networks) is unrelated in text to both domains at this
    # threshold except we force a synthetic close match via edges/threshold
    # below -- what matters for the self-check is the edge-suppression path.
    edges = [Edge(from_id="Wmethod2", to_id="Wdomain2")]

    candidates = detect_cross_pollination(methods, domains, edges, similarity_threshold=0.0)
    by_method = {c.method_id: c for c in candidates}

    # Wmethod2 has an edge to its closest domain match -> suppressed even
    # though similarity_threshold=0.0 means everything "matches".
    assert "Wmethod2" not in by_method or by_method["Wmethod2"].match.best_id != "Wdomain2", by_method

    # Wmethod1 has no edges at all -> surfaces as a candidate at threshold 0.0.
    assert "Wmethod1" in by_method, by_method

    # A method can never be matched to itself.
    self_edges: list[Edge] = []
    self_candidates = detect_cross_pollination([methods[0]], [methods[0]], self_edges, similarity_threshold=0.0)
    assert self_candidates == [], self_candidates

    print("einstein.cross_pollination self-check: OK")


if __name__ == "__main__":
    _self_check()
