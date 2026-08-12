"""arXiv search -> Record.

Uses the `arxiv` package (a thin wrapper around the arXiv Atom API), sorted
by submission date so a query returns the newest matching papers first.
`summary` is the paper's abstract, matching the convention that a Record's
text field is whatever a human would read to judge relevance.

The `arxiv.Client` already implements the API's requested politeness
(default `delay_seconds=3.0` between paged requests) and retries transient
failures internally; we do not add another retry loop on top of it.

`query` is passed to the arXiv API's `search_query` verbatim -- it is NOT
quoted into an exact phrase here. The arXiv API treats an unquoted
multi-word query as an OR across `all:` of each individual term (e.g.
"quantum error correction" becomes `all:quantum OR all:error OR
all:correction`), which is broad recall, not phrase matching. A caller
that wants "these words together" must quote it themselves, e.g.
`fetch_papers('all:"quantum error correction"')`. This fetcher does not
impose that choice because callers may also want to pass field-qualified
or boolean queries (`cat:quant-ph AND ti:decoder`) that quoting would
break.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

import arxiv

from einstein.schema import Record
from einstein.store import DEFAULT_NEGATIVE_TTL_DAYS, Store

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 30
SOURCE = "arxiv"


class ArxivFetchError(Exception):
    """Raised when the arXiv API call fails outright (not a zero-results search).

    A query that matches nothing is an exhausted, empty result iterator and
    `fetch_papers` returns `[]` for it without raising. A raised
    `arxiv.ArxivError` (HTTP failure, malformed feed, retries exhausted)
    means the search did not run to completion at all, and an empty list
    would misreport that as "we looked and found nothing".
    """

    def __init__(self, message: str, *, query: str, cause: BaseException) -> None:
        self.query = query
        self.__cause__ = cause
        super().__init__(message)


class _ArxivClient(Protocol):
    """Just enough of `arxiv.Client`'s surface to fetch and to fake in tests."""

    def results(self, search: arxiv.Search) -> Any: ...


def _to_record(result: arxiv.Result) -> Record:
    return Record(
        type="paper",
        id=result.get_short_id(),
        title=result.title,
        summary=result.summary,
        url=result.entry_id,
        ts=result.published.isoformat(),
        raw={
            "entry_id": result.entry_id,
            "updated": result.updated.isoformat(),
            "published": result.published.isoformat(),
            "title": result.title,
            "authors": [author.name for author in result.authors],
            "summary": result.summary,
            "comment": result.comment,
            "journal_ref": result.journal_ref,
            "doi": result.doi,
            "primary_category": result.primary_category,
            "categories": result.categories,
            "pdf_url": result.pdf_url,
        },
    )


def fetch_papers(
    query: str,
    *,
    max_results: int = DEFAULT_MAX_RESULTS,
    client: _ArxivClient | None = None,
    store: Store | None = None,
    ttl_days: int = DEFAULT_NEGATIVE_TTL_DAYS,
) -> list[Record]:
    """Search arXiv for papers matching `query`, newest submissions first.

    Raises `ArxivFetchError` if the underlying `arxiv.Client` call fails
    (network error, malformed feed, retries exhausted) -- callers must not
    treat that the same as a genuine zero-results search, which returns
    `[]` normally. `client` defaults to `arxiv.Client()`; pass a fake with
    a `.results` method to test without the network.

    If `store` is given, a cached outcome for this exact `(query,
    max_results)` short-circuits the network call: a prior positive result
    is replayed from `Store.get_record`, a prior not-yet-expired negative
    replays as `[]`. Otherwise the search runs live and its outcome --
    including a genuine zero-result search or a rate-limited/failed
    attempt -- is written to `store` via `Store.upsert_query` so the next
    call (in this run or a future one) can see it. See `einstein/store.py`
    for what each cached status means and why negatives expire and
    positives do not.
    """
    params = {"max_results": max_results}
    if store is not None:
        cached = store.lookup_query(source=SOURCE, query=query, params=params, ttl_days=ttl_days)
        if cached.status == "positive":
            return [
                record
                for record in (store.get_record("paper", id_) for id_ in cached.ids)
                if record is not None
            ]
        if cached.status == "negative":
            return []

    active_client: _ArxivClient = client if client is not None else arxiv.Client()
    search = arxiv.Search(
        query=query,
        max_results=max_results,
        sort_by=arxiv.SortCriterion.SubmittedDate,
    )

    try:
        results = list(active_client.results(search))
    except arxiv.ArxivError as exc:
        message = f"arXiv search failed for query={query!r}: {exc}"
        logger.error(message)
        if store is not None:
            status = "rate_limited" if getattr(exc, "status", None) == 429 else "error"
            store.upsert_query(source=SOURCE, query=query, params=params, status=status, record_type="paper")
        raise ArxivFetchError(message, query=query, cause=exc) from exc

    if not results:
        logger.info("arXiv search returned zero results for query=%r", query)

    records = [_to_record(result) for result in results]
    if store is not None:
        for record in records:
            store.upsert_record(record)
        store.upsert_query(
            source=SOURCE,
            query=query,
            params=params,
            status="ok",
            record_type="paper",
            ids=[record.id for record in records],
        )

    return records
