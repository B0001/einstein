"""LangGraph state machine wiring: ingest -> analyze -> route -> agents -> END.

This is the skeleton the rest of the agentic loop (einstein-15 ideator,
einstein-16 novelty auditor, einstein-17 feasibility, einstein-18 codegen --
see `bd show einstein-0`) plugs into. It does not implement any of those
agents itself. The ``agents`` node here is a deterministic, LLM-free stub
that records how many gaps it would have processed; a later bead replaces it
by passing ``agent_node=`` to `build_graph`, not by editing this module.

Nodes:

- ``ingest``: calls `fetch_papers` / `fetch_repos` / `fetch_patents` with
  `state["domain"]` as the query and populates `papers` / `repos` /
  `patents`. Fetch functions are injected (default: the real arXiv/GitHub/
  USPTO fetchers) so tests never touch the network -- pass fakes that return
  canned `Record` lists.
- ``analyze``: runs `einstein.gaps.detect_gaps` over the ingested records and
  populates `gaps`. Same "candidate, not a claim" framing as `gaps.py`
  applies to everything downstream of this node.
- conditional edge (`_route_after_analyze`): zero gaps routes straight to
  `END` -- no agent node runs, no LLM tokens burn on a domain with nothing to
  do. One or more gaps routes to ``agents``.
- ``agents``: LLM-free stub by default. Real agent wiring is a later bead's
  job; see module docstring above.

OpenAlex (einstein/openalex_fetcher.py) is not wired into `ingest` -- it
serves citation-edge lookups (einstein-6/12), a different role than the
arXiv/GitHub/USPTO three-way matrix this graph drives.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from einstein.arxiv_fetcher import fetch_papers as _fetch_papers
from einstein.embedding import Embedder
from einstein.gaps import DEFAULT_PATENT_THRESHOLD, DEFAULT_REPO_THRESHOLD, Gap, detect_gaps
from einstein.github_fetcher import fetch_repos as _fetch_repos
from einstein.schema import Record
from einstein.uspto_fetcher import fetch_patents as _fetch_patents

FetchPapersFn = Callable[[str], list[Record]]
FetchReposFn = Callable[[str], list[Record]]
FetchPatentsFn = Callable[[str], list[Record]]
AgentNodeFn = Callable[["AgentState"], dict]


class AgentState(TypedDict):
    """Shared state threaded through every node.

    `papers` / `repos` / `patents` start empty and are filled by `ingest`;
    `gaps` starts empty and is filled by `analyze`; `agent_notes` starts
    empty and is filled only when `agents` actually runs (i.e. never, on a
    zero-gap run -- that is the acceptance criterion this module exists to
    satisfy). `ideas` likewise starts empty and is only populated by an
    `agent_node` that chooses to return it (the default stub does not; the
    einstein-15 ideator node, `einstein.ideator.build_ideator_agent_node`,
    does). `audits` is the same story one stage later: only populated by
    the einstein-16 novelty auditor node,
    `einstein.novelty_auditor.build_novelty_auditor_agent_node`. Both are
    left untyped as `list` rather than `list[Idea]` / `list[Audit]` so this
    module keeps zero import-time dependency on any specific agent's
    output type -- same reasoning the module docstring gives for not
    implementing the agents here.
    """

    domain: str
    repo_threshold: float
    patent_threshold: float
    papers: list[Record]
    repos: list[Record]
    patents: list[Record]
    gaps: list[Gap]
    agent_notes: list[str]
    ideas: list
    audits: list


def initial_state(
    domain: str,
    *,
    repo_threshold: float = DEFAULT_REPO_THRESHOLD,
    patent_threshold: float = DEFAULT_PATENT_THRESHOLD,
) -> AgentState:
    """Build a fresh `AgentState` for `domain`. All list fields start empty."""
    return AgentState(
        domain=domain,
        repo_threshold=repo_threshold,
        patent_threshold=patent_threshold,
        papers=[],
        repos=[],
        patents=[],
        gaps=[],
        agent_notes=[],
        ideas=[],
        audits=[],
    )


def _default_agent_stub(state: AgentState) -> dict:
    """Placeholder for the real agentic loop (einstein-15/16/17/18).

    Deliberately does not call an LLM -- this bead's job is the graph
    skeleton, not the agents. Records that it ran and how many gaps it saw,
    so tests can assert whether this node was reached without needing a
    mock LLM.
    """
    return {
        "agent_notes": [
            f"stub agent node: {len(state['gaps'])} candidate gap(s) received, "
            "no ideator/auditor/feasibility/codegen wired yet (see einstein-15)"
        ]
    }


def _route_after_analyze(state: AgentState) -> str:
    """Zero gaps -> END, no LLM tokens spent. Otherwise -> agents."""
    return "agents" if state["gaps"] else "end"


def build_graph(
    *,
    fetch_papers: FetchPapersFn = _fetch_papers,
    fetch_repos: FetchReposFn = _fetch_repos,
    fetch_patents: FetchPatentsFn = _fetch_patents,
    embedder: Embedder | None = None,
    agent_node: AgentNodeFn = _default_agent_stub,
) -> CompiledStateGraph:
    """Compile the ingest -> analyze -> route -> agents -> END graph.

    Every side-effecting dependency is injected with a real default:
    `fetch_papers`/`fetch_repos`/`fetch_patents` default to the live arXiv/
    GitHub/USPTO fetchers, `embedder` defaults to `detect_gaps`'s own default
    (TF-IDF), and `agent_node` defaults to the LLM-free stub above. Tests
    pass fakes for all of them and never touch the network or an LLM.
    """

    def ingest(state: AgentState) -> dict:
        domain = state["domain"]
        return {
            "papers": fetch_papers(domain),
            "repos": fetch_repos(domain),
            "patents": fetch_patents(domain),
        }

    def analyze(state: AgentState) -> dict:
        gaps = detect_gaps(
            state["papers"],
            state["repos"],
            state["patents"],
            repo_threshold=state["repo_threshold"],
            patent_threshold=state["patent_threshold"],
            embedder=embedder,
        )
        return {"gaps": gaps}

    graph = StateGraph(AgentState)
    graph.add_node("ingest", ingest)
    graph.add_node("analyze", analyze)
    graph.add_node("agents", agent_node)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "analyze")
    graph.add_conditional_edges("analyze", _route_after_analyze, {"agents": "agents", "end": END})
    graph.add_edge("agents", END)

    return graph.compile()


def _self_check() -> None:
    def paper(id_: str, title: str, summary: str) -> Record:
        return Record(
            type="paper", id=id_, title=title, summary=summary,
            url=f"https://arxiv.org/abs/{id_}", ts="2026-08-11T00:00:00+00:00", raw={},
        )

    def repo(id_: str, title: str, summary: str) -> Record:
        return Record(
            type="repo", id=id_, title=title, summary=summary,
            url=f"https://github.com/{id_}", ts="2026-08-11T00:00:00+00:00", raw={},
        )

    # Zero-gap fixture: the one paper is a near-verbatim match to the one
    # repo, so `analyze` should find no gap and route straight to END.
    matched_papers = [paper("p1", "SGD convergence bounds", "convergence bounds for stochastic gradient descent")]
    matched_repos = [repo("o/sgd-bounds", "sgd-bounds", "convergence bounds for stochastic gradient descent")]

    calls = {"agents": 0}

    def counting_agent(state: AgentState) -> dict:
        calls["agents"] += 1
        return _default_agent_stub(state)

    zero_gap_graph = build_graph(
        fetch_papers=lambda domain: matched_papers,
        fetch_repos=lambda domain: matched_repos,
        fetch_patents=lambda domain: [],
        agent_node=counting_agent,
    )
    result = zero_gap_graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))
    assert result["gaps"] == [], result["gaps"]
    assert result["agent_notes"] == [], result["agent_notes"]
    assert calls["agents"] == 0, "agents node must not run on a zero-gap route"

    # Nonzero-gap fixture: unrelated repo, so the paper is a true_invention_gap
    # and the graph must route into the agents node.
    gap_papers = [paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
    unrelated_repos = [repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]

    nonzero_gap_graph = build_graph(
        fetch_papers=lambda domain: gap_papers,
        fetch_repos=lambda domain: unrelated_repos,
        fetch_patents=lambda domain: [],
        agent_node=counting_agent,
    )
    result = nonzero_gap_graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))
    # The unrelated repo is itself a gap (unformalized_code) alongside the paper's
    # true_invention_gap -- detect_gaps reads both directions of the same matrix.
    gap_kinds = {g.subject_id: g.kind for g in result["gaps"]}
    assert gap_kinds == {"p2": "true_invention_gap", "o/k8s-tool": "unformalized_code"}, gap_kinds
    assert calls["agents"] == 1, "agents node must run exactly once on a nonzero-gap route"
    assert len(result["agent_notes"]) == 1, result["agent_notes"]

    print("einstein.graph self-check: OK")


if __name__ == "__main__":
    _self_check()
