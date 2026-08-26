# Handoff: einstein-3 — arXiv fetcher

**Status:** closed (acceptance criteria met, evidence below).

## What changed

- `einstein/arxiv_fetcher.py` (new) — `fetch_papers(query, *, max_results=30, client=None) -> list[Record]`.
  Wraps `arxiv.Client().results(arxiv.Search(query=..., sort_by=arxiv.SortCriterion.SubmittedDate))`
  and maps each `arxiv.Result` to a `Record(type="paper", ...)`. Follows the same shape as
  the existing `einstein/github_fetcher.py`: a `_ArxivClient` Protocol for dependency injection
  so tests never touch the network, and a distinct `ArxivFetchError` so a real API failure
  (`arxiv.ArxivError` and its subclasses `HTTPError`, `UnexpectedEmptyPageError`) can't be
  confused with a genuine zero-results search (which returns `[]` without raising).
- `tests/test_arxiv_fetcher.py` (new) — 16 tests: mapping fidelity (id/title/summary/url/ts/raw),
  multiple results, zero results, sort-by-submitted-date and max_results passed through to
  `arxiv.Search`, and error handling for `HTTPError` / `UnexpectedEmptyPageError`, all against a
  `FakeArxivClient` — no network in the suite.

## Test suite

Command: `export UV_PROJECT_ENVIRONMENT=/tmp/venv; uv run python -m unittest discover tests`

```
Ran 44 tests in 0.008s

OK
```

(28 pre-existing tests for schema/embedding/github_fetcher + 16 new for arxiv_fetcher.)

## Acceptance criterion: "returns >=1 Record for 'quantum error correction'"

Verified live against the real arXiv API (not part of the automated suite — this was a
hand-run check per the repo's "tests must not touch the network" rule):

Command:
```
export UV_PROJECT_ENVIRONMENT=/tmp/venv
uv run python -c "
from einstein.arxiv_fetcher import fetch_papers
records = fetch_papers('quantum error correction', max_results=3)
print(len(records))
"
```
Output: `3` (3 Records returned, all `type == "paper"`).

## A correctness note I did NOT paper over

While doing the live check above, the three records that came back for the bare query
`'quantum error correction'` were about gravitational lensing, data assimilation, and
quantum de Sitter space — not error correction. I traced this to the arXiv API itself, not
a bug in my mapping code: an unquoted multi-word `search_query` is expanded by arXiv's own
server-side parser into an OR across `all:` of each individual word (confirmed via a raw
`curl` against `export.arxiv.org/api/query`, whose feed `<title>` literally echoes
`search_query=all:quantum OR all:error OR all:correction`). Sorted by `submittedDate`, that
OR surfaces whatever is newest and contains any one of "quantum"/"error"/"correction" —
which is exactly what happened.

Quoting the query as an exact phrase (`all:"quantum error correction"`) gives the expected
result — verified live, 3/3 relevant hits (titles: "Designer Codes from GALA: ... QEC on
Reconfigurable Atom Arrays", "Time-Reversal Selection Rules for Quantum Error Correction",
"Approximate Quantum Error Correction at Chiral Topological Edges").

I did **not** make the fetcher auto-quote every query, because the fetcher also needs to
support field-qualified / boolean queries (`cat:quant-ph AND ti:decoder`) that
auto-quoting would break — same reasoning as `github_fetcher.fetch_repos`, which also
passes `query` through to the API verbatim rather than rewriting it. Instead I documented
the OR-expansion behavior directly in `arxiv_fetcher.py`'s module docstring, since it is
exactly the kind of footgun that could silently turn a downstream "no match found" gap
claim into "no match found because the query fanned out to millions of loosely-related
papers and none of the top-N happened to overlap with GitHub/patents" rather than a genuine
absence. Any caller of `fetch_papers` building queries for gap detection (einstein-11) needs
to either quote phrases itself or accept OR semantics knowingly — it is now a stated
contract, not a hidden default. Filed as context here rather than a new bead because it is
not a defect in this bead's scope (the bead asked for "search by query," which this does
correctly per the arXiv API's own semantics) — it's a note for whoever writes the query
strings in einstein-11.

## What I decided not to do

- Did not add a retry loop around `arxiv.Client.results` — the `arxiv` package's `Client`
  already retries transient HTTP failures (`num_retries=3` default) and paces requests
  (`delay_seconds=3.0` default), matching this repo's own guidance not to retry-in-a-loop on
  top of a client that already backs off.
- Did not quote/rewrite the query string (see note above).
- Did not add full-text/LaTeX extraction — that's einstein-7, a separate bead that depends on
  this one being done; out of scope here.

## What I could not verify

- Behavior under actual arXiv rate-limiting (429/503) was verified only via a synthetic
  `FakeArxivClient` raising `arxiv.HTTPError`/`arxiv.UnexpectedEmptyPageError` — I did not
  reproduce a real 429 from arXiv (that would require hammering the live API, which the repo
  rules say not to do). The `arxiv` package's own retry/backoff logic inside `Client.results`
  is trusted as correct rather than independently verified here; if it misbehaves under real
  throttling, that is a bug in the third-party `arxiv==4.0.1` package, not in
  `einstein/arxiv_fetcher.py`.

## Suggested next commands (not run — conservative git policy)

```
git add einstein/arxiv_fetcher.py tests/test_arxiv_fetcher.py
git commit -m "einstein-3: add arXiv fetcher"
```

(`.beads/issues.jsonl` was also touched by `bd claim`/`bd close`; leaving that for the human
to decide whether to include, per the beads sync model — it's an export, not source of
truth.)
