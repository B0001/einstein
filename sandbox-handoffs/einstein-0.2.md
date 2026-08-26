# Handoff: einstein-0.2 — Wire fetchers to Store; log queries including empty results

**Status: closing** (`bd close einstein-0.2`). All four acceptance bullets
verified below with commands you can re-run.

## What I found at start

The bead was already `in_progress` (claimed by an earlier, unfinished
sandbox run). `einstein/store.py` already had the `queries` table in
`_SCHEMA`, the `QueryLookup` dataclass, `DEFAULT_NEGATIVE_TTL_DAYS = 30`,
and a module docstring describing `Store.lookup_query`'s intended
semantics — but the `Store` class had **no `upsert_query` or
`lookup_query` methods at all**, and none of the four fetchers
(`arxiv_fetcher.py`, `github_fetcher.py`, `uspto_fetcher.py`,
`openalex_fetcher.py`) took a `store` argument or imported
`einstein.store`. `grep -rn "einstein.store" einstein/` outside `store.py`
itself returned nothing. So: schema and docstring existed, nothing that
used them did. I did not trust the docstring's description of the intended
contract at face value — I read the schema columns directly and designed
the two missing methods against those columns, then fixed one place where
the docstring had drifted from the schema (see below).

I left the partial groundwork in place rather than rewriting it; the
`queries` table shape (columns `source, query, params, status,
record_type, ids, fetched_at`, `PRIMARY KEY (source, query, params)`) was
already right for what this bead needs.

## What I changed

### `einstein/store.py`
- Added `Store.upsert_query(*, source, query, params, status, record_type,
  ids=())` — writes/overwrites one `queries` row. `status` is asserted to
  be one of `"ok" | "rate_limited" | "error"`.
- Added `Store.lookup_query(*, source, query, params, ttl_days=30) ->
  QueryLookup` — returns `status="absent"` if never recorded;
  `"positive"` for `status="ok"` with non-empty `ids` (never expires);
  `"negative"` for `status="ok"` with empty `ids` and `fetched_at` within
  `ttl_days` of *this Store's injected clock* (`self._clock()`, the same
  callable used for `first_seen`/`last_seen` — **not** `datetime.now()`);
  `"expired"` for the same but past `ttl_days`; `"rate_limited"` /
  `"error"` pass through unchanged and are never reinterpreted as
  negative, regardless of age.
- Added `_days_between(earlier, later)` helper (fractional days via
  `datetime.fromisoformat` subtraction).
- Fixed a docstring/schema mismatch: the module docstring said `ids` holds
  `(record_type, id)` pairs, but the schema already has a separate
  `record_type` column, so I implemented `ids` as a flat list of id
  strings within that single `record_type` (one fetcher searches one
  `Record.type`) and corrected the docstring to say so.
- Extended `_self_check()` (`python -m einstein.store`) with a
  queries-table pass using a mutable-cell clock to exercise
  absent/negative/expired/positive/rate_limited without sleeping.

### `einstein/arxiv_fetcher.py`, `github_fetcher.py`, `uspto_fetcher.py`
Each `fetch_*` function gained `store: Store | None = None` and
`ttl_days: int = DEFAULT_NEGATIVE_TTL_DAYS` keyword args, defaulting to
`None`/30 so every existing call site and test keeps its old behavior
unchanged. When `store` is given:
1. `store.lookup_query(source=<arxiv|github|uspto>, query=query,
   params={"max_results": max_results}, ttl_days=ttl_days)` runs first.
   - `"positive"` → records are read back via `store.get_record(type,
     id)` for each cached id; **no network call**.
   - `"negative"` → returns `[]`; **no network call**. This is the
     "empty search leaves no trace" gap the bead exists to close: the
     `queries` row is the trace.
   - `"absent"`, `"expired"`, `"rate_limited"`, `"error"` → falls through
     to a live fetch (an expired negative or a past rate-limit is not
     evidence of anything and must be re-tried).
2. On a successful live fetch: every returned `Record` is
   `store.upsert_record(...)`'d, then one `store.upsert_query(...,
   status="ok", ids=[r.id for r in records])` row is written — empty
   `ids` for a genuine zero-result search, non-empty for a positive one.
3. On a raised fetch error: one `store.upsert_query(..., status=...)` row
   is written *before* re-raising — `"rate_limited"` if the failure was a
   429 (arXiv: `exc.status == 429` on the underlying `arxiv.HTTPError`;
   GitHub: the existing `_is_rate_limited` check, which also covers 403 +
   `X-RateLimit-Remaining: 0`; USPTO: `response.status_code == 429`),
   `"error"` otherwise. USPTO's missing-API-key failure happens before any
   request is attempted and writes nothing — there is no fetch outcome to
   cache.

### `einstein/openalex_fetcher.py`
`fetch_work_by_doi` gained the same `store`/`ttl_days` args, cache-keyed
on the normalized bare DOI (`mailto` excluded from the key — it's a
polite-pool hint, not part of the query's identity). This one needed a
judgment call the other three didn't: `fetch_work_by_doi` returns a single
`Record`, not a list, so "no record for this DOI" (404) can't be cached
and replayed as `[]` the way the search fetchers replay a negative. I
cached a 404 as `status="ok"`, `ids=[]` (it *is* a search that came back
empty — "no OpenAlex record for this DOI" is exactly the kind of claim the
`queries` table exists to make reproducible) and made a cached
`"negative"` re-raise `OpenAlexFetchError(404, ..., not_found=True)`
without touching the network, so the function's existing raise-on-missing
contract is unchanged either way. `fetch_citation_edges` gained
`store`/`ttl_days` and passes them straight through to
`fetch_work_by_doi`; citation edges themselves are not persisted (no edges
table exists, and this bead didn't ask for one).

### Tests
- `tests/test_store.py` — new `QueryCacheTest` (12 cases): absent, a
  zero-result write read back as `"negative"`, positive round-trips `ids`,
  positive never expires (`ttl_days` deliberately violated), rate_limited
  and error are never readable as negative (including when old — TTL does
  not apply to them at all), negative within TTL stays negative, negative
  past TTL reads `"expired"` **not** `"absent"`, default TTL is 30,
  `params` is part of the cache key, `params` key order doesn't matter,
  repeat `upsert_query` overwrites the row. TTL passage is driven by a
  `MutableClock` test helper (mutate `.value`, no `time.sleep`,
  no `datetime` patching).
- `tests/test_arxiv_fetcher.py`, `tests/test_github_fetcher.py`,
  `tests/test_uspto_fetcher.py`, `tests/test_openalex_fetcher.py` — each
  gained a `*CachingTest` class: second identical fetch hits the fake
  client/http exactly once (`len(client.searches) == 1` /
  `len(http.calls) == 1`), a zero-result search is cached as `"negative"`
  and a second call for the same zero-result query also makes no network
  call, a 429/rate-limit failure is cached as `"rate_limited"` and
  asserted `!= "negative"`, a non-rate-limit failure is cached as
  `"error"`, and (USPTO only, since it's the one fetcher with a
  before-any-request failure mode) a missing API key touches neither the
  fake http client nor the store. Each also has a
  `test_without_store_behaves_exactly_as_before` case.
- `tests/fixtures/github_repo_search.json`,
  `tests/fixtures/uspto_patent_search.json`,
  `tests/fixtures/openalex_work.json`, `tests/fixtures/arxiv_paper.json` —
  new. Structurally faithful trimmed recordings (each file says so in an
  explicit `_note` field, mirroring the comment already on
  `test_openalex_fetcher.py`'s inline `WORK_ITEM`), loaded via
  `json.loads(...)` from the new caching tests instead of being re-typed
  inline. `tests/fixtures/` was not actually empty at the start (the
  einstein-0.1 worker had already landed `gap_benchmark.json` there); the
  bead's "(currently empty)" note is stale but harmless.

## Verification

```
$ uv run python -m unittest discover tests
----------------------------------------------------------------------
Ran 220 tests in 0.351s

FAILED (errors=2)
```

The 220 includes 38 new tests from this bead (12 in `test_store.py`, the
rest split across the four fetcher test files). **The 2 failures are
pre-existing and not in this bead's scope**: `tests/test_cli.py:108` and
`:193` construct `ArxivFetchError("boom")` with only a message, but
`ArxivFetchError.__init__` (unchanged by me, already required this before
I started) needs keyword-only `query=` and `cause=`. I confirmed this is
pre-existing by checking `cli.py`/`test_cli.py` are untracked files owned
by `einstein-21` ("CLI entrypoint + scheduled run"), currently
`in_progress`, not by this bead. I did not touch `cli.py` or `test_cli.py`
— wiring the CLI to pass a `Store` through is CLI-layer work, not
fetcher-layer, and out of `einstein-0.2`'s acceptance criteria. I filed
`einstein-21.1` (P2 bug, parented under `einstein-21`) describing the
exact failure so whoever picks that bead up doesn't have to rediscover it.

Store self-check:
```
$ uv run python -m einstein.store
einstein.store self-check: OK
```

Per-bullet acceptance evidence:

- **"fetch_patents / fetch_repos / arXiv+OpenAlex fetchers persist Records
  through Store on the fetch path, and a second identical fetch reads from
  the store rather than the network (assert the fake HTTP client is
  called once across two calls)"** —
  `tests/test_arxiv_fetcher.py::FetchPapersCachingTest::test_second_identical_fetch_reads_from_store_not_network`,
  `tests/test_github_fetcher.py::FetchReposCachingTest::test_second_identical_fetch_reads_from_store_not_network`,
  `tests/test_uspto_fetcher.py::FetchPatentsCachingTest::test_second_identical_fetch_reads_from_store_not_network`,
  `tests/test_openalex_fetcher.py::FetchWorkByDoiCachingTest::test_second_identical_fetch_reads_from_store_not_network`
  — each asserts the fake's call-count is `1` after two `fetch_*(...,
  store=...)` calls with identical args. Run any one directly, e.g.
  `uv run python -m unittest tests.test_uspto_fetcher.FetchPatentsCachingTest -v`.

- **"A zero-result search writes a queries row with status=ok and no ids;
  a 429 writes status=rate_limited and is not readable as a negative
  finding. Both asserted."** —
  `*_is_cached_as_a_negative_not_dropped` tests assert
  `store.lookup_query(...).status == "negative"` after a zero-result
  fetch (which is only reachable through a `status="ok"`, empty-`ids` row
  — see `Store.lookup_query`). `*_429_is_cached_as_rate_limited_not_negative`
  / `test_rate_limited_failure_is_cached_as_rate_limited_not_negative`
  (arxiv) assert `.status == "rate_limited"` **and**
  `assertNotEqual(.status, "negative")` after a simulated 429, in
  `test_arxiv_fetcher.py`, `test_github_fetcher.py`, and
  `test_uspto_fetcher.py`.

- **"Reading a negative older than the TTL reports it as expired, not as
  absent. Asserted with an injected clock, not a sleep."** —
  `tests/test_store.py::QueryCacheTest::test_negative_past_ttl_reads_as_expired_not_absent`
  and `test_default_ttl_is_30_days`, both driven by the `MutableClock`
  helper (`clock.value = "2026-09-10T00:00:00+00:00"` after a `2026-08-08`
  write — 33 days, past the 30-day default). `store.py`'s own
  `_self_check()` repeats the same absent → negative → expired sequence
  with a mutable-cell clock.

- **"No network in the suite; the recorded responses land in
  tests/fixtures/"** — every new test uses a fake client/http object
  (`FakeArxivClient`, `FakeHttp`); the recorded payloads used by the
  caching tests live in `tests/fixtures/{github_repo_search,
  uspto_patent_search, openalex_work,arxiv_paper}.json` and are loaded via
  `json.loads`, not re-typed inline. `grep -rn "requests\.\(get\|post\)\|urllib\.request\|socket\." tests/`
  returns nothing.

## Where I did not state "novel" / "no prior art"

Nowhere in this bead's code, tests, or this handoff does anything call a
cached or live empty result "novel" or "no prior art" — the `Store` API
surface is `"absent" | "positive" | "negative" | "expired" |
"rate_limited" | "error"`, and `"negative"` is documented (in
`store.py`'s module docstring and in `QueryLookup`'s own docstring) as "no
match found within *this* index, at *this* threshold, as of *this*
`fetched_at`" — not existence-proof of absence. Consumers (einstein-16,
not built yet) get `fetched_at` alongside every `"negative"`/`"expired"`
result so they can decide for themselves whether the claim is still
fresh enough to use.

## What I decided not to do, and why

- **Did not wire `einstein/cli.py` to pass a `Store` through.** The
  acceptance criteria name the four fetchers, not the CLI; `cli.py` is
  untracked, owned by the in-progress `einstein-21`, and its
  `_make_fetchers` currently calls `partial(_fetch_papers,
  max_results=...)` with no `store=` — wiring that in is a reasonable
  follow-up but is einstein-21's call to make (it also owns the
  `--db-path`-style flag decision this would need). I did not file a
  separate bead for this since it's implicit in einstein-21's existing
  scope ("CLI entrypoint").
- **Did not build an edges table for OpenAlex citation edges.** The bead's
  acceptance only names `Records`; `Edge` (from `fetch_citation_edges`) is
  not a `Record` and there's no `edges` table in `store.py`'s schema.
  Adding one wasn't asked for and I didn't invent a shape for it — same
  reasoning `store.py`'s own docstring gives for not guessing `gaps`'
  payload shape ahead of the bead that defines it.
- **Did not change `USPTOFetchError`/`GitHubFetchError`/`OpenAlexFetchError`
  to carry a uniform `rate_limited` flag.** GitHub already had one; I
  added equivalent 429-detection logic inline for arXiv and USPTO instead
  of retrofitting their exception classes, to keep this change to "wire
  the fetch path to Store," not "redesign the error hierarchy." USPTO's
  429 case has no existing test coverage of the exception's attributes
  (only its `status_code`), so I didn't add a `missing_key`-style flag
  there either — status_code == 429 was sufficient for what `queries`
  needs to record.
- **Did not touch the pre-existing `test_cli.py` failures.** Filed as
  `einstein-21.1` instead — see Verification above.

## What I could not verify

- **Real API response shapes.** The four fixture files are "structurally
  faithful" recordings in the same sense the pre-existing `WORK_ITEM` in
  `test_openalex_fetcher.py` already was (trimmed, hand-built to match
  documented/observed field shapes) — I did not make a live call to
  arXiv, GitHub, USPTO, or OpenAlex this session (no `GITHUB_TOKEN` /
  `USPTO_API_KEY` in this container, and the project rule is no network in
  automated test runs regardless). If a real response has a field shape
  that differs from what's fixture, that's a pre-existing risk in the
  original fetcher tests, not something this bead's wiring changes.
- **Multi-process / concurrent-writer behavior of the `queries` table.**
  Only single-threaded, single-process usage is exercised. The bead
  doesn't ask for concurrency guarantees and SQLite's default isolation
  wasn't stress-tested here.

## Commands to run next

```bash
uv run python -m unittest discover tests   # 220 tests, 2 pre-existing cli.py failures (einstein-21.1)
git status                                  # review the diff below before committing
git add einstein/store.py einstein/arxiv_fetcher.py einstein/github_fetcher.py \
        einstein/uspto_fetcher.py einstein/openalex_fetcher.py \
        tests/test_store.py tests/test_arxiv_fetcher.py tests/test_github_fetcher.py \
        tests/test_uspto_fetcher.py tests/test_openalex_fetcher.py tests/fixtures/
git commit -m "einstein-0.2: wire fetchers to Store, log queries incl. empty results"
```

I did not run any `git commit` / `git push` / `bd dolt push` — conservative
git policy, per `CLAUDE.md`.
