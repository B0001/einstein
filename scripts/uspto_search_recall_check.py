"""Hand-run script: exercise the live USPTO Open Data Portal APIs that
einstein/uspto_fetcher.py and einstein/uspto_grant_text.py were built against,
to justify the endpoint, response shape, claims-text source, and search
relevance behavior those modules use (einstein-tix, einstein-b3b).

Three things this checks against the real API, not a guess:

1. The search endpoint is `/api/v1/patent/applications/search`, not
   `/api/v1/patent/search` (the latter 403s with AWS API Gateway's generic
   "no route matches" error -- easy to mistake for an auth failure, but it
   means the route doesn't exist).
2. What `q=<query>` actually does, and why `fetch_patents` always sends
   `sort=_score desc` (einstein-b3b): this API's default order is filing
   date descending (recency), not relevance, and a multi-word `q` matches
   on ANY term (OR) with no ranking unless a sort is given. This prints, for
   one query, the top results WITHOUT a sort override next to the top
   results `fetch_patents` actually returns (WITH `sort=_score desc`), so a
   human can see the difference live rather than trust a claim about it.
   Full characterization lives in `uspto_fetcher.py`'s module docstring and
   `sandbox-handoffs/einstein-b3b.md`.
3. That a granted patent's `grantDocumentMetaData.fileLocationURI` really
   does lead (through one redirect) to real, parseable claims text -- fetched
   live, flattened by `uspto_grant_text`, and run through the *unmodified*
   `patent_claims.independent_claims_for_record` to confirm the two modules
   are compatible end to end, not just against the hand-built test fixtures.

This is NOT part of the test suite -- it hits the live api.uspto.gov API and
requires a real USPTO_API_KEY (get one at https://account.uspto.gov). The
grant full-text download endpoint is rate-limited to roughly 20 downloads per
year *per specific file* -- this script fetches at most one grant document
per run, and prints which file it fetched so repeated runs can be tracked
against that quota by hand.

Usage: USPTO_API_KEY=... uv run python scripts/uspto_search_recall_check.py
"""

from __future__ import annotations

import os
import sys

from einstein.patent_claims import independent_claims_for_record
from einstein.uspto_fetcher import USPTO_SEARCH_URL, USPTOFetchError, fetch_patents
from einstein.uspto_grant_text import GrantTextError, fetch_grant_text, record_with_grant_text

QUERIES = [
    "quantum error correction",
    "transformer attention mechanism",
    "droplet microfluidics reagent transfer",
    "10x Genomics droplet",
    # `fetch_patents` now always sends `sort=_score desc` (einstein-b3b), so
    # these return topically relevant top results -- see
    # `_run_relevance_demo` for a live before/after on one of them. Note
    # that sorting by relevance instead of recency means the top results for
    # a query like "10x Genomics droplet" skew toward older, more-cited-
    # feeling matches, which tend to be granted less often than the flood of
    # very recent pending applications a pure-recency sort would surface --
    # this is why the grant-text check below no longer depends on any of
    # these queries happening to surface a granted patent (see
    # KNOWN_GRANTED_PATENT_NUMBER).
]

# A natural-language method paragraph, shaped like what novelty_auditor.py
# actually sends as `idea.method` -- not a keyword list. Used only for the
# relevance demo below (einstein-b3b); not one of QUERIES above.
DEMO_METHOD_PARAGRAPH = (
    "A method for generating droplets in a microfluidic channel and "
    "transferring a chemical reagent between droplets using an electric "
    "field to induce coalescence"
)

# A specific granted patent (confirmed live, einstein-tix), looked up
# directly via the dotted-path field-scoped query syntax (einstein-b3b) --
# `applicationMetaData.patentNumber:12735810` -- rather than hoping one of
# the topical QUERIES above happens to surface a granted result. Decoupling
# "does the grant-text fetch+parse pipeline work" from "did a topical search
# happen to rank a granted patent in its top page" makes this check reliable
# regardless of how USPTO's index or ranking shifts day to day.
KNOWN_GRANTED_PATENT_NUMBER = "12735810"


def _require_api_key() -> str:
    key = os.environ.get("USPTO_API_KEY")
    if not key:
        print("USPTO_API_KEY is not set -- get one at https://account.uspto.gov and re-run:")
        print("  USPTO_API_KEY=... uv run python scripts/uspto_search_recall_check.py")
        sys.exit(1)
    return key


def _run_search_checks(api_key: str) -> None:
    print(f"=== search endpoint: {USPTO_SEARCH_URL} ===\n")
    for query in QUERIES:
        try:
            # max_results=25: the API appears to ignore `rows` server-side (a
            # rows=5 and a rows=20 request both came back with the same 25
            # items live, see einstein-b3b) -- asking for more here just
            # keeps more of what the API sends anyway.
            records = fetch_patents(query, max_results=25, api_key=api_key)
        except USPTOFetchError as exc:
            print(f"  [{query!r}] FAILED: {exc}")
            continue
        print(f"  [{query!r}] {len(records)} result(s):")
        for record in records:
            granted = bool(record.raw.get("applicationMetaData", {}).get("patentNumber"))
            print(f"    - {record.id} ({'granted' if granted else 'pending'}) {record.title!r}")
        print()


def _run_relevance_demo(api_key: str) -> None:
    """einstein-b3b: show, live, why `fetch_patents` always sends
    `sort=_score desc` -- top results WITHOUT it next to top results WITH
    it (via `fetch_patents` itself), for the same natural-language method
    paragraph. Same `count` either way; only the ordering of what's
    returned changes.
    """
    import requests

    print("=== search relevance: sort=_score desc vs USPTO's default order ===\n")
    print(f"  query (unquoted, {len(DEMO_METHOD_PARAGRAPH)} chars): {DEMO_METHOD_PARAGRAPH!r}\n")

    response = requests.get(
        USPTO_SEARCH_URL,
        headers={"Accept": "application/json", "X-API-KEY": api_key},
        params={"q": DEMO_METHOD_PARAGRAPH, "rows": 3},
        timeout=30,
    )
    if response.status_code != 200:
        print(f"  unsorted request FAILED: {response.status_code} {response.reason}")
        return
    payload = response.json()
    print(f"  WITHOUT sort override -- count={payload.get('count')}, top 3 (USPTO's default order):")
    for doc in payload.get("patentFileWrapperDataBag", [])[:3]:
        title = (doc.get("applicationMetaData") or {}).get("inventionTitle")
        print(f"    - {title!r}")

    try:
        records = fetch_patents(DEMO_METHOD_PARAGRAPH, max_results=3, api_key=api_key)
    except USPTOFetchError as exc:
        print(f"  fetch_patents (sort=_score desc) FAILED: {exc}")
        return
    print(f"\n  WITH sort=_score desc (what fetch_patents sends) -- top {len(records)}:")
    for record in records:
        print(f"    - {record.title!r}")
    print()


def _run_grant_text_check(api_key: str) -> None:
    print("=== grant full-text fetch + claims parse (one real download) ===\n")
    field_query = f"applicationMetaData.patentNumber:{KNOWN_GRANTED_PATENT_NUMBER}"
    try:
        results = fetch_patents(field_query, max_results=1, api_key=api_key)
    except USPTOFetchError as exc:
        print(f"  lookup of known granted patent {KNOWN_GRANTED_PATENT_NUMBER} FAILED: {exc}")
        return
    candidate = next(
        (r for r in results if r.raw.get("grantDocumentMetaData", {}).get("fileLocationURI")),
        None,
    )
    if candidate is None:
        print(f"  {field_query!r} returned no result with a grantDocumentMetaData.fileLocationURI -- skipping.")
        return

    uri = candidate.raw["grantDocumentMetaData"]["fileLocationURI"]
    print(f"  fetching grant text for {candidate.id} ({candidate.title!r})")
    print(f"  fileLocationURI: {uri}")
    print("  (counts against that file's ~20-downloads/year quota -- see module docstring)")
    try:
        grant_text = fetch_grant_text(candidate, api_key=api_key)
    except GrantTextError as exc:
        print(f"  FAILED: {exc}")
        return

    print(f"  abstract ({len(grant_text.abstract)} chars): {grant_text.abstract[:200]!r}")
    enriched = record_with_grant_text(candidate, grant_text)
    try:
        claims = independent_claims_for_record(enriched)
    except Exception as exc:  # noqa: BLE001 -- report, don't hide, an incompatibility if one exists
        print(f"  independent_claims_for_record FAILED: {exc}")
        return
    print(f"  {len(claims)} independent claim(s) parsed by the *unmodified* patent_claims module:")
    for claim in claims[:3]:
        print(f"    {claim.number}. {claim.text[:150]!r}")


def main() -> None:
    api_key = _require_api_key()
    _run_relevance_demo(api_key)
    _run_search_checks(api_key)
    _run_grant_text_check(api_key)


if __name__ == "__main__":
    sys.exit(main())
