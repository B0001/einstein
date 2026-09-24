import tempfile
import unittest
from pathlib import Path

from einstein.graph import AgentState, build_graph, initial_state
from einstein.schema import Record
from einstein.store import Store


def _paper(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="paper",
        id=id_,
        title=title,
        summary=summary,
        url=f"https://arxiv.org/abs/{id_}",
        ts="2026-08-11T00:00:00+00:00",
        raw={},
    )


def _repo(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="repo",
        id=id_,
        title=title,
        summary=summary,
        url=f"https://github.com/{id_}",
        ts="2026-08-11T00:00:00+00:00",
        raw={},
    )


def _patent(id_: str, title: str, summary: str) -> Record:
    return Record(
        type="patent",
        id=id_,
        title=title,
        summary=summary,
        url=f"https://patents.google.com/patent/{id_}",
        ts="2026-08-11T00:00:00+00:00",
        raw={},
    )


class _RecordingAgent:
    """Stand-in for a real agent node: counts calls, never touches an LLM."""

    def __init__(self) -> None:
        self.calls: list[AgentState] = []

    def __call__(self, state: AgentState) -> dict:
        self.calls.append(state)
        return {"agent_notes": [f"recorded {len(state['gaps'])} gap(s)"]}


class InitialStateTest(unittest.TestCase):
    def test_defaults_are_empty_and_use_gap_thresholds(self):
        from einstein.gaps import DEFAULT_PATENT_THRESHOLD, DEFAULT_REPO_THRESHOLD

        state = initial_state("quantum computing")
        self.assertEqual(state["domain"], "quantum computing")
        self.assertEqual(state["repo_threshold"], DEFAULT_REPO_THRESHOLD)
        self.assertEqual(state["patent_threshold"], DEFAULT_PATENT_THRESHOLD)
        self.assertEqual(state["papers"], [])
        self.assertEqual(state["repos"], [])
        self.assertEqual(state["patents"], [])
        self.assertEqual(state["gaps"], [])
        self.assertEqual(state["agent_notes"], [])

    def test_thresholds_overridable(self):
        state = initial_state("x", repo_threshold=0.5, patent_threshold=0.6)
        self.assertEqual(state["repo_threshold"], 0.5)
        self.assertEqual(state["patent_threshold"], 0.6)


class BuildGraphCompileTest(unittest.TestCase):
    def test_compiles_with_real_fetcher_defaults_without_touching_network(self):
        # Compiling wires nodes/edges; it must not call any fetcher. Only
        # `.invoke()` would, and this test never invokes.
        graph = build_graph()
        self.assertIsNotNone(graph)


class GraphEndToEndTest(unittest.TestCase):
    """The bead's literal acceptance criterion: the graph compiles and runs
    end-to-end on fixture data with zero gaps, short-circuiting to END
    without running the agents node."""

    def test_zero_gaps_short_circuits_to_end_without_running_agents(self):
        papers = [_paper("p1", "SGD convergence bounds", "convergence bounds for stochastic gradient descent")]
        repos = [_repo("o/sgd-bounds", "sgd-bounds", "convergence bounds for stochastic gradient descent")]
        agent = _RecordingAgent()

        graph = build_graph(
            fetch_papers=lambda domain: papers,
            fetch_repos=lambda domain: repos,
            fetch_patents=lambda domain: [],
            agent_node=agent,
        )
        result = graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))

        self.assertEqual(result["gaps"], [])
        self.assertEqual(result["agent_notes"], [])
        self.assertEqual(agent.calls, [], "agents node must not run when detect_gaps finds nothing")
        # ingest populated state from the injected fetchers, not left empty
        self.assertEqual(result["papers"], papers)
        self.assertEqual(result["repos"], repos)
        self.assertEqual(result["patents"], [])

    def test_nonzero_gaps_routes_into_agents_node(self):
        papers = [_paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
        repos = [_repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]
        agent = _RecordingAgent()

        graph = build_graph(
            fetch_papers=lambda domain: papers,
            fetch_repos=lambda domain: repos,
            fetch_patents=lambda domain: [],
            agent_node=agent,
        )
        result = graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))

        gap_kinds = {g.subject_id: g.kind for g in result["gaps"]}
        self.assertEqual(gap_kinds, {"p2": "true_invention_gap", "o/k8s-tool": "unformalized_code"})
        self.assertEqual(len(agent.calls), 1, "agents node must run exactly once")
        self.assertEqual(result["agent_notes"], ["recorded 2 gap(s)"])

    def test_ingest_passes_domain_as_query_to_every_fetcher(self):
        seen_queries: dict[str, str] = {}

        def record(name):
            def fetcher(domain):
                seen_queries[name] = domain
                return []
            return fetcher

        graph = build_graph(
            fetch_papers=record("papers"),
            fetch_repos=record("repos"),
            fetch_patents=record("patents"),
        )
        graph.invoke(initial_state("photonic computing"))

        self.assertEqual(seen_queries, {
            "papers": "photonic computing",
            "repos": "photonic computing",
            "patents": "photonic computing",
        })

    def test_empty_everything_is_a_valid_zero_gap_run(self):
        agent = _RecordingAgent()
        graph = build_graph(
            fetch_papers=lambda domain: [],
            fetch_repos=lambda domain: [],
            fetch_patents=lambda domain: [],
            agent_node=agent,
        )
        result = graph.invoke(initial_state("nothing fetched"))

        self.assertEqual(result["gaps"], [])
        self.assertEqual(agent.calls, [])


class StoreWiringTest(unittest.TestCase):
    """einstein-0.3: `analyze` persists and dedupes gaps against a `Store`
    when one is injected, and leaves behavior byte-for-byte unchanged when
    it is not (the `store=None` default every other test in this file
    exercises)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "einstein.db"

    def _graph(self, papers, repos, *, store):
        return build_graph(
            fetch_papers=lambda domain: papers,
            fetch_repos=lambda domain: repos,
            fetch_patents=lambda domain: [],
            store=store,
        )

    def test_store_none_leaves_scored_gaps_empty(self):
        papers = [_paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
        repos = [_repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]

        graph = self._graph(papers, repos, store=None)
        result = graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))

        self.assertEqual(len(result["gaps"]), 2)
        self.assertEqual(result["scored_gaps"], [])

    def test_first_run_marks_every_gap_new_and_persists_it(self):
        papers = [_paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
        repos = [_repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]

        with Store(self.db_path) as store:
            graph = self._graph(papers, repos, store=store)
            result = graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))

            self.assertEqual(len(result["scored_gaps"]), 2)
            self.assertTrue(all(sg.is_new and sg.trend == "new" for sg in result["scored_gaps"]))
            stored_keys = {row["gap_key"] for row in store.all_gaps()}
            self.assertEqual(stored_keys, {g.gap_key for g in result["gaps"]})

    def test_second_run_on_the_same_store_sees_prior_gaps_as_not_new(self):
        papers = [_paper("p2", "Tensor network attention", "novel tensor contraction for transformer attention")]
        repos = [_repo("o/k8s-tool", "k8s-tool", "command line tool for kubernetes deployment pipelines")]

        with Store(self.db_path) as store:
            first_graph = self._graph(papers, repos, store=store)
            first_graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))

            second_graph = self._graph(papers, repos, store=store)
            result = second_graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))

            self.assertEqual(len(result["scored_gaps"]), 2)
            self.assertTrue(all(not sg.is_new for sg in result["scored_gaps"]))
            self.assertTrue(all(sg.age_days is not None and sg.age_days >= 0 for sg in result["scored_gaps"]))

    def test_zero_gap_run_still_scores_an_empty_list_without_error(self):
        papers = [_paper("p1", "SGD convergence bounds", "convergence bounds for stochastic gradient descent")]
        repos = [_repo("o/sgd-bounds", "sgd-bounds", "convergence bounds for stochastic gradient descent")]

        with Store(self.db_path) as store:
            graph = self._graph(papers, repos, store=store)
            result = graph.invoke(initial_state("optimization", repo_threshold=0.3, patent_threshold=0.3))

            self.assertEqual(result["gaps"], [])
            self.assertEqual(result["scored_gaps"], [])
            self.assertEqual(store.all_gaps(), [])


if __name__ == "__main__":
    unittest.main()
