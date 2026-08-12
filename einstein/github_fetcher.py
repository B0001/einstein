"""GitHub repository search -> Record.

Uses the GitHub REST "search repositories" endpoint. `summary` is built from
name + description + topics, matching the convention that a Record's text
field is whatever a human would read to judge relevance, not the whole raw
payload.

Auth is optional but strongly recommended: unauthenticated search is capped
at 10 requests/minute (60/hour on the wider API), authenticated is 30/minute
(5000/hour). Read from `GITHUB_TOKEN` if set.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Protocol

import requests

from einstein.schema import Record
from einstein.store import DEFAULT_NEGATIVE_TTL_DAYS, Store

logger = logging.getLogger(__name__)

GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
DEFAULT_TIMEOUT = 30
SOURCE = "github"


class GitHubFetchError(Exception):
    """Raised for any non-200 response from the GitHub search API.

    This is deliberately distinct from a zero-results search: a query that
    matches nothing is a 200 with an empty `items` list and `fetch_repos`
    returns `[]` for it without raising. A non-200 means the search did not
    run at all (bad auth, rate limit, GitHub outage, malformed query) and
    an empty list would misreport that as "we looked and found nothing".
    """

    def __init__(self, status_code: int, message: str, *, rate_limited: bool = False) -> None:
        self.status_code = status_code
        self.rate_limited = rate_limited
        super().__init__(message)


class _HttpClient(Protocol):
    """Just enough of `requests`' surface to fetch and to fake in tests."""

    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_rate_limited(response: requests.Response) -> bool:
    if response.status_code == 429:
        return True
    return response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0"


def _to_record(item: dict[str, Any]) -> Record:
    topics = item.get("topics") or []
    description = item.get("description") or ""
    summary = " ".join(part for part in (description, " ".join(topics)) if part).strip()
    ts = item.get("pushed_at") or item.get("updated_at") or item.get("created_at")
    return Record(
        type="repo",
        id=item["full_name"],
        title=item["full_name"],
        summary=summary,
        url=item["html_url"],
        ts=ts,
        raw=item,
    )


def fetch_repos(
    query: str,
    *,
    max_results: int = 30,
    http: _HttpClient | None = None,
    store: Store | None = None,
    ttl_days: int = DEFAULT_NEGATIVE_TTL_DAYS,
) -> list[Record]:
    """Search GitHub repositories matching `query`, mapped to Records.

    Raises `GitHubFetchError` on any non-200 response, including rate
    limiting (429, or 403 with `X-RateLimit-Remaining: 0`) -- callers must
    not treat that the same as a genuine zero-results search, which returns
    `[]` normally. `http` defaults to the `requests` module; pass a fake
    with a `.get` method to test without the network.

    If `store` is given, a cached outcome for this exact `(query,
    max_results)` short-circuits the network call -- see
    `arxiv_fetcher.fetch_papers` for the shared cache-then-fetch contract
    and `einstein/store.py` for what each cached status means.
    """
    cache_params = {"max_results": max_results}
    if store is not None:
        cached = store.lookup_query(source=SOURCE, query=query, params=cache_params, ttl_days=ttl_days)
        if cached.status == "positive":
            return [
                record
                for record in (store.get_record("repo", id_) for id_ in cached.ids)
                if record is not None
            ]
        if cached.status == "negative":
            return []

    client: _HttpClient = http if http is not None else requests
    per_page = min(max_results, 100)
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": per_page}

    response = client.get(
        GITHUB_SEARCH_URL, headers=_headers(), params=params, timeout=DEFAULT_TIMEOUT
    )

    if response.status_code != 200:
        rate_limited = _is_rate_limited(response)
        message = (
            f"GitHub search failed: {response.status_code} {response.reason} "
            f"for query={query!r}"
        )
        if rate_limited:
            reset = response.headers.get("X-RateLimit-Reset", "unknown")
            logger.warning("%s (rate-limited, resets at epoch %s)", message, reset)
        else:
            logger.error(message)
        if store is not None:
            status = "rate_limited" if rate_limited else "error"
            store.upsert_query(source=SOURCE, query=query, params=cache_params, status=status, record_type="repo")
        raise GitHubFetchError(response.status_code, message, rate_limited=rate_limited)

    payload = response.json()
    items = payload.get("items", [])
    if not items:
        logger.info("GitHub search returned zero results for query=%r", query)

    records = [_to_record(item) for item in items[:max_results]]
    if store is not None:
        for record in records:
            store.upsert_record(record)
        store.upsert_query(
            source=SOURCE,
            query=query,
            params=cache_params,
            status="ok",
            record_type="repo",
            ids=[record.id for record in records],
        )

    return records
