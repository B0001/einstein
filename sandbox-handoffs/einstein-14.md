# einstein-14: LangGraph state machine skeleton

Status: **closed**, evidence below.

## What I changed

New file `einstein/graph.py`. New file `tests/test_graph.py`. Added
`langgraph==1.2.10` to `pyproject.toml` (pinned exact, matching this
project's existing pin style) and let `uv add langgraph` update `uv.lock`.
Nothing else in the pipeline was touched — `einstein/gaps.py`,
`einstein/store.py`, the three fetchers, and their tests were already
committed/staged work from prior sessions; I read them for their contracts
and left them alone.

`build_graph(*, fetch_papers=..., fetch_repos=..., fetch_patents=...,
embedder=None, agent_node=...) -> CompiledStateGraph`:

- `AgentState` (`TypedDict`): `domain`, `repo_threshold`,
  `patent_threshold`, `papers`, `repos`, `patents`, `gaps`, `agent_notes`.
  `initial_state(domain, *, repo_threshold=..., patent_threshold=...)`
  builds one with every list field empty and thresholds defaulting to
  `einstein.gaps.DEFAULT_REPO_THRESHOLD` / `DEFAULT_PATENT_THRESHOLD` — the
  same constants `einstein-11` left as overridable, unvalidated defaults.
- **`ingest`** node: calls the three injected fetch functions with
  `state["domain"]` as the query, populates `papers`/`repos`/`patents`.
  Defaults to the real `arxiv_fetcher.fetch_papers` /
  `github_fetcher.fetch_repos` / `uspto_fetcher.fetch_patents` — so
  `build_graph()` with no arguments is the real pipeline graph — but every
  fetch function is a single injection point, so tests pass a lambda
  returning canned `Record`s and never touch the network.
- **`analyze`** node: calls `einstein.gaps.detect_gaps` on the ingested
  records with the state's thresholds and (optionally injected) embedder,
  populates `gaps`.
- **Conditional edge** (`_route_after_analyze`): `state["gaps"]` empty ->
  routes straight to `END`. Nonempty -> routes to `agents`. This is the
  literal "no LLM tokens burn when there's nothing to do" requirement from
  the bead.
- **`agents`** node: LLM-free stub by default
  (`_default_agent_stub`) — appends one string to `agent_notes` recording
  how many gaps it saw and that no ideator/auditor/feasibility/codegen is
  wired yet. It exists so the graph has a real node to route into and a
  later bead (`einstein-15`, blocked on this one) can swap it out via
  `build_graph(agent_node=...)` without touching `graph.py`. It does not
  call an LLM, matching this repo's rule that agent *nodes* need an LLM but
  *tests of the node's routing logic* must not.
- `openalex_fetcher` is deliberately not wired into `ingest` — it serves
  citation-edge lookups for `einstein-6`/`einstein-12`, a different role
  than the arXiv/GitHub/USPTO three-way matrix `einstein-11` built and this
  graph drives. Said so in the module docstring rather than silently
  omitting it.

## Test evidence

```
uv run python -m unittest discover tests
```
```
Ran 132 tests in 0.103s

OK
```
125 pre-existing + 7 new. `tests/test_graph.py` contributes exactly 7 test
methods across 4 `TestCase` classes — verified with
`unittest discover tests -v 2>&1 | grep -c '^test_'` (132 total) and
`... | grep -i graph` (7 lines, one per method, shown below). The 125
pre-existing count is more than the 108 the `einstein-11` handoff reported,
because `tests/test_gap_benchmark.py` and its fixture were added to the
tree by a different session after `einstein-11` closed and before this one
started — not something I added or need to explain further than "already
there at session start, per `git status`."

Module self-check (same pattern as every other module in this repo):
```
uv run python -m einstein.graph
```
```
einstein.graph self-check: OK
```

`tests/test_graph.py` covers:
- `initial_state` defaults (empty lists, thresholds pulled from
  `einstein.gaps`' constants) and override.
- `build_graph()` compiles with the real fetcher defaults and touches no
  network (compiling wires nodes/edges; only `.invoke()` calls a fetcher,
  and this test never invokes).
- **The bead's literal acceptance criterion** —
  `test_zero_gaps_short_circuits_to_end_without_running_agents`: a paper
  and a repo built to be a near-verbatim match (so `detect_gaps` finds no
  gap at `threshold=0.3`), run end-to-end through the compiled graph,
  asserting `result["gaps"] == []`, `result["agent_notes"] == []`, and a
  recording fake `agent_node` was called zero times.
- `test_nonzero_gaps_routes_into_agents_node`: an unrelated paper/repo pair
  (so `detect_gaps` yields both a `true_invention_gap` and an
  `unformalized_code` gap — two gaps from one paper/one repo, since
  `detect_gaps` reads the same paper x repo matrix by both row and column)
  asserts the agents node ran exactly once and `agent_notes` has exactly
  one entry.
- `test_ingest_passes_domain_as_query_to_every_fetcher`: all three
  injected fetchers see the same `domain` string as their query argument.
- `test_empty_everything_is_a_valid_zero_gap_run`: all three fetchers
  return `[]`, graph still runs end-to-end to `gaps == []` and agents not
  called (this also exercises `detect_gaps`'s own empty-corpus
  short-circuit from inside the graph, not just standalone).

## Numbers

None. This bead produces graph wiring and routing logic, not a measurement
— the only "numbers" in the fixtures (`repo_threshold=0.3`) are chosen to
force TF-IDF cosine similarity onto one side of the threshold in a unit
test, same caveat `einstein-11`'s handoff already gave for its own fixture
scores. Nothing here is a claim about real papers, repos, or patents.

I did not run this graph against live arXiv/GitHub/USPTO data. Doing so
would call `fetch_papers`/`fetch_repos`/`fetch_patents` with their real
defaults and would need a `USPTO_API_KEY` (the existing test suite already
demonstrates, via its captured stderr, that `USPTOFetchError` fires cleanly
without one — see the "USPTO fetch requires an API key" lines in the test
run output, which come from `tests/test_uspto_fetcher.py`, not from
anything I added). If a human wants to see this graph run against live
data: `uv run python -c "from einstein.graph import build_graph,
initial_state; g = build_graph(); print(g.invoke(initial_state('<domain>')))"`
— not run by me this session, since it costs real rate-limit budget on
three separate APIs for a skeleton bead that doesn't need it to satisfy its
acceptance criterion.

## What I decided not to do

- **Did not implement any real agent node.** `einstein-15` (ideator),
  blocked on this bead, is where that happens. `agents_node` here is
  intentionally a stub; building it out now would be scope creep into a
  bead I don't own this session, and it would have needed the LLM-call
  interface the top-level repo instructions say must be abstracted behind
  something with a deterministic test stub — that abstraction belongs to
  `einstein-15`, not this one.
- **Did not add retry/backoff around the fetchers.** The bead's job is
  wiring, not resilience. `ingest` lets `ArxivFetchError` /
  `GitHubFetchError` / `USPTOFetchError` propagate uncaught rather than
  swallowing them — a graph that silently treats "the API rate-limited me"
  the same as "zero results" would poison `detect_gaps` exactly the way
  `gaps.py`'s own docstring warns against. If a future bead wants retry
  logic, it's a deliberate addition, not something I backed in here.
- **Did not wire OpenAlex into `ingest`.** Explained above and in the
  module docstring — different role, different bead's concern
  (`einstein-6`/`einstein-12`).
- **Did not add a checkpointer, `store=`, or any persistence to
  `graph.compile()`.** `einstein/store.py` (SQLite) already exists as a
  separate persistence layer with its own `upsert_gap` contract that
  `einstein-11`'s `Gap.to_payload()` satisfies; wiring `detect_gaps`'s
  output into `Store` from inside this graph is CLI/orchestration
  territory (`einstein-21`, blocked on this bead), not this skeleton's job.
  I left `AgentState["gaps"]` as `list[Gap]` in memory, not persisted.
- **Did not add a new top-level CLI entrypoint.** That's `einstein-21`,
  explicitly blocked on this bead — I left `main.py` untouched.
- **Did not pin `langgraph` to a range.** Matched this repo's existing
  convention of exact `==` pins for every dependency in `pyproject.toml`.

## What I could not verify

- **Behavior against real API responses / real gap data.** As above — not
  run against live arXiv/GitHub/USPTO this session. The acceptance
  criterion is "runs end-to-end on fixture data with zero gaps," which is
  what I measured.
- **`agent_node` swap-in ergonomics for the actual `einstein-15`
  ideator.** I designed `agent_node: AgentState -> dict` as the extension
  point on the assumption a real ideator will want `state["gaps"]` and
  return updates to merge into state (LangGraph's reducer-less `TypedDict`
  state does a shallow dict update per node return, confirmed by the
  smoke-test I ran interactively before writing this module: sequential
  node returns overwrite rather than accumulate unless a node explicitly
  concatenates, which is why `_default_agent_stub` and my test fakes
  explicitly do `state["agent_notes"] + [...]`-equivalent list construction
  rather than relying on any automatic merge behavior). Whether
  `einstein-15`'s actual signature needs something richer (streaming,
  multiple agent sub-nodes, its own conditional routing) is unverified —
  that bead will find out when it's built.

## Commands to hand off

```bash
git status
git add einstein/graph.py tests/test_graph.py pyproject.toml uv.lock
git commit -m "..."   # not run -- git policy is hands-off this session
```

I did not `git add`, `git commit`, `git push`, or run `bd dolt push` — per
this repo's git policy, that's for a human to run. `bd close einstein-14`
was run this session (see below) since the acceptance criterion is met and
verified by the test evidence above.
