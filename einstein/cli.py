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
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from functools import partial
from typing import IO, TextIO

from einstein.arxiv_fetcher import fetch_papers as _fetch_papers
from einstein.gaps import DEFAULT_PATENT_THRESHOLD, DEFAULT_REPO_THRESHOLD, Gap
from einstein.github_fetcher import fetch_repos as _fetch_repos
from einstein.graph import AgentState, build_graph, initial_state
from einstein.schema import Record
from einstein.uspto_fetcher import fetch_patents as _fetch_patents

DEFAULT_MAX_RESULTS = 30

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


def run(args: argparse.Namespace) -> tuple[AgentState, dict]:
    """Compile the graph with fetchers matched to `args`, invoke it once for
    `args.domain`, and return `(final_state, source_status)`.

    Raises `SourceFetchError` if any non-skipped source's live fetch fails --
    callers must not catch this and substitute an empty report; see module
    docstring.
    """
    fetchers, status, _ = _make_fetchers(args)
    graph = build_graph(
        fetch_papers=fetchers["papers"],
        fetch_repos=fetchers["repos"],
        fetch_patents=fetchers["patents"],
    )
    state = graph.invoke(
        initial_state(args.domain, repo_threshold=args.repo_threshold, patent_threshold=args.patent_threshold)
    )
    return state, status


def _gap_row(gap: Gap) -> dict:
    return asdict(gap)


def render_json(domain: str, state: AgentState, status: dict) -> str:
    report = {
        "domain": domain,
        "sources": status,
        "gap_counts": {
            kind: sum(1 for g in state["gaps"] if g.kind == kind)
            for kind in sorted({g.kind for g in state["gaps"]})
        },
        "gaps": [_gap_row(g) for g in state["gaps"]],
        "agent_notes": state["agent_notes"],
    }
    return json.dumps(report, indent=2) + "\n"


def render_text(domain: str, state: AgentState, status: dict) -> str:
    lines = [f"domain: {domain}"]

    lines.append("sources:")
    for source in SOURCES:
        info = status.get(source, {"status": "unknown", "reason": None, "count": 0})
        if info["status"] == "skipped":
            lines.append(f"  {source}: skipped ({info['reason']})")
        else:
            lines.append(f"  {source}: fetched {info['count']} record(s)")

    gaps = state["gaps"]
    lines.append(f"gaps: {len(gaps)} candidate(s) -- unmatched in this index, at this threshold, not a novelty claim")
    if not gaps:
        lines.append("  (none)")
    for gap in gaps:
        best = "; ".join(
            f"{m.against_type}~{m.best_id or 'none'}={m.best_similarity:.3f}(thr={m.threshold:.3f})"
            for m in gap.matches
        )
        lines.append(f"  [{gap.kind}] {gap.subject_type}:{gap.subject_id} {gap.subject_title!r} -- {best}")

    lines.append(f"agent_notes: {len(state['agent_notes'])}")
    for note in state["agent_notes"]:
        lines.append(f"  - {note}")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, *, stderr: TextIO = sys.stderr) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        state, status = run(args)
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
    output.write(render(args.domain, state, status))
    if output is not sys.stdout:
        output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
