"""USPTO Open Data Portal patent search -> Record.

Confirmed live against the real API (einstein-tix): the search endpoint is
the Patent File Wrapper API's `/api/v1/patent/applications/search` -- there
is no `/api/v1/patent/search` route (that guess 403'd with AWS API
Gateway's generic "no route matches" error, which reads exactly like an
auth failure but isn't one). Its response shape is also different from what
was originally guessed: results come back under `patentFileWrapperDataBag`,
not `patentData`, and title/patent-number/dates live under each result's
nested `applicationMetaData`, not at the top level.

This API is a *prosecution-history* search across both pending applications
and granted patents -- it has no abstract or claims-text field at all, for
either. `summary` is therefore title-only here. A patent's real abstract
and claims text live in USPTO's separate Patent Grant Full-Text XML bulk
data product, reachable (once a patent has granted) via this response's
`grantDocumentMetaData.fileLocationURI` -- see `einstein.uspto_grant_text`
for fetching and parsing that.

Unlike GitHub and OpenAlex, this fetcher has no anonymous path: the USPTO
Open Data Portal requires an API key for search, and the original design
draft (`gemini_convo.md`) fell back to a hardcoded "MOCK-PATENT-01" record
when no key was present or the request failed. That fallback is deliberately
NOT reproduced here. A gap detector reads "no patent found" as evidence of
absence; silently substituting fabricated patent data for a real API call
would make the detector confidently wrong in a way nothing downstream could
catch. Missing credentials or a failed request must raise, never degrade to
placeholder content.

Search relevance (einstein-b3b). This API's `q=` parameter is NOT a
relevance-ranked full-text search by default -- characterized live against
api.uspto.gov this session (no reachable API docs: the developer portal's
linked PDF 301-redirects to a dead page, and every `/api-docs`, `/swagger.json`
guess on api.uspto.gov 403'd). What `q=` actually does, confirmed by
systematic querying (reproduce with `scripts/uspto_search_recall_check.py`
or the raw `curl` commands in `sandbox-handoffs/einstein-b3b.md`):

- It is an Elasticsearch `query_string`-style query against a broad set of
  indexed text fields. Confirmed via its error messages, which are verbatim
  Elasticsearch (`sort=score` -> `"No mapping found for [score] in order to
  sort on"`), and via syntax that only `query_string` supports: quoted exact
  phrases (`"quantum error correction"`), boolean operators (`AND`/`OR`/
  `NOT`/`-term`), wildcards (`quant*`), and dotted-path field-scoping
  (`applicationMetaData.inventionTitle:quantum` -- NOT bare `inventionTitle:
  quantum`, which 404s; the bead's original guess used the wrong path).
- **The default (no boolean operators) is OR-of-terms, not AND, and not a
  phrase match.** `q="quantum error correction"` unquoted returns the exact
  same `count` and top results as `q="quantum OR error OR correction"`
  (926455, dominated by anything containing the common words "error" or
  "correction" alone -- 49451 and 865708 hits respectively). Quoting as an
  exact phrase (`q="\"quantum error correction\""`) collapses that to 158,
  all genuinely on-topic. AND-joining does the same (239 hits). But neither
  quoting nor AND-joining is usable as this fetcher's default query
  strategy: a natural-language method paragraph (what `novelty_auditor`
  sends) essentially never appears verbatim in patent text, so a quoted or
  AND-joined paragraph reliably returns **zero** hits (confirmed: a
  realistic method-paragraph query, quoted, 404'd -- "No matching records
  found"), silently discarding real recall rather than improving precision.
- **The API has no relevance ranking without an explicit `sort=`.** With no
  `sort` param, results are ordered by filing date descending (most recent
  first) -- confirmed identical to `sort=applicationMetaData.filingDate
  desc`. That's the actual mechanism behind the bead's original complaint:
  a loose OR match across ~900k+ documents, truncated to a page, sorted by
  recency -- so the returned page is arbitrary recent filings that happen to
  contain any one query word, not the closest matches.
- **`sort=_score desc` fixes this without sacrificing recall**, and is what
  this fetcher now always sends. It does NOT shrink `count` (still the same
  near-whole-index OR match) -- it only reorders which page of that match
  set comes back. Confirmed live with the same realistic natural-language
  method paragraph used above, unquoted: `count` stayed at 12,916,985 either
  way, but the top 3 results went from unrelated (screen UI controls, RNA
  formulations, photonics gyroscopes) to genuinely on-topic (three droplet/
  microfluidics/reagent patents) purely from adding this sort. This is the
  fetcher's actual mitigation: better ranking of a necessarily-broad match,
  not a narrower or more precise match.
- **`count` must never be read as a relevance or no-conflict signal**, before
  or after this fix -- it is a hit count against a loose OR query and stays
  in the hundreds-of-thousands to millions range for any ordinary multi-word
  query regardless of topical relevance. This fetcher does not expose
  `count` on `Record` for exactly this reason; if a future caller reads it
  off `record.raw` or the raw payload, it is not evidence of anything.
- Broader recall check (einstein-uts, `scripts/uspto_relevance_benchmark.py`):
  b3b's characterization above covers a handful of hand-chosen queries; this
  is a same-day (2026-09-29) live measurement across 10 scored, independently
  ground-truthed topics (ground truth from a quoted exact-phrase lookup --
  see the script's docstring for why that oracle is different from, but not
  fully independent of, the OR-mode path being tested; a fully independent
  ground truth was not built). Recall@10 -- what `novelty_auditor.audit_idea`
  actually gets, since it calls `search_patents(idea.method)` with no
  `max_results` override -- was 9/10 (90%); recall@25 was 10/10 (100%). The
  one recall@10 miss was a near-exact title match ("SURGICAL ROBOT END
  EFFECTOR") that still ranked outside the top 10 of a sort=_score result
  set. This sample deliberately paraphrases each ground-truth patent's own
  title, which is an easier case than a real `idea.method` paragraph
  generated from scratch with no guaranteed vocabulary overlap -- so 90%
  should be read as an upper bound on live recall@10, not a representative
  estimate. Reproduce with `USPTO_API_KEY=... uv run python
  scripts/uspto_relevance_benchmark.py` (numbers drift as the index changes
  day to day). Whether this is sufficient to trust patent-side "pass"
  verdicts at face value, or whether `novelty_auditor.Audit` needs an
  explicit lower-confidence signal on the patent side given a measured (not
  hand-waved) recall@10 short of 100% even in an easy case, is the open
  product/risk-tolerance question tracked as einstein-uts (labeled `human`);
  see that bead and `sandbox-handoffs/einstein-uts.md` for the full
  methodology, numbers, and a non-binding recommendation.

Timestamp field variance across applicationStatusCode (einstein-zfp).
`_to_record`'s `ts` used to check only `grantDate` and `filingDate`, hardcoded
`"1970-01-01"` otherwise. Confirmed live against api.uspto.gov this session:
a real pending application (US application 19589991, IQM Finland OY,
applicationStatusCode 19 "Application Undergoing Preexam Processing") has no
`filingDate` key at all -- only `effectiveFilingDate` ('2026-05-22'). Without
a fallback to it, a real, recent application was silently timestamped as if
from before the API existed. `ts` now also falls back to
`effectiveFilingDate` before the `"1970-01-01"` placeholder.

Separately: whether `patentNumber` presence remains a reliable granted/
pending classifier (used below to pick the Google Patents vs. Patent Center
URL) was checked against this same live variance, not assumed. A sample of
226 real documents across 5 unrelated queries ("quantum computer",
"artificial intelligence", "battery", "vaccine", "semiconductor", each
`rows=100`, `sort=_score desc`) spanned 12 distinct `applicationStatusCode`
values (19, 30, 41, 93, 150, 159, 160, 161, 218, 250, 566) and showed
`patentNumber` present for 100% of documents in the two "has been granted"
codes (150 "Patented Case", 250 "Patent Expired Due to NonPayment of
Maintenance Fees" -- expired-after-grant still carries its number) and
absent for 100% of documents in every other status, including
allowance-adjacent ones (93 "Notice of Allowance Mailed") and abandoned ones
(160, 161). No status code was mixed. Decision: `patentNumber` presence
stays the classifier -- it tracks "was a patent number ever assigned", which
is the fact the URL choice actually needs, and the live sample found no
counterexample across a reasonably diverse status set. This was not
exhaustive (12 of USPTO's ~60+ status codes; reissue and design-patent
statuses were not specifically sampled), so treat it as "no counterexample
found in a diverse live sample", not "proven for all codes".
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

import requests

from einstein.schema import Record
from einstein.store import DEFAULT_NEGATIVE_TTL_DAYS, Store

logger = logging.getLogger(__name__)

USPTO_SEARCH_URL = "https://api.uspto.gov/api/v1/patent/applications/search"
DEFAULT_TIMEOUT = 30
SOURCE = "uspto"


class USPTOFetchError(Exception):
    """Raised for any failure fetching from the USPTO patent search API.

    `missing_key` distinguishes "we never made the request because no API
    key was configured" from "the request ran and USPTO rejected it" (bad
    key, rate limit, outage) -- both are failures a caller must not treat as
    a genuine zero-results search, which returns `[]` normally.
    """

    def __init__(self, message: str, *, status_code: int | None = None, missing_key: bool = False) -> None:
        self.status_code = status_code
        self.missing_key = missing_key
        super().__init__(message)


class _HttpClient(Protocol):
    """Just enough of `requests`' surface to fetch and to fake in tests."""

    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


def _resolve_api_key(api_key: str | None) -> str:
    key = api_key if api_key is not None else os.environ.get("USPTO_API_KEY")
    if not key:
        message = (
            "USPTO fetch requires an API key: set USPTO_API_KEY or pass "
            "api_key=... . Refusing to fall back to placeholder patent "
            "data -- a fabricated 'no prior art found' silently poisons "
            "gap detection."
        )
        logger.error(message)
        raise USPTOFetchError(message, missing_key=True)
    return key


def _to_record(doc: dict[str, Any]) -> Record:
    metadata = doc.get("applicationMetaData") or {}
    patent_number = metadata.get("patentNumber")
    application_number = doc.get("applicationNumberText")
    number = patent_number or application_number
    if not number:
        raise USPTOFetchError(f"USPTO record has no patent/application number: {doc!r}")
    number = str(number)
    title = metadata.get("inventionTitle") or "Untitled Patent"
    # No abstract field exists in this API for either pending applications or
    # granted patents -- confirmed live (einstein-tix). Title-only is the
    # honest summary here; einstein.uspto_grant_text.fetch_grant_text can
    # enrich a granted patent's Record with real abstract + claims text.
    summary = title
    # einstein-zfp: some pending applications (applicationStatusCode 19,
    # "Application Undergoing Preexam Processing", confirmed live -- see
    # module docstring) have no filingDate key at all, only
    # effectiveFilingDate -- without this fallback those got silently
    # timestamped "1970-01-01".
    ts = metadata.get("grantDate") or metadata.get("filingDate") or metadata.get("effectiveFilingDate") or "1970-01-01"
    if patent_number:
        url = f"https://patents.google.com/patent/US{patent_number}/en"
    else:
        url = f"https://patentcenter.uspto.gov/applications/{application_number}"
    return Record(
        type="patent",
        id=number,
        title=title,
        summary=summary,
        url=url,
        ts=ts,
        raw=doc,
    )


def _is_no_match_404(response: requests.Response) -> bool:
    """True for USPTO's zero-hit reply, which is a 404, not 200+[] (einstein-n7n).

    Confirmed live: q=zzzznonexistentqueryterm12345 returns 404 with
    detailedMessage "No matching records found, refine your search criteria
    and try again". Any other 404 (or unparseable body) stays a real error.
    """
    if response.status_code != 404:
        return False
    try:
        body = response.json()
    except ValueError:
        return False
    return isinstance(body, dict) and str(body.get("detailedMessage", "")).startswith("No matching records found")


def fetch_patents(
    query: str,
    *,
    max_results: int = 10,
    api_key: str | None = None,
    http: _HttpClient | None = None,
    store: Store | None = None,
    ttl_days: int = DEFAULT_NEGATIVE_TTL_DAYS,
) -> list[Record]:
    """Search USPTO patents matching `query`, mapped to Records.

    Raises `USPTOFetchError` (with `missing_key=True`) if no API key is
    configured -- via `api_key`, or the `USPTO_API_KEY` env var -- before any
    request is made. Raises `USPTOFetchError` (with `missing_key=False`) for
    any non-200 response, except USPTO's "No matching records found" 404,
    which is its real zero-hit reply and returns `[]` (einstein-n7n). `http` defaults to the `requests` module; pass a
    fake with a `.get` method to test without the network.

    If `store` is given, a cached outcome for this exact `(query,
    max_results)` short-circuits the network call (and the missing-key
    check happens first regardless, since a missing key means no request
    would run either way) -- see `arxiv_fetcher.fetch_papers` for the
    shared cache-then-fetch contract and `einstein/store.py` for what each
    cached status means. Quota burned on USPTO's paid key is exactly what
    this cache exists to stop re-spending.

    Always sends `sort=_score desc` -- see module docstring's "Search
    relevance (einstein-b3b)" section for why this fetcher does not trust
    this API's default (recency) ordering. This reorders the returned page,
    it does not narrow it: a multi-word `query` still matches on ANY of its
    terms (OR), so the *count* of matching documents server-side stays huge
    regardless of topical relevance -- only the top `max_results` actually
    returned benefit from the relevance sort.
    """
    key = _resolve_api_key(api_key)
    cache_params = {"max_results": max_results}
    if store is not None:
        cached = store.lookup_query(source=SOURCE, query=query, params=cache_params, ttl_days=ttl_days)
        if cached.status == "positive":
            return [
                record
                for record in (store.get_record("patent", id_) for id_ in cached.ids)
                if record is not None
            ]
        if cached.status == "negative":
            return []

    client: _HttpClient = http if http is not None else requests
    headers = {"Accept": "application/json", "X-API-KEY": key}
    # sort=_score desc is not optional -- see module docstring's "Search
    # relevance (einstein-b3b)" section. Without it this API defaults to
    # filingDate descending (most-recent-first), not relevance, and a
    # multi-word `q` matches on ANY term (OR), so an unsorted search returns
    # arbitrary very-recent applications with no topical connection to the
    # query. Confirmed live: adding this sort keeps the same (very broad)
    # matched set but reorders it so the returned page is topically relevant.
    params = {"q": query, "rows": max_results, "sort": "_score desc"}

    response = client.get(USPTO_SEARCH_URL, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)

    if _is_no_match_404(response):
        payload: dict[str, Any] = {}
    elif response.status_code != 200:
        message = (
            f"USPTO search failed: {response.status_code} {response.reason} "
            f"for query={query!r}"
        )
        logger.error(message)
        if store is not None:
            status = "rate_limited" if response.status_code == 429 else "error"
            store.upsert_query(source=SOURCE, query=query, params=cache_params, status=status, record_type="patent")
        raise USPTOFetchError(message, status_code=response.status_code)
    else:
        payload = response.json()
    docs = payload.get("patentFileWrapperDataBag") or []
    if not docs:
        logger.info("USPTO search returned zero results for query=%r", query)

    records = [_to_record(doc) for doc in docs[:max_results]]
    if store is not None:
        for record in records:
            store.upsert_record(record)
        store.upsert_query(
            source=SOURCE,
            query=query,
            params=cache_params,
            status="ok",
            record_type="patent",
            ids=[record.id for record in records],
        )

    return records
