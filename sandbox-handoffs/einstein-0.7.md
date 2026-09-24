# einstein-0.7: Wire constraint/friction mining (einstein-9) into CLI + report

## Decision

The bead asked for a product decision on one thing; implemented as follows:

**Cluster persistence.** Yes, persist widespread clusters through the same
`Store`/`velocity.score_gaps` machinery gaps and cross-pollination candidates
already use, under exactly `kind="constraint_cluster"` (the bead's own
suggested name). Implemented via a new duck-typed adapter,
`cli._ConstraintClusterGap`, mirroring the existing `cli._CrossPollinationGap`
pattern from einstein-0.3 — `Store.upsert_gap`'s `kind` parameter and
`velocity.score_gaps`'s scored object are already open string / duck-typed
respectively (not the closed `GapKind` Literal in `einstein/gaps.py`), which
is what makes this possible without widening that closed set.

Two sub-decisions inside that, both written into `_ConstraintClusterGap`'s
own docstring in `einstein/cli.py`:

- `gap_key` is the sorted, pipe-joined `source_ids` of the cluster
  (`f"constraint_cluster:{'|'.join(sorted(cluster.source_ids))}"`). This
  means a cluster whose *membership* changes between runs (a source drops
  out, or a new source joins) gets a new `gap_key` and reads as "new" rather
  than "the same cluster, grown" — a real limitation, not fixed, because
  inventing a more stable cross-run identity (e.g. hashing member texts, or
  tracking cluster lineage) would fabricate a precision the underlying
  single-link clustering doesn't actually have run-to-run (see below,
  "what was deliberately not done").
- `matches` is always `()` — a constraint cluster has no natural
  `against_type="repo"` `Match` the way an invention gap does, and
  `velocity.py`'s "rising" trend is defined specifically off that match
  type. A constraint cluster can therefore only ever report `new` / `dormant`
  / `stable`, never `rising`. This is a real gap in what velocity can say
  about this gap kind, not a bug — documented rather than worked around.

## What changed

- `einstein/cli.py` (target confirmed correct: `einstein/report.py` — the
  einstein-20 Proposal-report module — exposes `render_json`/
  `render_markdown`, not `render_text`; `cli.py` has exactly the
  `render_text`/`render_json` pair the bead names, and is where the
  einstein-0.3 cross-pollination precedent for "opt-in extra CLI step" also
  lives):
  - New imports: `einstein.arxiv_source.{NoTexSourceError, extract_source as
    _extract_source}`, `einstein.constraint_mining.{DEFAULT_SIMILARITY_THRESHOLD
    as DEFAULT_CONSTRAINT_SIMILARITY_THRESHOLD, PainPointCluster,
    cluster_pain_points, pain_points_from_issues, pain_points_from_source,
    widespread_clusters}`, `einstein.github_fetcher.fetch_issues as
    _fetch_issues` (module-level aliases, matching the existing
    `_fetch_papers`/`_fetch_repos`/`_fetch_patents`/`_search_papers`/
    `_fetch_citation_edges` convention — these are the tests' patch points).
  - New flags: `--mine-constraints OWNER/REPO` (repeatable,
    `action="append"`), `--constraint-similarity-threshold` (default
    `constraint_mining.DEFAULT_SIMILARITY_THRESHOLD`).
  - New `_ConstraintClusterGap` frozen dataclass adapter (see Decision).
  - `run()` return type widened from a 3-tuple
    (`state, status, cross_pollination`) to a 4-tuple, adding
    `constraint_mining: dict | None` — `None` unless `--mine-constraints`
    was passed.
  - New `_run_constraint_mining(args, state, store)`: validates every
    `--mine-constraints` repo is already in `state["repos"]` (the run's
    fetched corpus — a hard requirement per the bead's "for repos already in
    the run's repo corpus" phrasing, not just descriptive text; an unknown
    repo raises `SourceFetchError(source="constraint_mining", ...)` before
    any fetch happens), fetches issues per repo via `_fetch_issues`, extracts
    LaTeX source per paper in `state["papers"]` via `_extract_source`
    (catching `NoTexSourceError` as a counted skip — an expected "this paper
    has no TeX source" outcome, not a failure — while any other
    `ArxivSourceError` propagates as `SourceFetchError`, preserving the
    existing "no partial report on failure" contract), builds `PainPoint`s
    from both, clusters via `cluster_pain_points`, filters to
    `widespread_clusters`, persists+velocity-scores each via
    `_ConstraintClusterGap`/`store`/`velocity.score_gaps`, and returns a
    summary dict: `repos`, `papers_considered`, `papers_without_tex_source`,
    `pain_points_mined`, `clusters` (count), `widespread_clusters` (list of
    payload dicts, each with a `velocity` sub-dict).
  - `render_json`/`render_text` both gained a trailing
    `constraint_mining: dict | None = None` parameter. `render_json` adds
    `report["constraint_mining"]` when not `None`. `render_text` appends a
    block reporting repos/papers-considered/papers-skipped/pain-points-mined
    → clusters → widespread-cluster count and, per widespread cluster, its
    `source_ids`/member count/velocity — with the same "friction reported by
    more than one source in this pass, not a severity or unsolved-elsewhere
    claim" framing the module docstring for `constraint_mining.py` already
    uses.
  - `main()` updated for the 4-tuple unpack and the extra `render(...)` arg.
  - Module docstring: new section documenting `--mine-constraints`, the
    repo-must-already-be-fetched rule, `NoTexSourceError` handling, and the
    `_ConstraintClusterGap` persistence approach.
- `README.md`: new "Constraint & friction mining (`--mine-constraints`,
  `einstein-9`/`einstein-0.7`)" subsection, same documentation depth as the
  existing Cross-pollination section (flag, off-by-default rationale, the
  repo-corpus precondition, `NoTexSourceError` handling, `kind:
  "constraint_cluster"` persistence, threshold flag).
- `tests/test_cli.py`:
  - New imports: `einstein.arxiv_source.{NoTexSourceError, Section,
    SourceExtraction}`, `einstein.github_fetcher.{GitHubFetchError, Issue}`.
  - All 8 pre-existing call sites of `cli.run(args)` that unpack the return
    tuple updated from 3-tuple to 4-tuple unpacking (calls inside
    `assertRaises` blocks, which don't unpack a return value, were untouched
    — verified by the full-suite pass below that nothing was missed).
  - `BuildParserTest`: extended the defaults test with
    `mine_constraints is None` / `constraint_similarity_threshold ==
    cli.DEFAULT_CONSTRAINT_SIMILARITY_THRESHOLD`; new
    `test_mine_constraints_repeatable_and_threshold_overridable`.
  - `RunTest`: six new tests —
    `test_mine_constraints_not_requested_leaves_it_none`,
    `test_mine_constraints_unknown_repo_raises_source_fetch_error`,
    `test_mine_constraints_fetches_issues_and_paper_source_and_persists_widespread_cluster`
    (asserts mock call args for `_fetch_issues`/`_extract_source`, every
    field of the returned `constraint_mining` dict, and that
    `Store.all_gaps()` contains a row with `kind == "constraint_cluster"`),
    `test_mine_constraints_no_tex_source_is_counted_not_a_failure`,
    `test_mine_constraints_live_fetch_failure_raises_source_fetch_error`.
  - `MainTest`: new
    `test_mine_constraints_report_renders_widespread_clusters`, covering both
    the plain-text and `--json` render paths end-to-end through `cli.main()`.

## Verification

```
export UV_PROJECT_ENVIRONMENT=/tmp/venv
uv run python -m unittest discover tests
```
→
```
Ran 409 tests in 0.526s

OK
```
(run at the end of this session, after the README edit, as the final check.)

`tests/test_cli.py` alone (`uv run python -m unittest tests.test_cli -v`):
```
Ran 26 tests in 0.144s

OK
```
(was 19 before this session — 26 - 19 = 7 net new tests: 1 in
`BuildParserTest`, 5 in `RunTest`, 1 in `MainTest`.)

`git diff --stat einstein/cli.py tests/test_cli.py`:
```
 einstein/cli.py   | 456 +++++++++++++++++++++++++++++++++++++++++++++++++++---
 tests/test_cli.py | 347 +++++++++++++++++++++++++++++++++++++++--
 2 files changed, 776 insertions(+), 27 deletions(-)
```

Isolation check — confirmed my session's edits are scoped to exactly those
two files plus `README.md` (this handoff), and did not touch any of the
other in-progress beads' uncommitted work already present in this shared
working tree (`einstein/github_fetcher.py`, `einstein/graph.py`,
`einstein/store.py`, etc. — pre-existing diffs from other bead sessions, not
mine): `git status --short | grep -E "cli\.py|test_cli\.py"` shows only
` M einstein/cli.py` / ` M tests/test_cli.py`.

Also ran a manual `--help` invocation
(`uv run einstein --help`) during implementation to confirm `--mine-constraints`
and `--constraint-similarity-threshold` render correctly in argparse's
generated help text, and a manual smoke-test script exercising `cli.run()`
with `_fetch_issues`/`_extract_source` monkeypatched to confirm the wiring
end-to-end outside of the unittest harness — both network-free, both
succeeded (not re-run at handoff time since the unit tests already cover the
same code paths more rigorously; not re-quoted here to avoid stating a number
that isn't independently regeneratable the way the two test-run lines above
are).

## Numbers reported and where they come from

- **409 tests, all passing** — literal final line of
  `uv run python -m unittest discover tests` above. Includes other
  in-progress beads' test files already in the tree; this run confirms this
  bead's changes don't break them, not that this bead wrote all 409.
- **26 tests in `tests/test_cli.py`, up from 19** — literal line of
  `uv run python -m unittest tests.test_cli -v` above; the "19" baseline is
  from the prior session's handoff (`sandbox-handoffs/einstein-0.3.md`),
  cross-checked against this session's diff (7 new `def test_` blocks added
  to `tests/test_cli.py`, all under `--mine-constraints`).
- **776 insertions / 27 deletions across 2 files** — literal
  `git diff --stat` output above.
- No other numeric claims in this handoff, and nothing here (or in the code
  it describes) is phrased as "novel" or "no prior art". The places I was
  tempted to reach for that language were the `render_text` widespread-
  cluster block and the README section describing what a cluster means;
  both instead say a cluster is "friction reported by more than one source
  in this pass, not a severity or unsolved-elsewhere claim" — extending
  `einstein/constraint_mining.py`'s own module-docstring framing ("evidence
  that these particular texts, in this particular run, embedded close to
  each other above a threshold this run chose... not a claim that the
  underlying constraint is real, severe, or unsolved elsewhere") verbatim
  into the CLI-facing text rather than writing new, weaker-hedged language.

## What was deliberately not done, and why

- **`einstein/report.py` was not touched.** The bead's phrase "renders
  widespread clusters in `render_text`/`render_json`" names functions that
  only exist in `einstein/cli.py` — `report.py` has `render_json`/
  `render_markdown` instead. Concluded `cli.py` is the correct (and only
  plausible) target; did not also add constraint-mining output to
  `report.py`'s differently-named/differently-scoped renderers, since that
  would be inventing scope the bead didn't ask for.
- **No cross-run stable cluster identity beyond sorted `source_ids`.**
  Considered giving `_ConstraintClusterGap.gap_key` something more durable
  (a hash of member texts, or a lineage tracker matching clusters across
  runs by best-overlap), rejected it: `cluster_pain_points` itself doesn't
  guarantee stable membership run-to-run (new papers/issues shift the
  embedding space's pairwise similarities), so any "stable identity" built
  on top would imply a precision the clustering doesn't actually have. A
  cluster that changes membership honestly reads as "new" in velocity
  output rather than silently claiming continuity it can't back up. This is
  documented as a known limitation in `_ConstraintClusterGap`'s docstring,
  not silently accepted.
- **`GapKind` in `einstein/gaps.py` was not widened.** Same reasoning
  einstein-0.3 used for `cross_pollination`: it's a closed `Literal` that
  `einstein/gaps.py`'s own self-check asserts against
  (`{g.kind for g in gaps} == set(GAP_KINDS)`); widening it to include
  `"constraint_cluster"` would break that invariant for no benefit, since
  `Store.upsert_gap`/`velocity.score_gaps` already accept the duck-typed
  adapter without it.
- **Did not add a "papers with no LaTeX source at all" retry or fallback
  extraction path.** `NoTexSourceError` is counted and skipped per the
  existing `arxiv_source.py` contract (a paper legitimately not having TeX
  source on arXiv is normal, not a defect to work around); building an
  alternate extraction path (e.g. from the HTML abstract page) is out of
  this bead's scope and would be new detector work, not CLI wiring.
- **Did not attempt to dedupe pain points across multiple `--mine-constraints`
  runs beyond what `cluster_pain_points`/velocity already do.** No new
  caching layer was added for `_fetch_issues`/`_extract_source` — issue
  fetches have no `Store`-backed cache in `github_fetcher.py` (see that
  module's own docstring: "no `Store` caching here either... there is no
  `Record` to hand back on a cache hit"), and `_extract_source` reads
  through `arxiv_source.py`'s existing behavior unchanged. Wiring a new
  cache layer into either of those modules is out of this bead's scope
  (CLI wiring, not fetcher/extractor changes).
- **`pyproject.toml`/`uv.lock` and other files' diffs in the working tree are
  not mine.** Pre-date this session, left by other in-progress bead work
  sharing this container's `/workspace`. Not staged, not spoken to beyond
  this note.

## What could not be verified

- **No live GitHub or arXiv network call was made this session.** Every
  `_fetch_issues`/`_extract_source` call in both the automated tests and the
  manual smoke test was mocked/monkeypatched, per repo policy (tests must
  not touch network). The actual behavior of `--mine-constraints` against
  real GitHub issues and real arXiv e-print archives — real label coverage,
  real TeX-source availability rate, real clustering quality on genuine
  paraphrase drift between a paper's prose and an issue reporter's prose —
  is unverified beyond what `tests/test_constraint_mining.py`'s existing
  (pre-existing, not written this session) unit coverage of
  `cluster_pain_points` already establishes. A hand-run check against a
  small number of real repos/papers would be a reasonable next step for
  whoever reviews this, but was not attempted here (would require live
  `GITHUB_TOKEN` and outbound network access this sandboxed session may not
  have anyway).
- **Velocity `trend` values for constraint clusters were only exercised for
  `new`** (first-time-seen) in the test suite — `dormant`/`stable` paths are
  covered generically by `einstein/velocity.py`'s own test suite (not part
  of this session's changes) but not specifically re-exercised here for the
  `constraint_cluster` kind across multiple `cli.run()` invocations against
  the same `--db`. The `matches=()` behavior (constraint clusters can never
  show `rising`) is verified only by reading `velocity.py`'s trend logic,
  not by a dedicated multi-run test in this session.

## Handoff for a human (git)

Working tree is intentionally left dirty. Suggested commands:

```bash
git add einstein/cli.py tests/test_cli.py README.md \
        sandbox-handoffs/einstein-0.7.md
git commit -m "einstein-0.7: wire constraint/friction mining into CLI + report"
```

Left out of that `git add` on purpose: every other-bead uncommitted file
already in this shared working tree (`einstein/github_fetcher.py`,
`einstein/graph.py`, `einstein/store.py`, and whatever else `git status`
currently shows beyond the four paths above) — none of those are mine to
stage or speak to. Run `git status` before staging to confirm the current
full picture.

Did **not** run `git commit`, `git push`, `bd dolt push`, or any other
sync/publish command this session — per standing container policy
(`BEADS_ACTOR=sandbox`; pre-commit/pre-push hooks refuse these) and the
explicit no-commit/no-push instruction for this task. No hook was hit
because no commit was attempted.
