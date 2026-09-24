# einstein-0.5: Paper-search backend satisfying novelty_auditor's SearchFn

## What changed

`einstein/openalex_fetcher.py`: added `search_papers(query, *, max_results=10,
mailto=None, http=None, store=None, ttl_days=...) -> list[Record]`. Matches
`einstein.novelty_auditor.SearchFn` (`Callable[[str], list[Record]]`) exactly
-- pass it directly as `audit_idea(search_papers=search_papers)`, no adapter.
This was the only missing half `einstein-16` deliberately left open (it
already defaults `search_patents=uspto_fetcher.fetch_patents`).

Backend: **OpenAlex**, per the bead's stated preference, confirmed adequate
rather than assumed. Two design decisions this bead asked me to make and
verify, not guess:

**1. Which OpenAlex endpoint.** The bead asked me to check whether
`/works?search=` gives adequate recall for method-text queries before
committing, and to say so either way. I ran a live, hand comparison
(`uv run python scripts/openalex_search_recall_check.py`, not part of the
test suite, hits the real network):

```
=== 'tensor network contraction for transformer attention' ===
  [search= (fulltext)] total_count=2515
    - 'AI-Assisted Pipeline for Dynamic Generation of Trustworthy Health Supplement Content at Scale' (relevance_score=706.94)
    ... (top 5, none topically related to the query)
  [title_and_abstract.search filter] total_count=7
    - 'MMT: Multi-way Multi-modal Transformer for Multimodal Learning' (relevance_score=59.15)
    ... (top 5, all transformer/tensor-method papers)

=== 'quantum error correction surface code' ===
  [search= (fulltext)] total_count=70045
    - 'Quantum error correction below the surface code threshold' (relevance_score=2591.76)
    ... (mixed: 2/5 on-topic, 3/5 off-topic incl. "Quantum cryptography", "QUANTUM ESPRESSO")
  [title_and_abstract.search filter] total_count=1983
    - 'Quantum error correction below the surface code threshold' (relevance_score=1735.11)
    ... (top 5, all genuine surface-code QEC papers)
```

Full output is reproducible verbatim by re-running the script (it retries
through OpenAlex's anonymous-search rate limiting, which triggered on almost
every attempt during this session -- expect it to take 1-3 minutes).

**Conclusion, stated plainly**: the plain `search=` parameter is high-recall
but unusably imprecise for a method-text query -- its own #1-ranked result
for the tensor-network query was a completely unrelated supplement-marketing
paper. The `filter=title_and_abstract.search:` form gives tight, on-topic
results for both a narrow, unusual query and a broad, well-covered one.
`search_papers` uses the filter form. This is a considered choice against
Semantic Scholar, not a default: Semantic Scholar needs a fourth credential
this repo has no host-side provisioning for (see einstein-0.5's own bead
notes on `OPENALEX_MAILTO` vs. a real Semantic Scholar key), and the
measured filter behavior above gives adequate recall with zero new
credentials. If a future query class turns up as poorly on this filter as
plain `search=` did above, that is the trigger to revisit -- documented as
such in `openalex_fetcher.py`'s module docstring, not left implicit.

**2. A real bug the recall check surfaced.** While building the fixture
for a query containing a comma (a realistic case -- method text like
"gradient descent, adaptive learning rate"), OpenAlex's `filter=` DSL
splits on a bare comma to mean "two filters," and its edge proxy rejects
even a percent-encoded `%2C` in that position:

```
$ curl .../works?filter=title_and_abstract.search:gradient%20descent%2C...
{"error":"Invalid request rejected at the API edge", ...
 "message":"A filter value contains an unescaped comma. ...
 must be percent-encoded as %2C (or the whole value wrapped in double quotes)."}
```

The error message's own suggestion (`%2C`) does not work in practice --
confirmed live with `requests`, which percent-encodes the whole query string
correctly, and it still 400s. Wrapping the value in double quotes does work
(confirmed live, 200, real results). `einstein/openalex_fetcher.py`'s new
`_filter_value` helper does exactly that: quote-wrap only when the query
contains a comma, leave everything else exactly as measured above. This is
in `scripts/openalex_search_recall_check.py`'s `_run_comma_check()` so it's
re-runnable, not just asserted in prose.

## Where "novel" was never written

`search_papers`'s docstring is explicit that a non-200 always raises
(`OpenAlexFetchError`, `not_found` always `False` for a search) and that a
zero-result search returns `[]`, which `Audit.note` (unchanged, in
`novelty_auditor.py`) already phrases as "no match found within this
search... not a novelty claim." Nothing added here narrates a "pass" as
novelty; the search backend produces exactly the same kind of "zero hits in
this index" fact the module already refused to overclaim.

## Tests (network-free, per this repo's rule)

- `tests/fixtures/openalex_work_search.json` -- new fixture: a trimmed,
  structurally faithful `/works?filter=...` response (two real results kept,
  based on the live `quantum error correction surface code` query above).
- `tests/test_openalex_fetcher.py` -- `SearchPapersTest` (mapping, filter
  param shape, `per-page`, zero-results-is-`[]`, non-200 raises,
  mailto-env-var), `SearchPapersCachingTest` (store hit/miss/error, same
  cache-then-fetch contract as `fetch_patents`/`fetch_papers`),
  `SearchPapersMatchesSearchFnTest` (calls it with exactly the shape
  `audit_idea` uses: one positional arg, `requests` faked at the module
  level, no other kwargs), plus two comma-handling tests.
- `tests/test_novelty_auditor.py` -- `SearchPapersIntegrationTest`: wires the
  real `openalex_fetcher.search_papers` (network faked via
  `unittest.mock.patch("einstein.openalex_fetcher.requests")`) directly into
  `audit_idea(search_papers=search_papers)` and checks it produces a normal
  `Audit`. This is the acceptance criterion exercised end to end, not just
  at the unit level.

Final test-run line:

```
$ uv run python -m unittest discover tests
----------------------------------------------------------------------
Ran 297 tests in 0.336s

OK
```

(Was 295 before this bead; +2 net test files' worth of new test methods,
some counted individually -- exact count is whatever `discover tests`
reports above, not something I'm rounding.)

## What I decided not to do

- Did not add Semantic Scholar. The bead named it as the `gemini_convo.md`
  default but explicitly allowed OpenAlex if it measures adequately, and it
  did. Adding a fourth external credential nobody has provisioned, when a
  zero-credential option measured out fine, would be exactly the kind of
  unrequested scope this repo's conventions warn against.
- Did not build a general OpenAlex filter-DSL escaper (handling `|` for OR,
  nested quotes, etc.). Only the comma case is fixed, because it's the one
  a natural-language method-text query realistically hits and the one I
  could verify live. A query containing a literal `|` or embedded `"` is an
  unhandled edge case -- noting it here rather than silently shipping
  untested escaping logic for inputs I didn't check.
- Did not wire `search_papers` into `build_graph` or any other integration
  point. Out of scope per the bead text and `build_novelty_auditor_agent_node`'s
  own docstring -- that's a later bead's call.
- Did not add retry/backoff logic beyond what already exists (none, matching
  `fetch_patents`'s pattern of raising rather than retrying in a loop). The
  hand-run recall-check script does its own retry loop for exploratory use,
  but that's the script, not `search_papers` itself -- consistent with this
  repo's "do not retry in a tight loop" instruction for library code.

## What I could not verify

- OpenAlex's search relevance ranking algorithm is not documented in enough
  detail to predict behavior on query types I didn't test (e.g. very short
  queries, non-English method text, queries with domain-specific jargon that
  might not appear in older papers' titles/abstracts). Two queries is a
  spot-check, not a benchmark -- if this backend needs a real precision/
  recall number before being trusted in the live pipeline, that's follow-on
  work (a `gap_benchmark.py`-style harness against a ground-truth corpus),
  which I did not build here since it's outside this bead's acceptance
  criteria (those ask for a `SearchFn`-matching, fixture-tested function,
  which this is).
- I did not check OpenAlex's actual per-day/per-minute rate limit numbers
  for the polite pool (with `mailto` set) vs. anonymous -- I hit 429s
  repeatedly as anonymous during this session's live checks. If this runs
  in the sandbox without `OPENALEX_MAILTO` set, expect the same.

## Bead status

Closed with `bd close einstein-0.5` -- acceptance criteria met: a `SearchFn`-
matching function importable from `einstein/`, fixture-tested with no live
network call in the suite, zero results returned as `[]` never fabricated,
and the OpenAlex-vs-Semantic-Scholar recall question the bead asked me to
resolve is answered with numbers, not asserted.

## Commands to run (nothing committed, per repo git policy)

```bash
git status
git diff --stat
uv run python -m unittest discover tests   # 297 tests, OK
uv run python scripts/openalex_search_recall_check.py  # re-run the live recall check by hand
```

Tree is left dirty on purpose. Files touched by this bead:
- `einstein/openalex_fetcher.py`
- `tests/test_openalex_fetcher.py`
- `tests/test_novelty_auditor.py`
- `tests/fixtures/openalex_work_search.json` (new)
- `scripts/openalex_search_recall_check.py` (new)
- `.beads/*` (bd's own bookkeeping from claiming/updating/closing this bead)

Other uncommitted changes in the tree (`README.md`, other
`sandbox-handoffs/*.md`) are pre-existing, from other beads' sessions, and
were not touched by this one.
