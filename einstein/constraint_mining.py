"""Constraint & friction mining (einstein-9): pain points -> clusters.

gemini_convo.md, "C. Constraint & Friction Mining": scrape text under
Limitations/Future Work/Open Problems headings in papers, plus GitHub issues
labeled `bug`/`help wanted`, then cluster those pain points across
repos/papers to surface widespread bottlenecks -- friction reported in more
than one place is a stronger signal than friction reported once.

This module does the extraction-to-normalized-shape step and the
clustering step. It does not fetch anything itself:

- Paper-side pain points come from `einstein.arxiv_source.extract_source`'s
  `limitations`/`future_work` sections (einstein-7) -- `pain_points_from_source`
  turns a `SourceExtraction` into `PainPoint`s.
- Repo-side pain points come from `einstein.github_fetcher.fetch_issues`
  (einstein-9's own addition to that module) -- `pain_points_from_issues`
  turns a list of `Issue`s into `PainPoint`s.

`PainPoint` is deliberately not a `Record`: `einstein.schema.RecordType` is a
closed `paper`/`repo`/`patent` `Literal` and a pain point is a fragment of
one of those, not a new source-native entity.

Clustering follows `einstein.gaps`/`einstein.cross_pollination`'s convention
of a cosine-similarity threshold rather than a fixed cluster count (k-means
would force every pain point into some cluster, including ones with no real
sibling anywhere in the input). `cluster_pain_points` fits one embedder over
every pain point's text, builds the pairwise cosine-similarity graph, and
takes connected components at `similarity_threshold` as clusters -- single-
link, deliberately: a pain point in a widespread cluster only needs to
resemble *one* other member above threshold, not all of them, because
paraphrases of the same underlying constraint often drift in vocabulary
(a paper's "cannot scale past 8192 tokens" and an issue titled "OOM above
8k context" describe the same friction without sharing much text).

A cluster is "widespread" (`PainPointCluster.is_widespread`) when its members
come from at least `MIN_DISTINCT_SOURCES_FOR_WIDESPREAD` (2) distinct
sources (`source_id` -- an arXiv id or a `"owner/repo"`) -- the same
bottleneck showing up in more than one place, not the same repo filing five
similar issues against itself. Same candidate framing as every other
detector in this repo: a cluster is evidence that these particular texts,
in this particular run, embedded close to each other above a threshold this
run chose. It is not a claim that the underlying constraint is real,
severe, or unsolved elsewhere -- only that this input mining pass found no
distinguishing signal between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from sklearn.metrics.pairwise import cosine_similarity

from einstein.arxiv_source import SourceExtraction
from einstein.embedding import Embedder, embed_groups
from einstein.github_fetcher import Issue

PainPointKind = Literal["paper_limitation", "paper_future_work", "github_issue"]
PAIN_POINT_KINDS: tuple[str, ...] = get_args(PainPointKind)

DEFAULT_SIMILARITY_THRESHOLD = 0.3
MIN_DISTINCT_SOURCES_FOR_WIDESPREAD = 2


@dataclass(frozen=True, slots=True)
class PainPoint:
    """One reported limitation, future-work note, or issue -- normalized to
    one shape regardless of which of the two extraction paths produced it.

    `source_id` is the arXiv id (for `paper_limitation`/`paper_future_work`)
    or the `"owner/repo"` (for `github_issue`) this pain point came from --
    the unit `cluster_pain_points` counts distinct sources over to decide
    "widespread".
    """

    kind: PainPointKind
    source_id: str
    source_title: str
    text: str
    url: str

    def __post_init__(self) -> None:
        assert self.kind in PAIN_POINT_KINDS, self.kind
        assert isinstance(self.source_id, str) and self.source_id, "source_id must be non-empty"
        assert isinstance(self.text, str) and self.text.strip(), "text must be non-empty"

    def to_payload(self) -> dict:
        return {
            "kind": self.kind,
            "source_id": self.source_id,
            "source_title": self.source_title,
            "text": self.text,
            "url": self.url,
        }


def pain_points_from_source(
    extraction: SourceExtraction, *, title: str, url: str
) -> list[PainPoint]:
    """Turn one paper's `SourceExtraction.limitations`/`future_work` sections
    (einstein.arxiv_source, einstein-7) into `PainPoint`s.

    A section's `heading` is folded into its `text` (`"{heading}: {text}"`)
    so a downstream reader without the original `Section` still knows what
    was being described -- some headings ("Limitations") carry no signal
    beyond confirming the kind, others ("Limitations of the Sparse Variant")
    narrow what follows.
    """
    points: list[PainPoint] = []
    for section in extraction.limitations:
        points.append(
            PainPoint(
                kind="paper_limitation",
                source_id=extraction.arxiv_id,
                source_title=title,
                text=f"{section.heading}: {section.text}",
                url=url,
            )
        )
    for section in extraction.future_work:
        points.append(
            PainPoint(
                kind="paper_future_work",
                source_id=extraction.arxiv_id,
                source_title=title,
                text=f"{section.heading}: {section.text}",
                url=url,
            )
        )
    return points


def pain_points_from_issues(issues: list[Issue]) -> list[PainPoint]:
    """Turn `bug`/`help wanted` GitHub issues (einstein.github_fetcher.fetch_issues)
    into `PainPoint`s. `text` is `"{title}\\n{body}"`; an issue with an empty
    body (title-only reports are common) still yields a non-empty `text` as
    long as the title is non-empty, which GitHub already guarantees.
    """
    return [
        PainPoint(
            kind="github_issue",
            source_id=issue.repo,
            source_title=issue.repo,
            text=f"{issue.title}\n{issue.body}".strip(),
            url=issue.url,
        )
        for issue in issues
    ]


@dataclass(frozen=True, slots=True)
class PainPointCluster:
    """One connected component of pain points, at whatever
    `similarity_threshold` `cluster_pain_points` was called with.
    """

    members: tuple[PainPoint, ...]
    similarity_threshold: float

    def __post_init__(self) -> None:
        assert self.members, "a cluster must have at least one member"

    @property
    def source_ids(self) -> frozenset[str]:
        return frozenset(p.source_id for p in self.members)

    @property
    def is_widespread(self) -> bool:
        """At least `MIN_DISTINCT_SOURCES_FOR_WIDESPREAD` distinct sources --
        the same friction reported in more than one paper/repo, not one
        source repeating itself."""
        return len(self.source_ids) >= MIN_DISTINCT_SOURCES_FOR_WIDESPREAD

    def to_payload(self) -> dict:
        return {
            "similarity_threshold": self.similarity_threshold,
            "is_widespread": self.is_widespread,
            "source_ids": sorted(self.source_ids),
            "members": [m.to_payload() for m in self.members],
        }


class _UnionFind:
    """Minimal union-find over `range(n)` -- connected components at a
    similarity threshold, not a general-purpose clustering algorithm.
    stdlib-only, no new dependency for something this small.
    """

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def cluster_pain_points(
    pain_points: list[PainPoint],
    *,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    embedder: Embedder | None = None,
) -> list[PainPointCluster]:
    """Single-link cluster `pain_points` by cosine similarity of their text.

    Fits one embedder over every pain point's `text` (via `embed_groups`,
    same shared-vector-space pattern as `einstein.gaps`/
    `einstein.cross_pollination`), then unions any pair at or above
    `similarity_threshold` (a pain point is never compared against itself).
    Every input pain point ends up in exactly one output cluster -- a pain
    point with no sibling above threshold is a singleton cluster, not
    dropped; see module docstring for why a singleton is not itself evidence
    of anything.

    Clusters are returned in the order their first (lowest-index) member
    appears in `pain_points`, for deterministic output across runs on the
    same input.
    """
    if not pain_points:
        return []

    texts = [p.text for p in pain_points]
    groups = embed_groups({"pain_points": texts}, embedder=embedder)
    vectors = groups["pain_points"]
    sim = cosine_similarity(vectors)

    n = len(pain_points)
    uf = _UnionFind(n)
    for i in range(n):
        for j in range(i + 1, n):
            if sim[i, j] >= similarity_threshold:
                uf.union(i, j)

    groups_by_root: dict[int, list[int]] = {}
    for i in range(n):
        groups_by_root.setdefault(uf.find(i), []).append(i)

    clusters = [
        PainPointCluster(
            members=tuple(pain_points[i] for i in indices),
            similarity_threshold=similarity_threshold,
        )
        for indices in groups_by_root.values()
    ]
    clusters.sort(key=lambda c: pain_points.index(c.members[0]))
    return clusters


def widespread_clusters(clusters: list[PainPointCluster]) -> list[PainPointCluster]:
    """Filter to clusters spanning >= `MIN_DISTINCT_SOURCES_FOR_WIDESPREAD`
    distinct sources -- the "isolate widespread architectural bottlenecks"
    half of the bead, as a plain filter over `cluster_pain_points`'s output
    so callers who want every cluster (including singletons) still can.
    """
    return [c for c in clusters if c.is_widespread]


def _self_check() -> None:
    from einstein.arxiv_source import Section

    def extraction(arxiv_id: str, limitation_text: str) -> SourceExtraction:
        return SourceExtraction(
            arxiv_id=arxiv_id,
            tex_filenames=(f"{arxiv_id}.tex",),
            equations=(),
            limitations=(Section(heading="Limitations", text=limitation_text),),
            future_work=(),
        )

    def issue(repo: str, number: int, title: str, body: str) -> Issue:
        return Issue(
            repo=repo, number=number, title=title, body=body, labels=("bug",),
            url=f"https://github.com/{repo}/issues/{number}", ts="2026-08-08T00:00:00+00:00",
        )

    paper_a = extraction(
        "2101.00001",
        "The model cannot process sequences longer than 8192 tokens and runs out of memory beyond that context length.",
    )
    paper_b = extraction(
        "2101.00002",
        "Training this architecture requires a dedicated multi-GPU cluster; commodity hardware is not sufficient.",
    )
    issue_a = issue(
        "someone/transformer-lib", 42, "OOM above 8k context",
        "Running out of memory whenever the input sequence exceeds 8192 tokens, same context length limit as the paper.",
    )
    issue_b = issue(
        "someone/other-lib", 7, "Unrelated formatting bug",
        "The CLI --help output has a typo in the usage string.",
    )

    points = (
        pain_points_from_source(paper_a, title="Paper A", url="https://arxiv.org/abs/2101.00001")
        + pain_points_from_source(paper_b, title="Paper B", url="https://arxiv.org/abs/2101.00002")
        + pain_points_from_issues([issue_a, issue_b])
    )
    assert len(points) == 4, points

    clusters = cluster_pain_points(points, similarity_threshold=0.15)
    widespread = widespread_clusters(clusters)

    # einstein-9 acceptance: a cluster spans >1 distinct source (paper +
    # repo both reporting the same context-length/OOM friction).
    assert len(widespread) >= 1, [c.to_payload() for c in clusters]
    context_cluster = next(c for c in widespread if "2101.00001" in c.source_ids)
    assert "someone/transformer-lib" in context_cluster.source_ids, context_cluster.to_payload()
    assert len(context_cluster.source_ids) >= 2, context_cluster.to_payload()

    # The unrelated formatting-bug issue and the GPU-cluster limitation
    # share no real signal with anything else -- singleton clusters, and
    # correctly excluded from "widespread".
    singleton_source_ids = {
        sid for c in clusters if not c.is_widespread for sid in c.source_ids
    }
    assert "someone/other-lib" in singleton_source_ids, clusters
    assert "2101.00002" in singleton_source_ids, clusters

    # Every input pain point ends up in exactly one output cluster.
    assert sum(len(c.members) for c in clusters) == len(points)

    print("einstein.constraint_mining self-check: OK")


if __name__ == "__main__":
    _self_check()
