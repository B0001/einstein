# einstein-6: OpenAlex/Semantic Scholar fetcher + citation edges

Status: **closed**. Bead's acceptance criterion ("one seed DOI returns >0
outbound citation edges") is met and verified both in the test suite and
against the live API.

## What I changed

- **New file: `einstein/openalex_fetcher.py`**
  - `fetch_work_by_doi(doi, *, mailto=None, http=None) -> Record` — looks up
    one work by DOI via `GET https://api.openalex.org/works/doi:{doi}`,
    maps it to the shared `Record` schema (`type="paper"`). `summary` is
    reconstructed from OpenAlex's `abstract_inverted_index` (word -> list of
    positions) into plain text, since OpenAlex doesn't return abstract text
    directly.
  - `fetch_citation_edges(doi, *, mailto=None, http=None) -> list[Edge]` —
    calls `fetch_work_by_doi` and turns its `referenced_works` array into
    `Edge(from_id, to_id)` pairs (both OpenAlex short IDs, e.g. `W2741809807`).
    This is the citation adjacency einstein-12 (cross-pollination detection)
    depends on.
  - `OpenAlexFetchError(status_code, message, not_found=bool)` — raised on
    any non-200. `not_found=True` for a 404 (DOI not in OpenAlex) vs. `False`
    for a transient failure (5xx, rate limit) — mirrors the distinction
    `ArxivFetchError`/`GitHubFetchError` already make. A raised error is
    never conflated with "found, cites nothing" (`fetch_citation_edges`
    returning `[]`).
  - Only scoped OpenAlex, not Semantic Scholar, despite the bead title
    naming both — see "What I decided not to do" below.

- **New file: `tests/test_openalex_fetcher.py`** — 16 tests, all against a
  `FakeHttp` stand-in for `requests` (no network), fixture payload trimmed
  from a real OpenAlex response. Covers: Record mapping, abstract
  reconstruction (present/absent), bare-DOI and `https://doi.org/...`-form
  DOI input, 404 vs. 500 error distinction, `mailto` param sourcing
  (env var / explicit arg / absent), and the acceptance criterion itself
  (`test_seed_doi_returns_outbound_citation_edges`, asserting `len(edges) >
  0` and checking the exact edge set).

## Command whose output justifies each claim

**Full suite, after the change:**

```
$ uv run python -m unittest discover tests
...
----------------------------------------------------------------------
Ran 57 tests in 0.013s

OK
```

(57 = the 41 pre-existing tests + 16 new ones in `test_openalex_fetcher.py`.)

**Live API verification** (hand-run, not part of `discover tests`, per repo
policy — this is the actual evidence for the bead's acceptance criterion,
since the unit tests only prove the code handles the *shape* I assumed
correctly, not that the real API matches that shape):

```
$ uv run python -c "
from einstein.openalex_fetcher import fetch_citation_edges, fetch_work_by_doi
record = fetch_work_by_doi('10.7717/peerj.4375')
print('record id:', record.id)
print('title:', record.title)
print('summary (first 80 chars):', record.summary[:80])
edges = fetch_citation_edges('10.7717/peerj.4375')
print('num outbound edges:', len(edges))
print('sample edges:', edges[:3])
"
record id: W2741809807
title: The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles
summary (first 80 chars): Despite growing interest in Open Access (OA) to scholarly literature, there is a
num outbound edges: 54
sample edges: [Edge(from_id='W2741809807', to_id='W1560783210'), Edge(from_id='W2741809807', to_id='W1724212071'), Edge(from_id='W2741809807', to_id='W1767272795')]
```

Seed DOI `10.7717/peerj.4375` → **54 outbound edges**, > 0. Acceptance
criterion met against the real API, not just the mock.

## Where I was tempted to say "novel" / "no prior art"

Nowhere in this bead — it's a fetcher, not a detector. The only
epistemically loaded word here is "citation edge" itself: an edge means "the
seed work's OpenAlex record lists this as a reference", which is a fact
about OpenAlex's index, not a claim about what the paper actually cites (OCI
extraction from PDFs is imperfect and OpenAlex's coverage of older/non-OA
works is incomplete). I did not encode that caveat into the code — it
belongs in einstein-12 when these edges are turned into a "no connection
found" claim, and that bead's acceptance work should carry the caveat
forward, not silently rely on "citation edge" meaning "ground truth".

## What I decided not to do, and why

- **Semantic Scholar was not implemented**, despite being in the bead
  title ("OpenAlex/Semantic Scholar fetcher"). The acceptance criterion only
  requires one source with outbound citation edges from a seed DOI, and
  OpenAlex's `referenced_works` field gives that in a single unpaginated
  call with no API key. Semantic Scholar's citations endpoint would
  duplicate that capability with a second auth/rate-limit surface (S2 asks
  for an API key for reasonable throughput) for no marginal coverage this
  bead's acceptance criterion needs. If cross-pollination detection
  (einstein-12) later turns out to need S2-specific coverage (e.g. its
  citation-context sentences, which OpenAlex doesn't expose), that's new
  scope — I'd file a bead for it rather than build it speculatively now.
- **No inbound-citation fetch** (`cited_by` / who cites the seed). The bead
  only asks for outbound edges. OpenAlex exposes `cited_by_api_url` on every
  work if that's wanted later; adding it now would be a dependency added for
  something not asked for.
- **No pagination / multi-hop traversal** (following `referenced_works`
  transitively to build a deeper graph). Bead scope is "one seed DOI -> its
  direct outbound edges." A traversal depth policy belongs with whatever
  consumes the adjacency table (einstein-12), not the fetcher.
- **No persistence of the edges.** einstein-2 (SQLite persistence layer) is
  a separate, currently-open bead — `fetch_citation_edges` returns
  in-memory `Edge` objects and leaves storage to that layer, matching how
  `arxiv_fetcher`/`github_fetcher` also just return `list[Record]`.

## What I could not verify

- **Rate-limit / throttling behavior under sustained load** — I made one
  live call by hand; I did not stress-test OpenAlex's stated rate limits
  (this fetcher doesn't send `mailto` by default, so it's in the anonymous
  pool). If einstein-12 ends up calling this in a loop over many seed DOIs,
  someone should watch for 429s and decide whether to set `OPENALEX_MAILTO`
  or add backoff — I did not add retry logic since the bead didn't ask for
  it and CLAUDE.md says not to retry in a tight loop without evidence it's
  needed.
- **DOI normalization edge cases** beyond bare-DOI and `https://doi.org/...`
  prefix stripping (e.g. `doi:` prefix, URL-encoded DOIs, case sensitivity)
  — untested because no such input appeared in scope; OpenAlex's own DOI
  matching is case-insensitive per their docs, but I did not verify that
  independently.

## Handoff

```bash
git status        # confirm: new einstein/openalex_fetcher.py, tests/test_openalex_fetcher.py
uv run python -m unittest discover tests   # 57 tests, OK
bd show einstein-6   # closed, notes + reason recorded
```

No commit/push made — per repo git policy, tree is left ready to commit.
Suggested commit (not run):

```bash
git add einstein/openalex_fetcher.py tests/test_openalex_fetcher.py
git commit -m "einstein-6: OpenAlex fetcher + outbound citation edges"
```

einstein-12 (method-domain cross-pollination detection), which this bead
blocked, is now unblocked on this piece — though it still depends on
whatever einstein-2 (persistence) and the detection logic itself need.
