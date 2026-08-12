"""USPTO Open Data Portal patent search -> Record.

`summary` is title + abstract text, matching the convention that a Record's
text field is whatever a human (or a TF-IDF/embedding pass) would read to
judge relevance.

Unlike GitHub and OpenAlex, this fetcher has no anonymous path: the USPTO
Open Data Portal requires an API key for search, and the original design
draft (`gemini_convo.md`) fell back to a hardcoded "MOCK-PATENT-01" record
when no key was present or the request failed. That fallback is deliberately
NOT reproduced here. A gap detector reads "no patent found" as evidence of
absence; silently substituting fabricated patent data for a real API call
would make the detector confidently wrong in a way nothing downstream could
catch. Missing credentials or a failed request must raise, never degrade to
placeholder content.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

import requests

from einstein.schema import Record
from einstein.store import DEFAULT_NEGATIVE_TTL_DAYS, Store

logger = logging.getLogger(__name__)

USPTO_SEARCH_URL = "https://api.uspto.gov/api/v1/patent/search"
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
    number = doc.get("patentNumber") or doc.get("applicationNumber")
    if not number:
        raise USPTOFetchError(f"USPTO record has no patent/application number: {doc!r}")
    number = str(number)
    title = doc.get("patentTitle") or "Untitled Patent"
    abstract = doc.get("abstractText") or ""
    summary = " ".join(part for part in (title, abstract) if part).strip()
    ts = doc.get("grantDate") or doc.get("filingDate") or "1970-01-01"
    return Record(
        type="patent",
        id=number,
        title=title,
        summary=summary,
        url=f"https://patents.google.com/patent/US{number}/en",
        ts=ts,
        raw=doc,
    )


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
    any non-200 response. `http` defaults to the `requests` module; pass a
    fake with a `.get` method to test without the network.

    If `store` is given, a cached outcome for this exact `(query,
    max_results)` short-circuits the network call (and the missing-key
    check happens first regardless, since a missing key means no request
    would run either way) -- see `arxiv_fetcher.fetch_papers` for the
    shared cache-then-fetch contract and `einstein/store.py` for what each
    cached status means. Quota burned on USPTO's paid key is exactly what
    this cache exists to stop re-spending.
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
    params = {"q": query, "rows": max_results}

    response = client.get(USPTO_SEARCH_URL, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)

    if response.status_code != 200:
        message = (
            f"USPTO search failed: {response.status_code} {response.reason} "
            f"for query={query!r}"
        )
        logger.error(message)
        if store is not None:
            status = "rate_limited" if response.status_code == 429 else "error"
            store.upsert_query(source=SOURCE, query=query, params=cache_params, status=status, record_type="patent")
        raise USPTOFetchError(message, status_code=response.status_code)

    payload = response.json()
    docs = payload.get("patentData") or []
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
