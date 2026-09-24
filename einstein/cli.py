"""CLI entrypoint: run ingest -> analyze -> agents (`einstein/graph.py`) for
one domain query and print what it found.

    uv run einstein "stochastic gradient descent"
    uv run python -m einstein.cli "stochastic gradient descent" --json

Flags map directly onto what the graph already takes: `--repo-threshold` /
`--patent-threshold` (see `einstein/gaps.py` for what "threshold" means and
why 0.2 is not a magic-clean number), and `--skip-papers` / `--skip-repos`
/ `--skip-patents` to leave a corpus empty on purpose.

That last point is the one worth being careful about. `einstein/gaps.py`'s
module docstring is explicit: "a run with `patents=[]` still marks every
low-repo paper `true_invention_gap`" -- this module cannot tell the
difference between "nobody looked" and "looked, found nothing" from the
`AgentState` alone. So this CLI keeps the two failure modes visibly separate
at the one place that still has the information to do so:

- `--skip-<source>` is a *user* choice not to search. The report says
  "skipped (--skip-x)", never a bare zero.
- A live fetch failure (USPTO with no `USPTO_API_KEY`, GitHub rate-limited,
  arXiv unreachable) is a *failed* search, not an empty one. This CLI does
  not catch it and substitute `[]` -- that would recreate exactly the
  "MOCK-PATENT-01" failure mode `einstein/uspto_fetcher.py` refuses to
  reproduce. It reports which source failed and why, and exits nonzero
  with no gap report at all, because a report built on a partially-failed
  ingest is worse than no report.

einstein-0.3 wired two more things into this entrypoint that were previously
implemented and tested in isolation but never invoked from here:

- **Gap persistence + velocity scoring.** Every invocation opens a `Store`
  at `--db` (default `einstein.db`, relative to cwd -- gitignored, same
  convention as `scripts/nightly_run.sh`'s `./reports/`) and passes it to
  `build_graph`, so `analyze` now dedupes each run's gaps against every
  prior run's (`einstein.velocity.snapshot_gaps`/`score_gaps`) instead of
  treating every run as the first. The report's `velocity` field per gap
  (`is_new`/`trend`/`age_days`/`similarity_delta`) is that scoring, not a
  new claim -- see `einstein/velocity.py`'s module docstring for what
  "dormant"/"rising" do and do not mean.
- **Cross-pollination detection (`einstein.cross_pollination`, Rule A).**
  This is a paper x paper rule over papers/edges that share the OpenAlex id
  scheme -- it cannot run against the arXiv-id `papers` this CLI's normal
  ingest fetches (see `einstein.cross_pollination`'s own id-space caveat).
  So `--cross-pollinate DOMAIN_B` is a genuinely separate, opt-in step, not
  a repurposing of the main `domain` query's papers: both the method corpus
  (`domain`, re-fetched) and the domain-B corpus (`--cross-pollinate`'s
  argument) are pulled fresh from `einstein.openalex_fetcher.search_papers`
  so every paper on both sides has an OpenAlex short id, then citation
  edges are fetched (`fetch_citation_edges`, one call per unique DOI across
  both corpora -- papers with no DOI in OpenAlex's record are counted and
  skipped, never silently dropped) to give `detect_cross_pollination`
  real, if partial, edge data instead of an empty list that would read as
  "definitely unconnected." Candidates are persisted and velocity-scored
  through the same `Store` as the main gaps, under `kind="cross_pollination"`
  -- `Store.upsert_gap`'s own self-check already treats `kind` as an open
  string for exactly this reason (a `CrossPollinationCandidate` cannot
  become a real `einstein.gaps.Gap`: `GapKind` is a closed `Literal` that
  does not include it).

einstein-0.7 wires a third previously-isolated detector in the same
opt-in-extra-step shape: constraint/friction mining
(`einstein.constraint_mining`, einstein-9).

- **`--mine-constraints REPO` (repeatable).** Fetches `bug`/`help wanted`
  issues (`einstein.github_fetcher.fetch_issues`) for each named repo, and
  fetches LaTeX source (`einstein.arxiv_source.extract_source`) for every
  paper already in this run's `papers` corpus -- a live e-print fetch per
  paper, real added API load, which is why this whole step is opt-in rather
  than automatic. Each `REPO` must already be one of this run's *fetched*
  repos (`state["repos"]`, i.e. it matched the `domain` search) -- mining
  issues for a repo this run never actually looked at would silently expand
  the scope of what "this run" means without the run having done the work
  to justify it; an unknown repo is a `SourceFetchError`, not a fetch of a
  repo nobody asked this run to consider.
- A paper with no TeX source (`NoTexSourceError`) is not a failure: not
  every arXiv submission has one (`einstein.arxiv_source`'s own module
  docstring). It is counted and skipped, same "counted, not silently
  dropped" treatment `--cross-pollinate` gives papers with no DOI.
- Pain points from both paths are clustered (`cluster_pain_points`) and
  filtered to widespread ones (`widespread_clusters` -- >=2 distinct
  sources agreeing on the same friction). Widespread clusters are persisted
  and velocity-scored through the same `Store`, under
  `kind="constraint_cluster"`, via `_ConstraintClusterGap` -- see that
  class's own docstring for the `gap_key` choice and its tradeoffs; a
  `PainPointCluster` has no single natural "subject" the way a `Gap` or a
  `CrossPollinationCandidate` does, and that absence is real, not papered
  over.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from functools import partial
from typing import IO, TextIO

from einstein.arxiv_fetcher import fetch_papers as _fetch_papers
from einstein.arxiv_source import NoTexSourceError
from einstein.arxiv_source import extract_source as _extract_source
from einstein.constraint_mining import (
    DEFAULT_SIMILARITY_THRESHOLD as DEFAULT_CONSTRAINT_SIMILARITY_THRESHOLD,
)
from einstein.constraint_mining import (
    PainPointCluster,
    cluster_pain_points,
    pain_points_from_issues,
    pain_points_from_source,
    widespread_clusters,
)
from einstein.cross_pollination import (
    DEFAULT_SIMILARITY_THRESHOLD as DEFAULT_CROSS_POLLINATION_THRESHOLD,
)
from einstein.cross_pollination import CrossPollinationCandidate, detect_cross_pollination
from einstein.gaps import DEFAULT_PATENT_THRESHOLD, DEFAULT_REPO_THRESHOLD, Gap
from einstein.github_fetcher import fetch_issues as _fetch_issues
from einstein.github_fetcher import fetch_repos as _fetch_repos
from einstein.graph import AgentState, build_graph, initial_state
from einstein.openalex_fetcher import fetch_citation_edges as _fetch_citation_edges
from einstein.openalex_fetcher import search_papers as _search_papers
from einstein.schema import Record
from einstein.store import Store
from einstein.uspto_fetcher import fetch_patents as _fetch_patents
from einstein.velocity import ScoredGap, score_gaps, snapshot_gaps

DEFAULT_MAX_RESULTS = 30
DEFAULT_DB_PATH = "einstein.db"

SOURCES = ("papers", "repos", "patents")


class SourceFetchError(Exception):
    """A live fetch for `source` raised. Carries the original exception so
    `main` can print a real cause, not just "something failed"."""

    def __init__(self, source: str, original: Exception) -> None:
        self.source = source
        self.original = original
        super().__init__(f"{source} fetch failed: {original}")


def _skip_fetcher(_domain: str) -> list[Record]:
    return []


def _guard(source: str, fetch):
    """Wrap a real fetcher so any exception it raises is tagged with which
    source raised it, and record the fetched count for the report -- without
    ever turning a raised exception into a `[]` result."""

    def _fetch(domain: str) -> list[Record]:
        try:
            return fetch(domain)
        except Exception as exc:  # noqa: BLE001 - re-tagged and re-raised, not swallowed
            raise SourceFetchError(source, exc) from exc

    return _fetch


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="einstein",
        description="Run the arXiv x GitHub x USPTO gap-detection graph for one domain query.",
    )
    parser.add_argument("domain", help='Search query, e.g. "stochastic gradient descent"')
    parser.add_argument(
        "--repo-threshold",
        type=float,
        default=DEFAULT_REPO_THRESHOLD,
        help=f"cosine-similarity floor for a repo match (default {DEFAULT_REPO_THRESHOLD})",
    )
    parser.add_argument(
        "--patent-threshold",
        type=float,
        default=DEFAULT_PATENT_THRESHOLD,
        help=f"cosine-similarity floor for a patent match (default {DEFAULT_PATENT_THRESHOLD})",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=DEFAULT_MAX_RESULTS,
        help=f"max records to request per source (default {DEFAULT_MAX_RESULTS})",
    )
    parser.add_argument("--skip-papers", action="store_true", help="do not query arXiv")
    parser.add_argument("--skip-repos", action="store_true", help="do not query GitHub")
    parser.add_argument(
        "--skip-patents",
        action="store_true",
        help="do not query USPTO (no anonymous path exists; skip this if USPTO_API_KEY is not set)",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="emit a JSON report instead of text")
    parser.add_argument(
        "--output",
        type=argparse.FileType("w"),
        default=sys.stdout,
        help="write the report here instead of stdout",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB_PATH,
        help=(
            f"SQLite path for gap persistence + dedup/velocity scoring "
            f"(default {DEFAULT_DB_PATH!r}, relative to cwd; gitignored)"
        ),
    )
    parser.add_argument(
        "--cross-pollinate",
        metavar="DOMAIN_B",
        default=None,
        help=(
            "also run method-domain cross-pollination detection (Rule A, "
            "einstein-12): fetches DOMAIN_B as a second paper corpus from "
            "OpenAlex and checks it against `domain`'s papers (also "
            "re-fetched from OpenAlex, for a shared id space -- see module "
            "docstring). Off by default: two extra OpenAlex searches plus "
            "one citation-edge fetch per paper is real, additional API load"
        ),
    )
    parser.add_argument(
        "--cross-pollination-threshold",
        type=float,
        default=DEFAULT_CROSS_POLLINATION_THRESHOLD,
        help=f"cosine-similarity floor for a cross-pollination candidate (default {DEFAULT_CROSS_POLLINATION_THRESHOLD})",
    )
    parser.add_argument(
        "--mine-constraints",
        metavar="OWNER/REPO",
        action="append",
        default=None,
        help=(
            "also run constraint/friction mining (einstein-9): fetch bug/"
            "help-wanted issues for this repo (repeatable) plus LaTeX "
            "source for every paper already in this run's corpus, cluster "
            "the resulting pain points, and report widespread clusters. "
            "REPO must already be one of this run's fetched repos (see "
            "module docstring). Off by default: a live e-print fetch per "
            "paper plus an issues fetch per repo is real, additional API load"
        ),
    )
    parser.add_argument(
        "--constraint-similarity-threshold",
        type=float,
        default=DEFAULT_CONSTRAINT_SIMILARITY_THRESHOLD,
        help=f"cosine-similarity floor for a pain-point cluster edge (default {DEFAULT_CONSTRAINT_SIMILARITY_THRESHOLD})",
    )
    return parser


def _make_fetchers(args: argparse.Namespace) -> tuple[dict, dict, dict]:
    """Build the three `AgentState`-compatible fetch functions plus a
    `source -> {"status", "count"|"error"}` map the report renders from.

    The status map is populated lazily, as each fetcher actually runs
    (`ingest` calls all three), so it always reflects what really happened
    on this invocation rather than what was merely requested.
    """
    status: dict[str, dict] = {}
    skip_flags = {"papers": args.skip_papers, "repos": args.skip_repos, "patents": args.skip_patents}
    real_fetchers = {
        "papers": partial(_fetch_papers, max_results=args.max_results),
        "repos": partial(_fetch_repos, max_results=args.max_results),
        "patents": partial(_fetch_patents, max_results=args.max_results),
    }

    fetchers = {}
    for source in SOURCES:
        if skip_flags[source]:
            status[source] = {"status": "skipped", "reason": f"--skip-{source}", "count": 0}
            fetchers[source] = _skip_fetcher
            continue

        def _fetch(domain: str, source=source) -> list[Record]:
            records = real_fetchers[source](domain)
            status[source] = {"status": "fetched", "reason": None, "count": len(records)}
            return records

        fetchers[source] = _guard(source, _fetch)

    return fetchers, status, skip_flags


@dataclass(frozen=True, slots=True)
class _CrossPollinationGap:
    """Adapts a `CrossPollinationCandidate` to the `gap_key`/`kind`/
    `matches`/`to_payload()` shape `einstein.velocity.score_gaps` and
    `Store.upsert_gap` expect. Not a real `einstein.gaps.Gap`: `GapKind` is a
    closed `Literal` (`true_invention_gap`/`open_source_disruption_target`/
    `unformalized_code`) that does not include, and must not be widened to
    include, "cross_pollination" -- `einstein.gaps.detect_gaps`'s own
    self-check asserts its output kinds equal `GAP_KINDS` exactly. `Store`
    itself already treats `kind` as an open string for this reason (see its
    own self-check's `store.upsert_gap("gap-1", "cross_pollination", ...)`).
    """

    candidate: CrossPollinationCandidate

    @property
    def kind(self) -> str:
        return "cross_pollination"

    @property
    def gap_key(self) -> str:
        return f"cross_pollination:paper:{self.candidate.method_id}"

    @property
    def matches(self) -> tuple:
        return (self.candidate.match,)

    def to_payload(self) -> dict:
        return self.candidate.to_payload()


@dataclass(frozen=True, slots=True)
class _ConstraintClusterGap:
    """Adapts a widespread `PainPointCluster` to the `gap_key`/`kind`/
    `matches`/`to_payload()` shape `einstein.velocity.score_gaps` and
    `Store.upsert_gap` expect -- same reasoning as `_CrossPollinationGap`:
    `"constraint_cluster"` is not a real `einstein.gaps.Gap` (`GapKind`
    stays closed), and `Store` already treats `kind` as an open string for
    exactly this.

    `gap_key` is the sorted, pipe-joined `source_ids` -- the same set of
    distinct papers/repos converging on one friction is the closest thing a
    cluster has to a stable subject across runs (a `Gap` has a subject id, a
    `CrossPollinationCandidate` has `method_id`; a cluster has neither). If
    that source set changes between runs -- a new source joins, or an
    embedding drift pulls a member out -- the key changes too and the
    cluster reads as `is_new` again rather than "grew". That is an honest
    consequence of not inventing a stronger identity than clustering
    actually gives; it is not scored as continuous growth because this
    module has no measured basis for calling it that.

    `matches` is empty: a cluster carries no repo-similarity `Match` the
    way a `Gap` does, so `einstein.velocity.score_gaps`'s "rising" trend
    (which reads `matches` for a repo match) never fires for a constraint
    cluster -- same documented limitation `_CrossPollinationGap.matches`
    already has for the same reason.
    """

    cluster: PainPointCluster

    @property
    def kind(self) -> str:
        return "constraint_cluster"

    @property
    def gap_key(self) -> str:
        return f"constraint_cluster:{'|'.join(sorted(self.cluster.source_ids))}"

    @property
    def matches(self) -> tuple:
        return ()

    def to_payload(self) -> dict:
        return self.cluster.to_payload()


def run(args: argparse.Namespace) -> tuple[AgentState, dict, dict | None, dict | None]:
    """Compile the graph with fetchers matched to `args`, invoke it once for
    `args.domain`, and return `(final_state, source_status, cross_pollination,
    constraint_mining)`.

    `final_state["scored_gaps"]` carries the dedup/velocity result for every
    gap in `final_state["gaps"]` (see `einstein.graph`'s module docstring).
    `cross_pollination` is `None` unless `--cross-pollinate` was given, in
    which case it is the dict `render_json`/`render_text` render (see
    `_run_cross_pollination`). `constraint_mining` is likewise `None` unless
    `--mine-constraints` was given (see `_run_constraint_mining`).

    Raises `SourceFetchError` if any non-skipped source's live fetch fails --
    callers must not catch this and substitute an empty report; see module
    docstring. The same holds for `--cross-pollinate`'s OpenAlex fetches
    (tagged with source `"cross_pollination"`) and `--mine-constraints`'s
    GitHub/arXiv-source fetches (tagged with source `"constraint_mining"`).
    """
    fetchers, status, _ = _make_fetchers(args)
    with Store(args.db) as store:
        graph = build_graph(
            fetch_papers=fetchers["papers"],
            fetch_repos=fetchers["repos"],
            fetch_patents=fetchers["patents"],
            store=store,
        )
        state = graph.invoke(
            initial_state(args.domain, repo_threshold=args.repo_threshold, patent_threshold=args.patent_threshold)
        )

        cross_pollination = _run_cross_pollination(args, store) if args.cross_pollinate else None
        constraint_mining = _run_constraint_mining(args, state, store) if args.mine_constraints else None

    return state, status, cross_pollination, constraint_mining


def _run_cross_pollination(args: argparse.Namespace, store: Store) -> dict:
    """Fetch a method corpus (`args.domain`) and a domain-B corpus
    (`args.cross_pollinate`), both from OpenAlex (shared id space -- see
    module docstring), fetch citation edges for every paper in either corpus
    that has a DOI, run `detect_cross_pollination`, and persist + velocity-
    score every candidate through `store` via `_CrossPollinationGap`.

    Raises `SourceFetchError(source="cross_pollination", ...)` on any
    OpenAlex failure -- same "no partial report" contract as the three main
    sources.
    """
    try:
        method_papers = _search_papers(args.domain, max_results=args.max_results, store=store)
        domain_papers = _search_papers(args.cross_pollinate, max_results=args.max_results, store=store)

        corpus = list({r.id: r for r in (*method_papers, *domain_papers)}.values())
        dois = sorted({r.raw["doi"] for r in corpus if r.raw.get("doi")})
        papers_without_doi = sum(1 for r in corpus if not r.raw.get("doi"))

        edges = [edge for doi in dois for edge in _fetch_citation_edges(doi, store=store)]
    except Exception as exc:  # noqa: BLE001 - re-tagged and re-raised, not swallowed
        raise SourceFetchError("cross_pollination", exc) from exc

    candidates = detect_cross_pollination(
        method_papers, domain_papers, edges, similarity_threshold=args.cross_pollination_threshold
    )

    previous = snapshot_gaps(store)
    wrapped = [_CrossPollinationGap(c) for c in candidates]
    scored = score_gaps(previous, wrapped, now=store.now())
    for scored_gap in scored:
        store.upsert_gap(scored_gap.gap.gap_key, scored_gap.gap.kind, scored_gap.gap.to_payload())
    scored_by_method_id = {sg.gap.candidate.method_id: sg for sg in scored}

    return {
        "domain_b": args.cross_pollinate,
        "method_corpus_size": len(method_papers),
        "domain_corpus_size": len(domain_papers),
        "papers_without_doi": papers_without_doi,
        "edges_fetched": len(edges),
        "candidates": [
            {**c.to_payload(), "velocity": _velocity_row(scored_by_method_id[c.method_id])} for c in candidates
        ],
    }


def _run_constraint_mining(args: argparse.Namespace, state: AgentState, store: Store) -> dict:
    """Fetch GitHub issues for each `--mine-constraints` repo plus LaTeX
    source for every paper already in `state["papers"]`, turn both into
    `PainPoint`s (`einstein.constraint_mining`), cluster them, and persist +
    velocity-score every widespread cluster through `store` via
    `_ConstraintClusterGap`.

    Raises `SourceFetchError(source="constraint_mining", ...)` if any
    `--mine-constraints` repo is not among this run's fetched repos
    (`state["repos"]`), or on any live GitHub/arXiv-source fetch failure --
    same "no partial report" contract as the other two opt-in steps. A
    paper with no TeX source (`NoTexSourceError`) is not a failure -- it is
    counted in `papers_without_tex_source` and skipped, same treatment
    `--cross-pollinate` gives a paper with no DOI.
    """
    known_repos = {r.id for r in state["repos"]}
    unknown_repos = [repo for repo in args.mine_constraints if repo not in known_repos]
    if unknown_repos:
        raise SourceFetchError(
            "constraint_mining",
            ValueError(
                f"--mine-constraints repo(s) not in this run's fetched repo corpus: "
                f"{unknown_repos} (fetched: {sorted(known_repos)})"
            ),
        )

    try:
        issues_by_repo = {
            repo: _fetch_issues(repo, max_results=args.max_results) for repo in args.mine_constraints
        }

        extractions = []
        papers_without_source = 0
        for paper in state["papers"]:
            try:
                extractions.append((paper, _extract_source(paper.id)))
            except NoTexSourceError:
                papers_without_source += 1
    except Exception as exc:  # noqa: BLE001 - re-tagged and re-raised, not swallowed
        raise SourceFetchError("constraint_mining", exc) from exc

    points = [
        point
        for paper, extraction in extractions
        for point in pain_points_from_source(extraction, title=paper.title, url=paper.url)
    ] + [point for issues in issues_by_repo.values() for point in pain_points_from_issues(issues)]

    clusters = cluster_pain_points(points, similarity_threshold=args.constraint_similarity_threshold)
    widespread = widespread_clusters(clusters)

    previous = snapshot_gaps(store)
    wrapped = [_ConstraintClusterGap(c) for c in widespread]
    scored = score_gaps(previous, wrapped, now=store.now())
    for scored_gap in scored:
        store.upsert_gap(scored_gap.gap.gap_key, scored_gap.gap.kind, scored_gap.gap.to_payload())
    scored_by_key = {sg.gap.gap_key: sg for sg in scored}

    return {
        "repos": list(args.mine_constraints),
        "papers_considered": len(state["papers"]),
        "papers_without_tex_source": papers_without_source,
        "pain_points_mined": len(points),
        "clusters": len(clusters),
        "widespread_clusters": [
            {
                **cluster.to_payload(),
                "velocity": _velocity_row(scored_by_key.get(_ConstraintClusterGap(cluster).gap_key)),
            }
            for cluster in widespread
        ],
    }


def _gap_row(gap: Gap) -> dict:
    return asdict(gap)


def _velocity_row(scored: ScoredGap | None) -> dict | None:
    if scored is None:
        return None
    return {
        "is_new": scored.is_new,
        "trend": scored.trend,
        "age_days": scored.age_days,
        "similarity_delta": scored.similarity_delta,
    }


def render_json(
    domain: str,
    state: AgentState,
    status: dict,
    cross_pollination: dict | None = None,
    constraint_mining: dict | None = None,
) -> str:
    scored_by_key = {sg.gap.gap_key: sg for sg in state.get("scored_gaps", [])}
    report = {
        "domain": domain,
        "sources": status,
        "gap_counts": {
            kind: sum(1 for g in state["gaps"] if g.kind == kind)
            for kind in sorted({g.kind for g in state["gaps"]})
        },
        "gaps": [
            {**_gap_row(g), "velocity": _velocity_row(scored_by_key.get(g.gap_key))} for g in state["gaps"]
        ],
        "agent_notes": state["agent_notes"],
    }
    if cross_pollination is not None:
        report["cross_pollination"] = cross_pollination
    if constraint_mining is not None:
        report["constraint_mining"] = constraint_mining
    return json.dumps(report, indent=2) + "\n"


def render_text(
    domain: str,
    state: AgentState,
    status: dict,
    cross_pollination: dict | None = None,
    constraint_mining: dict | None = None,
) -> str:
    lines = [f"domain: {domain}"]

    lines.append("sources:")
    for source in SOURCES:
        info = status.get(source, {"status": "unknown", "reason": None, "count": 0})
        if info["status"] == "skipped":
            lines.append(f"  {source}: skipped ({info['reason']})")
        else:
            lines.append(f"  {source}: fetched {info['count']} record(s)")

    scored_by_key = {sg.gap.gap_key: sg for sg in state.get("scored_gaps", [])}
    gaps = state["gaps"]
    lines.append(f"gaps: {len(gaps)} candidate(s) -- unmatched in this index, at this threshold, not a novelty claim")
    if not gaps:
        lines.append("  (none)")
    for gap in gaps:
        best = "; ".join(
            f"{m.against_type}~{m.best_id or 'none'}={m.best_similarity:.3f}(thr={m.threshold:.3f})"
            for m in gap.matches
        )
        velocity = scored_by_key.get(gap.gap_key)
        velocity_str = f" -- velocity={velocity.trend}(is_new={velocity.is_new})" if velocity is not None else ""
        lines.append(f"  [{gap.kind}] {gap.subject_type}:{gap.subject_id} {gap.subject_title!r} -- {best}{velocity_str}")

    lines.append(f"agent_notes: {len(state['agent_notes'])}")
    for note in state["agent_notes"]:
        lines.append(f"  - {note}")

    if cross_pollination is not None:
        lines.append(f"cross_pollination (domain_b={cross_pollination['domain_b']!r}):")
        lines.append(
            f"  method corpus: {cross_pollination['method_corpus_size']} paper(s) (OpenAlex); "
            f"domain corpus: {cross_pollination['domain_corpus_size']} paper(s) (OpenAlex)"
        )
        lines.append(
            f"  citation edges fetched: {cross_pollination['edges_fetched']} "
            f"({cross_pollination['papers_without_doi']} paper(s) skipped, no DOI in OpenAlex)"
        )
        candidates = cross_pollination["candidates"]
        lines.append(
            f"  candidates: {len(candidates)} -- no citation edge found in this index, not a novelty claim"
        )
        for c in candidates:
            v = c["velocity"]
            velocity_str = f" -- velocity={v['trend']}(is_new={v['is_new']})" if v is not None else ""
            match = c["match"]
            lines.append(
                f"    {c['method_id']} ~ {match['best_id']}={match['best_similarity']:.3f}"
                f"(thr={match['threshold']:.3f}){velocity_str}"
            )

    if constraint_mining is not None:
        lines.append(f"constraint_mining (repos={constraint_mining['repos']!r}):")
        lines.append(
            f"  papers considered: {constraint_mining['papers_considered']} "
            f"({constraint_mining['papers_without_tex_source']} skipped, no TeX source)"
        )
        lines.append(
            f"  pain points mined: {constraint_mining['pain_points_mined']} -> "
            f"{constraint_mining['clusters']} cluster(s)"
        )
        widespread = constraint_mining["widespread_clusters"]
        lines.append(
            f"  widespread clusters: {len(widespread)} -- friction reported by more than one "
            "source in this pass, not a severity or unsolved-elsewhere claim"
        )
        for c in widespread:
            v = c["velocity"]
            velocity_str = f" -- velocity={v['trend']}(is_new={v['is_new']})" if v is not None else ""
            lines.append(
                f"    sources={sorted(c['source_ids'])} members={len(c['members'])}{velocity_str}"
            )

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, *, stderr: TextIO = sys.stderr) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        state, status, cross_pollination, constraint_mining = run(args)
    except SourceFetchError as exc:
        print(f"error: {exc}", file=stderr)
        if exc.source == "patents" and getattr(exc.original, "missing_key", False):
            print(
                "hint: USPTO fetch has no anonymous path -- set USPTO_API_KEY or pass --skip-patents",
                file=stderr,
            )
        print("no report produced: a live fetch failure is not a zero-results search", file=stderr)
        return 1

    render = render_json if args.as_json else render_text
    output: IO[str] = args.output
    output.write(render(args.domain, state, status, cross_pollination, constraint_mining))
    if output is not sys.stdout:
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
