# einstein-tix: USPTO fetcher's search URL and response-shape assumptions were wrong — handoff

## Status

Closing this session. All three "what would close this" items in the bead
are done and verified. Two follow-up beads filed for things discovered along
the way that are explicitly out of this bead's scope.

## What I changed, and the command that justifies each claim

**1. `einstein/uspto_fetcher.py` — fixed URL and response shape.**

- `USPTO_SEARCH_URL` changed from `.../api/v1/patent/search` (403s, AWS API
  Gateway "no route matches") to the confirmed-live
  `.../api/v1/patent/applications/search`:

  ```
  curl -s -o /dev/null -w '%{http_code}\n' \
    "https://api.uspto.gov/api/v1/patent/search?q=quantum&rows=1" \
    -H "Accept: application/json" -H "X-API-KEY: $USPTO_API_KEY"
  # 403

  curl -s -o /dev/null -w '%{http_code}\n' \
    "https://api.uspto.gov/api/v1/patent/applications/search?q=quantum&rows=1" \
    -H "Accept: application/json" -H "X-API-KEY: $USPTO_API_KEY"
  # 200
  ```

- `_to_record` rewritten for the real shape: results come back under
  `patentFileWrapperDataBag` (not `patentData`), and title/patent-number/dates
  live under each item's nested `applicationMetaData` (not top-level). Verified
  directly against a live response:

  ```
  curl -s "https://api.uspto.gov/api/v1/patent/applications/search?q=10x%20Genomics%20droplet&rows=25" \
    -H "X-API-KEY: $USPTO_API_KEY" -H "Accept: application/json" | python3 -m json.tool | head -40
  ```

  shows `patentFileWrapperDataBag[0].applicationMetaData.inventionTitle`,
  `.patentNumber`, `.grantDate`, `.filingDate`, and top-level
  `applicationNumberText` — the field names now in `_to_record`.

- Added the still-pending-application fallback path (`id` =
  `applicationNumberText`, `url` = `patentcenter.uspto.gov/applications/...`,
  `ts` falls back to `filingDate`) — this is a real code path, not
  speculative: live queries this session returned real applications with
  `applicationStatusCode: 150` ("Patented Case") but `patentNumber: None` (2
  of 25 in one sample), confirming the number can genuinely be absent even
  for allowed/granted-adjacent statuses.

**2. `einstein/uspto_grant_text.py` — new module, the bead's central open
question.** The search API has no abstract or claims field for either
pending applications or granted patents (confirmed: no such key appears
anywhere in `applicationMetaData` across every live response fetched this
session). What granted patents do carry is
`grantDocumentMetaData.fileLocationURI` — a link (through one redirect) to
USPTO's Patent Grant Full-Text XML bulk-data product, which has real
`<abstract>` and `<claims>` text, not a PDF. This is neither of the two
options the bead worried about ("PDF-only" or "no endpoint at all") — it's a
separate, decades-old bulk-data product reachable straight from the search
response. New module exposes `fetch_grant_text`, `parse_grant_xml`,
`record_with_grant_text`, `GrantText`, `GrantTextError`.

**3. `einstein/patent_claims.py` — docstring only, no logic change.**
Removed the stale "field name was not confirmed, 403 without authenticated
access" paragraph and replaced it with the confirmed source
(`uspto_grant_text.record_with_grant_text` populates `raw["claimsText"]`).
Verified the *existing, unmodified* `parse_claims`/`independent_claims_for_record`
correctly handle real grant-XML-derived claim text — no code changes needed:

```
uv run python -c "
from einstein.uspto_grant_text import parse_grant_xml
from einstein.patent_claims import parse_claims
data = open('tests/fixtures/uspto_grant_text.xml', 'rb').read()
claims = parse_claims(parse_grant_xml(data).claims_text)
print(len(claims), [c.is_independent for c in claims])
"
# 3 [True, False, False]
```

**4. Fixtures replaced/added:**
- `tests/fixtures/uspto_patent_search.json` — trimmed real response for
  US12735810 (granted) plus a hand-constructed pending-application entry
  (no live query surfaced one in top results this session; built using only
  field names confirmed live on every real document, omitting only the
  fields genuinely absent pre-grant).
- `tests/fixtures/uspto_grant_text.xml` — trimmed real grant XML (3 of the
  real 20 claims kept) for US12735810, fetched live this session.
- `tests/fixtures/uspto_grant_text_no_abstract.xml` — hand-authored, real
  bibliographic metadata (design patent D1149092, confirmed via live search
  to have `applicationTypeCode: "DES"` and no abstract field), illustrative
  claim text (I did not spend a grant-text download on a design patent I
  wasn't otherwise using).

**5. `tests/test_uspto_fetcher.py`** rewritten for the new shape and the
pending-application path. **`tests/test_uspto_grant_text.py`** added (13
tests: parse success/no-abstract/malformed-XML, fetch success/missing-URI/
missing-key/non-200/wrong-record-type, record-merge success/empty-abstract/
empty-claims/no-mutation, plus one test proving compatibility with the
unmodified `patent_claims` parser).

**6. `scripts/uspto_search_recall_check.py`** — new hand-run live script
mirroring `scripts/openalex_search_recall_check.py`. Ran it this session; see
"numbers" section below for its output.

## Final test-run line (verbatim)

```
uv run python -m unittest discover tests
```

```
Ran 448 tests in 0.625s

OK
```

(448 = 435 pre-existing, per einstein-tix's own bead text, + 13 new in
`tests/test_uspto_grant_text.py`. No tests were removed; `test_uspto_fetcher.py`
was rewritten in place, same test count as before modulo the one added
pending-application test.)

## Every number, and the command that regenerates it

- **448 tests, 0 failures** — `uv run python -m unittest discover tests`
  (above).
- **20 real claims in US12735810's grant XML, 3 kept in the trimmed
  fixture** — counted via
  `grep -c '<claim id=' tests/fixtures/uspto_grant_text.xml` → 3;
  original count confirmed via the live-fetched `/tmp/utl1.xml` during this
  session (not committed — see "what I could not verify" below for why the
  live grant-text fetch itself isn't reproducible on demand).
- **896,900 = the `count` field USPTO returned** for
  `q="droplet microfluidics reagent transfer"` — reproduce:
  ```
  curl -s "https://api.uspto.gov/api/v1/patent/applications/search?q=droplet%20microfluidics%20reagent%20transfer&rows=5" \
    -H "X-API-KEY: $USPTO_API_KEY" -H "Accept: application/json" | python3 -c "import json,sys;print(json.load(sys.stdin)['count'])"
  ```
  This is the evidence behind the `einstein-b3b` follow-up bead (search
  relevance) — filed, not fixed, see below.
- **1 independent claim extracted live** from US12735810 via the full
  search → grant-text-fetch → claims-parse pipeline — reproduce (uses one of
  that file's ~20/year download quota):
  ```
  USPTO_API_KEY=... uv run python scripts/uspto_search_recall_check.py
  ```
- **rows=5 and rows=20 both returned the same 25 items** for an identical
  query — reproduce:
  ```
  curl -s "https://api.uspto.gov/api/v1/patent/applications/search?q=10x%20Genomics%20droplet&rows=5"  -H "X-API-KEY: $USPTO_API_KEY" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['patentFileWrapperDataBag']))"
  curl -s "https://api.uspto.gov/api/v1/patent/applications/search?q=10x%20Genomics%20droplet&rows=20" -H "X-API-KEY: $USPTO_API_KEY" | python3 -c "import json,sys;print(len(json.load(sys.stdin)['patentFileWrapperDataBag']))"
  # both print 25
  ```

## Where I was tempted to say "novel" / "no prior art" — and what I wrote instead

In `_to_record`'s comment about the missing abstract field, an early draft of
mine said "no abstract available" without qualifying *where* — that reads
close to "this patent has no abstract" (a claim about the patent) rather than
"this API doesn't expose one" (a claim about the data source). Final wording:

> "No abstract field exists in this API for either pending applications or
> granted patents — confirmed live (einstein-tix)."

— scoped explicitly to the API, not to the patent's actual content (which
does have an abstract, just not reachable through this search response). The
module docstring and `uspto_grant_text.py`'s docstring use the same
API-scoped phrasing throughout. Nowhere in the diff does any code or comment
claim a patent doesn't exist, is novel, or that "no prior art was found" —
this bead is entirely about data-shape plumbing, not about any gap-detection
verdict.

## What I decided not to do, and why

- **Did not fix USPTO search relevance.** Live testing this session showed
  `q=<query>` behaves nothing like a relevance-ranked search — see the
  896,900-count number above, and three natural-language queries
  ("quantum error correction", "transformer attention mechanism",
  "droplet microfluidics reagent transfer") that each returned top-25 results
  with essentially zero topical relevance (ice makers and car seats for
  "transformer attention mechanism"). This is a real, separate problem from
  the URL/shape bug this bead was about. Filed as **`einstein-b3b`**, not
  fixed here.
- **Did not wire `uspto_grant_text` into `graph.py`/`novelty_auditor.py`.**
  Both call `fetch_patents` directly and use its title-only Records as-is;
  nothing calls `fetch_grant_text`/`record_with_grant_text`, so
  `independent_claims_for_record` still raises `ClaimsNotFoundError` (handled
  gracefully as an abstention, per existing `novelty_auditor.py` design) for
  every real patent Record today. The claim-text comparison path this
  session made possible doesn't run against real data yet. Filed as
  **`einstein-9it`**, not fixed here — deciding eager-vs-lazy enrichment
  against the grant-text download quota is a design call for a separate
  session.
- **Did not touch `scripts/nightly_run.sh` / `.github/workflows/nightly.yml`**
  (the bead's own text flagged these only add `--skip-patents` when the key
  is *unset*, meaning a configured key currently makes the nightly run fail
  outright). Out of scope for a data-shape bug; didn't want to touch CI
  config inside a bead about fetcher internals.
- **Did not spend more than one grant-text download.** The endpoint is
  rate-limited to ~20 downloads/year *per specific file* (confirmed via the
  API's own response message: "you submitted 1 allowed requests URL out of
  20 for this URL per 31536000 sec"). I fetched US12735810's grant XML once
  to build the fixture and once via the recall-check script run recorded
  above (two total against that one file this session, both logged).
  Deliberately did not fetch a second granted patent's grant XML just to
  build the no-abstract fixture — that one is hand-authored from real
  bibliographic metadata instead (see fixtures section above).

## What I could not verify

- **Whether the pending-application fixture entry in
  `tests/fixtures/uspto_patent_search.json` is exactly what a real pending
  application looks like.** No live query in this session's result sets
  surfaced one in a top-25 page (USPTO's `q=` appears to sort by filing date
  descending with poor relevance filtering — see `einstein-b3b` — so recent
  filings dominate every result page, and recent filings still in early
  prosecution stages exist but weren't among the ones I happened to fetch).
  I built that fixture entry using field names that *are* confirmed live on
  every real document, and omitted only fields that logically cannot exist
  pre-grant (`patentNumber`, `grantDate`, `grantDocumentMetaData`) — but the
  entry itself is constructed, not fetched. If a future session gets a
  precise repro of a genuinely pending application, it's worth diffing
  against this.
- **Whether every granted-patent grant-XML document follows the exact
  `<claim-text>` nesting shown in the one real file this session parsed
  (US12735810's).** `_flatten_claims` uses `element.itertext()`, which
  recurses through arbitrary nesting depth, so it should be robust to
  variation — but I only confirmed against one real document (utility,
  granted 2026) plus one hand-authored design-patent shape. Older grant XML
  (different DTD versions, e.g. pre-2001 SGML-era patents) was not checked.

## Commands to run (not run by me — git policy is conservative this session)

```
git add einstein/uspto_fetcher.py einstein/uspto_grant_text.py einstein/patent_claims.py \
        tests/test_uspto_fetcher.py tests/test_uspto_grant_text.py \
        tests/fixtures/uspto_patent_search.json tests/fixtures/uspto_grant_text.xml \
        tests/fixtures/uspto_grant_text_no_abstract.xml \
        scripts/uspto_search_recall_check.py .beads/issues.jsonl
git commit -m "einstein-tix: fix USPTO search URL/response shape, add grant-text claims source"
```

`bd close einstein-tix` will be run after this handoff is written (per the
write-then-close ordering this session was given). Two follow-ups were filed
during this session and left open: `einstein-b3b` (search relevance) and
`einstein-9it` (wire grant-text enrichment into the live pipeline). No
`git commit`, `git push`, or `bd dolt push` was run — per this session's git
policy, the tree is left dirty and ready for a human to review and run the
commands above.
