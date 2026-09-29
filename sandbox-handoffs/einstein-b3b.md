# einstein-b3b handoff

USPTO search API's `q=` param is not relevance-ranked -- returns near-whole-index
counts sorted by date.

## What I changed

**`einstein/uspto_fetcher.py`** — `fetch_patents` now always sends
`sort=_score desc` in its request params (previously sent no `sort` at all):

```python
params = {"q": query, "rows": max_results, "sort": "_score desc"}
```

Justifying command (before/after comparison against the live API, same query,
same day):

```
USPTO_API_KEY=... uv run python scripts/uspto_search_recall_check.py
```

Output (relevance-demo section) showed, for the unquoted natural-language
query `"A method for generating droplets in a microfluidic channel and
transferring a chemical reagent between droplets using an electric field to
induce coalescence"`:

- WITHOUT sort override (USPTO's default order): top 3 titles were about
  screen UI controls, RNA formulations, and photonics gyroscopes — no
  topical connection to the query.
- WITH `sort=_score desc` (what `fetch_patents` now sends): top 3 titles
  were all genuinely droplet/microfluidics/reagent patents.
- `count` was **identical either way**: 12,916,985. The sort reorders the
  returned page; it does not narrow the match set.

Also added a "Search relevance (einstein-b3b)" section to the module
docstring (`einstein/uspto_fetcher.py` lines 30-88) documenting the full
characterization below, and updated `fetch_patents`'s own docstring to
explain why the sort is unconditional.

**`tests/test_uspto_fetcher.py`** — added
`test_sort_by_score_always_requested`, asserting every request's `params["sort"]
== "_score desc"` via a `FakeHttp`/`FakeResponse` (no network).

**`scripts/uspto_search_recall_check.py`** — extended the existing hand-run
live-check script (from einstein-tix) with:
- `_run_relevance_demo`: the before/after comparison quoted above.
- `KNOWN_GRANTED_PATENT_NUMBER = "12735810"` and a rewritten
  `_run_grant_text_check` that looks this patent up directly via the
  field-scoped query `applicationMetaData.patentNumber:12735810`, instead of
  hoping a topical query's top page happens to contain a granted patent.
  This was necessary because the sort fix is a *self-inflicted regression
  fix*: before it, `_run_grant_text_check` relied on the `"10x Genomics
  droplet"` query surfacing a granted patent by luck under recency-sort;
  under relevance-sort, that query's top page skews toward older/more-cited
  patents generally, but on this specific query returned 0/25 granted
  results in one live run. Decoupling the grant-text check from any
  topical query's luck makes it reliable regardless of how the index or
  ranking shifts day to day.

## Final test-run line

```
uv run python -m unittest discover tests
```
→ `Ran 454 tests in 0.720s` / `OK`

(454 = 453 before this session's one new test. The USPTO-key-missing and
404 lines printed during the run are expected output from tests
deliberately exercising those error paths, not failures.)

## Characterization: what `q=` actually does (live, not guessed)

No official docs were reachable this session: the USPTO developer portal's
linked PDF 301-redirects to a dead page (`data.uspto.gov/home`), and guesses
at `/api-docs`, `/swagger.json` on `api.uspto.gov` all 403'd. Everything
below is from systematic live querying against `api.uspto.gov` on
2026-09-29, reproducible with the `curl` shapes described here or via
`scripts/uspto_search_recall_check.py`.

**Backend is Elasticsearch's `query_string` query.** Confirmed via:
- Verbatim ES error messages, e.g. requesting `sort=score` (wrong field
  name) returns `"No mapping found for [score] in order to sort on"` — an
  ES-internal error string, not a USPTO-authored one.
- Support for syntax unique to `query_string`: quoted exact phrases,
  boolean operators (`AND`/`OR`/`NOT`/`-term`), wildcards (`quant*`), and
  dotted-path field-scoping (`applicationMetaData.inventionTitle:quantum`
  works; bare `inventionTitle:quantum` 404s — the bead's original guess at
  a field-scope path was wrong).

**Default is OR-of-terms, not AND, not phrase.** Every `count` below is from
a live `GET` against `https://api.uspto.gov/api/v1/patent/applications/search`
with only `q` varied (reproduce with `curl -H "X-API-KEY: $USPTO_API_KEY" -G
--data-urlencode "q=<query>" https://api.uspto.gov/api/v1/patent/applications/search
| uv run python -m json.tool` and read `.count`):

| query | count |
|---|---|
| `quantum error correction` (unquoted) | 926455 |
| `quantum OR error OR correction` | 926455 (identical) |
| `error` alone | 49451 |
| `correction` alone | 865708 |
| `"quantum error correction"` (quoted phrase) | 158 |
| `quantum AND error AND correction` | 239 |
| realistic method paragraph, quoted | 404 HTTP status, "No matching records found" |
| realistic method paragraph, unquoted | 12916985 |
| `applicationMetaData.inventionTitle:quantum` | 16448 |

The unquoted multi-word count matching the OR-joined count exactly (both
926455) is the direct evidence for "default operator is OR." Quoting or
AND-joining narrows correctly for a short known phrase (158 / 239) but
collapses a realistic natural-language paragraph (what `novelty_auditor`
actually sends as `idea.method`) to **zero** hits — a 404, not an empty
200. That rules out phrase-quoting/AND-joining as this fetcher's default
query strategy: it would trade "returns the wrong stuff" for "returns
nothing," which is worse for a gap detector that needs recall.

**Default sort is filing date descending (recency), not relevance.**
Confirmed by comparing a request with no `sort` param against one with
`sort=applicationMetaData.filingDate desc` — identical result sets and
order both times.

**`sort=_score desc` fixes ranking without narrowing recall** — the fix
shipped. See the before/after in the "What I changed" section above.

**`count` must never be read as a relevance or no-conflict signal**, before
or after this fix. It stays in the hundreds-of-thousands-to-millions range
for any ordinary multi-word query regardless of topical relevance. This
fetcher does not expose `count` on `Record` for exactly this reason.

**Caveat, stated plainly**: this is a handful of hand-chosen queries against
one day's live index, not a formal benchmark. Whether `sort=_score desc`
gives adequate precision across the broader space of queries
`novelty_auditor` actually generates is unmeasured — that open question is
`einstein-uts` (see below), not resolved by this bead.

## Where I was tempted to write "novel" / "no prior art" and what I wrote instead

In the module docstring, describing what a loose OR-match-then-recency-sort
was doing to search results, I was tempted to write something like "this
means the fetcher was missing real prior art." I did not write that —
`fetch_patents` returning irrelevant-but-nonzero results is a precision
problem in what gets *returned*, not evidence about what does or doesn't
exist in USPTO's index. I wrote instead: "count must never be read as a
relevance or no-conflict signal" and confined every claim to what was
directly observed (specific counts, specific titles, specific HTTP codes).
Nowhere in the diff does the word "novel" or a claim of "no prior art"
appear — the fetcher has no verdict-producing logic; that lives in
`novelty_auditor.py`, which I did not touch.

## What I decided not to do, and why

- **Did not change `novelty_auditor.py`'s Audit/verdict semantics.** The
  bead's third acceptance criterion asks to "decide (with a human) whether
  `novelty_auditor` should down-weight or skip USPTO 'no conflict found'
  results" given this limitation. There is no human in this autonomous
  session to actually have that conversation, and unilaterally changing
  production trust semantics without sign-off would overstep what the bead
  asked for. Instead I filed `einstein-uts` (P2, task, labeled `human`) so
  it surfaces via `bd human list` for an actual human decision. Confirmed:
  ```
  bd human list
  ```
  → lists `einstein-uts`.
- **Did not adopt phrase-quoting or AND-joining as the default query
  strategy** — explained above (kills recall to zero on realistic
  paragraphs).
- **Did not fix two side-bugs discovered while characterizing this API** —
  out of this bead's scope per the task's explicit instruction to file
  new beads rather than fix incidentally-discovered issues:
  - `einstein-n7n` (P2, bug): USPTO returns HTTP 404, not `200` + empty
    array, for genuine zero-hit searches. `fetch_patents` currently treats
    any non-200 as a hard `USPTOFetchError`, so a real "nothing matches"
    result is indistinguishable from an actual API failure.
  - `einstein-zfp` (P3, bug): some pending-application `applicationMetaData`
    shapes populate `effectiveFilingDate` instead of `filingDate`, which
    `_to_record`'s timestamp extraction doesn't check, silently falling back
    to `"1970-01-01"` for those records.

## What I could not verify

- Whether `sort=_score desc` is *sufficient* precision across the broad,
  varied query shapes `novelty_auditor` actually generates day to day —
  this session only checked a handful of hand-picked queries on one day's
  index. Left as the open question in `einstein-uts`.
- Official USPTO API documentation for `q=`'s semantics — never reachable
  this session (dead PDF redirect, 403s on doc-guess endpoints). Everything
  in this handoff is inferred from live behavior, not confirmed against a
  spec.
- Whether USPTO's ranking/index composition is stable day-to-day — the
  counts and top-result titles above are a snapshot from 2026-09-29 and
  will drift as the index updates (new filings, index rebuilds).

## Git status (left dirty, per repo policy — no commit/push/dolt push run)

```
$ git status --short
 M .beads/interactions.jsonl
 M .beads/issues.jsonl
 M einstein/novelty_auditor.py
 M einstein/patent_claims.py
 M einstein/uspto_fetcher.py
 M tests/fixtures/uspto_patent_search.json
 M tests/test_novelty_auditor.py
 M tests/test_uspto_fetcher.py
?? einstein/uspto_grant_text.py
?? sandbox-handoffs/einstein-9it.md
?? sandbox-handoffs/einstein-b3b.md
?? sandbox-handoffs/einstein-tix.md
?? scripts/uspto_search_recall_check.py
?? tests/fixtures/uspto_grant_text.xml
?? tests/fixtures/uspto_grant_text_no_abstract.xml
?? tests/test_uspto_grant_text.py
```

The `einstein/novelty_auditor.py`, `einstein/patent_claims.py`,
`tests/fixtures/uspto_patent_search.json`, `tests/test_novelty_auditor.py`,
`einstein/uspto_grant_text.py`, and the `uspto_grant_text*` fixtures/tests
predate this session (einstein-tix / einstein-9it work, still uncommitted
in this tree). This session's own changes are limited to:
`einstein/uspto_fetcher.py` (sort param + docstring),
`tests/test_uspto_fetcher.py` (new test), and
`scripts/uspto_search_recall_check.py` (relevance demo + grant-text-check
robustness fix).

Suggested commands for a human to review and run (not executed):

```
git add einstein/uspto_fetcher.py tests/test_uspto_fetcher.py scripts/uspto_search_recall_check.py sandbox-handoffs/einstein-b3b.md
git commit -m "einstein-b3b: always sort USPTO search by _score, not recency"
```

(This leaves the einstein-tix/einstein-9it uncommitted work as a separate
concern for a human to commit separately, as noted in
`sandbox-handoffs/einstein-9it.md`.)

## Beads filed this session

- `einstein-n7n` (P2, bug, open) — USPTO 404s on genuine zero-hit queries.
- `einstein-zfp` (P3, bug, open) — `effectiveFilingDate` field-name gap.
- `einstein-uts` (P2, task, open, labeled `human`) — the down-weighting
  decision this bead's third acceptance criterion asks a human to make.

## Bead resolution

Closing `einstein-b3b` as done: characterization complete (systematic live
querying, not guessing), a better option was found and shipped
(`sort=_score desc`, verified live and by a new unit test), and the
"decide with a human" criterion is satisfied by flagging `einstein-uts` for
human review rather than making that call unilaterally. Full test suite
passes (454/454).
