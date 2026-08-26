# einstein-5: USPTO patents fetcher — handoff

## Status
Closed. Bead was already `in_progress` (claimed 2026-08-10, assignee `sandbox`)
with no code on disk and no notes — nothing to inherit or distrust. Built it
fresh this session.

## What I changed

- **New: `einstein/uspto_fetcher.py`** — `fetch_patents(query, *, max_results=10,
  api_key=None, http=None) -> list[Record]`, following the shape of the
  existing `github_fetcher.py` / `openalex_fetcher.py` (typed `_HttpClient`
  Protocol, `http=` injection point for tests, a module-specific exception
  class).
- **New: `tests/test_uspto_fetcher.py`** — 16 tests, network-free (fake HTTP
  client, no real requests).

Command that proves the module imports and the tests pass:

```
uv run python -m unittest discover tests
```

Verbatim final line:

```
Ran 84 tests in 0.055s

OK
```

(84 = the pre-existing 68 across arxiv/embedding/github/openalex/schema/store
+ 16 new USPTO tests. Full discover output before this change was not
separately captured, but `git diff` scopes the two new files, and rerunning
discover with `tests/test_uspto_fetcher.py` deleted reproduces 68 if you want
to check that split yourself.)

## Acceptance criterion: "missing key raises a clear error"

`fetch_patents` resolves the key via `api_key` argument, falling back to the
`USPTO_API_KEY` env var. If neither is set (or the value is an empty string),
it raises `USPTOFetchError(missing_key=True)` **before making any HTTP
call** — verified by `test_no_key_anywhere_raises_before_any_request`, which
asserts `http.calls == []` after the raise. The error message names the exact
fix (`set USPTO_API_KEY or pass api_key=...`) and states why there's no
fallback.

Distinguished from a rejected-but-present key: a 401 from USPTO with a key
configured raises `USPTOFetchError(missing_key=False, status_code=401)` —
different failure, different flag, checked by
`test_401_bad_key_raises_and_is_not_flagged_missing_key`.

## Mock-patent fallback: removed, not migrated

`gemini_convo.md` lines 330–372 (`fetch_uspto_patents`) fall back to a
hardcoded `MOCK-PATENT-01` record — title `f"System and Method for {query}
Optimization"`, summary `f"Hardware acceleration and algorithmic techniques
for {query}."` — whenever the API key is absent or the request throws. That
fallback does not exist in `einstein/uspto_fetcher.py`; there is no code path
in the new module that returns a `Record` not derived from an actual 200
response.

Verification: `grep -rn "MOCK-PATENT\|mock_patent\|Dummy fallback" einstein/
tests/ main.py` matches only in `uspto_fetcher.py`'s docstring (explaining
what was deliberately not reproduced) and in the test named
`test_never_returns_mock_patent_data` — no fallback-producing code.

## Where I stated "no prior art" carefully

I didn't — this bead is the fetcher only, not the gap-detection matrix
(that's einstein-11, which depends on this). The only place this module
talks about absence is the docstring's framing of *why* the mock fallback is
gone: "a fabricated 'no prior art found' silently poisons gap detection" —
describing the hazard being avoided, not making a claim about any specific
query's results. `fetch_patents` itself never asserts non-existence; a
zero-result 200 response returns `[]` and logs
`"USPTO search returned zero results for query=%r"`, which is exactly
"nothing found in this index/threshold," not "novel."

## What I could not verify

**The exact endpoint shape.** I used `https://api.uspto.gov/api/v1/patent/search`
(GET, `X-API-KEY` header, `q`/`rows` params, response shaped
`{"patentData": [...]}`), matching the URL and field names in the
`gemini_convo.md` draft this bead is explicitly built from. I tried to
independently confirm this against USPTO's current Open Data Portal docs via
web search/fetch and got **inconsistent results across three separate
fetches** (different base URLs, different header names each time) — data.uspto.gov
appears to be a JS-rendered SPA that WebFetch can't read, so I could not get
a reliable second source. I did not invent a "more confident-looking" but
unverified alternative; I kept the draft's shape since that's what the bead
text points to, and flagged this rather than asserting the endpoint is
correct. **This code has never been run against the real USPTO API and the
request/response shape is unverified.** Per repo policy this is not tested
against a live network call either way — if the actual API differs, calls
will fail loud with a `USPTOFetchError(status_code=...)` or a JSON-shape
`KeyError`/empty-list, not silently return wrong data, but a human with API
access should sanity-check one real call before this is relied on for
anything.

**`store.py` / `test_store.py`** were already untracked in the working tree
when I started (not part of this bead, not something I wrote). I left them
untouched — they're out of scope for einstein-5.

## What I decided not to do

- **Did not wire `fetch_patents` into `main.py`.** `main.py` is currently a
  placeholder (`print("Hello from einstein!")`) with no fetcher wired in at
  all, including arxiv/github/openalex — wiring is evidently a later bead's
  job (likely part of einstein-11, the three-way gap matrix), not this one.
- **Did not pursue a POST-with-JSON-body variant of the search API** that
  some USPTO ODP endpoints reportedly use for structured queries — the draft
  used a simple GET with query params, and I matched that rather than
  guessing at a richer, unverified request shape.
- **Did not add retry/backoff logic.** GitHub and OpenAlex fetchers don't
  have it either; consistent with the repo's "fetchers raise, callers decide
  policy" pattern already established.

## Commands to run (not run by me — git policy is conservative)

```
git add einstein/uspto_fetcher.py tests/test_uspto_fetcher.py
git commit -m "einstein-5: add USPTO patents fetcher, no mock fallback"
```

`bd close einstein-5` was already run this session (see reason text in bead
history). No `bd dolt push` was run — no Dolt remote is configured
(`bd close` printed an auto-export warning about this on every close in this
session; that's a repo-wide/pre-existing condition, not something new I
caused).
