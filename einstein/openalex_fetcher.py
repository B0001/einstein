"""OpenAlex work lookup -> Record, plus outbound citation edges.

OpenAlex (https://openalex.org) is a free, keyless bibliographic graph. Each
work carries `referenced_works`: the list of other OpenAlex work IDs it
cites. That is the citation adjacency this module extracts -- one HTTP call
per seed DOI, no pagination, no auth.

`summary` is the paper's abstract. OpenAlex does not return abstract text
directly -- for copyright reasons it returns an `abstract_inverted_index`
(word -> the list of positions it occupies), and callers are expected to
reconstruct the text themselves. We do that reconstruction here so `Record`
keeps its convention of holding readable text, not an index a downstream
consumer would have to know to decode.

An unauthenticated caller can supply a contact email via `OPENALEX_MAILTO`
(or the `mailto` argument) to join OpenAlex's "polite pool", which gets a
faster, more consistent rate limit than anonymous requests. This is optional
-- OpenAlex works without it.

`search_papers` (einstein-0.5) is the free-text paper search
`einstein.novelty_auditor.audit_idea` needs and had no default for: given a
method-text query, return the `Record`s OpenAlex thinks are relevant.
OpenAlex exposes two ways to do that and they are not equivalent:

- `GET /works?search=<q>` runs the query against full-text search (indexed
  fulltext where available, else title/abstract/other fields) ranked by a
  relevance score. Live-checked by hand 2026-09-24 against the query
  "tensor network contraction for transformer attention": 2,515 hits, and
  the #1 result by relevance_score was "AI-Assisted Pipeline for Dynamic
  Generation of Trustworthy Health Supplement Content at Scale" -- a
  supplement-marketing paper with no topical relation to the query at all.
  High recall, unusable precision for this use case.
- `GET /works?filter=title_and_abstract.search:<q>` restricts the same
  ranking to title + abstract text. The identical query against this
  endpoint returned 7 hits, all transformer/tensor-method papers (e.g.
  "MMT: Multi-way Multi-modal Transformer for Multimodal Learning"). A
  second check against "quantum error correction surface code" (a query
  with much deeper OpenAlex coverage) returned 1,983 hits topped by
  "Quantum error correction below the surface code threshold" and three
  more surface-code QEC papers in the top five -- tight and on-topic.

`search_papers` therefore uses `title_and_abstract.search`, not `search`.
This is a considered trade against Semantic Scholar's `/paper/search`
(what `gemini_convo.md` names): Semantic Scholar needs a fourth credential
this repo does not have configured (see einstein-0.5's notes), and the
`title_and_abstract.search` filter measured above gives adequate recall
for a method-text query with zero new credentials. If a future query class
turns up as poorly on this filter as "tensor network contraction..." did on
plain `search`, that is grounds to revisit -- Semantic Scholar becomes the
answer after all, per the bead. See `scripts/openalex_search_recall_check.py`
to re-run this comparison by hand.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Protocol

import requests

from einstein.schema import Record
from einstein.store import DEFAULT_NEGATIVE_TTL_DAYS, Store

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
DEFAULT_TIMEOUT = 30
SOURCE = "openalex"


class OpenAlexFetchError(Exception):
    """Raised for any non-200 response from the OpenAlex works API.

    `not_found` distinguishes "this DOI is not in OpenAlex" (404) from a
    transient failure (rate limit, 5xx) -- callers may want to retry the
    latter and not the former. Neither case is a "zero citations" result:
    `fetch_citation_edges` returning `[]` means the work was found and has
    no `referenced_works`, which is a different fact than "we couldn't look
    it up at all".
    """

    def __init__(self, status_code: int, message: str, *, not_found: bool = False) -> None:
        self.status_code = status_code
        self.not_found = not_found
        super().__init__(message)


class _HttpClient(Protocol):
    """Just enough of `requests`' surface to fetch and to fake in tests."""

    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


def _short_id(openalex_url: str) -> str:
    """`https://openalex.org/W2741809807` -> `W2741809807`."""
    return openalex_url.rstrip("/").rsplit("/", 1)[-1]


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not inverted_index:
        return ""
    positioned = [(pos, word) for word, positions in inverted_index.items() for pos in positions]
    positioned.sort(key=lambda pair: pair[0])
    return " ".join(word for _, word in positioned)


def _to_record(item: dict[str, Any]) -> Record:
    ts = item.get("publication_date") or "1970-01-01"
    return Record(
        type="paper",
        id=_short_id(item["id"]),
        title=item.get("display_name") or item.get("title") or item["id"],
        summary=_reconstruct_abstract(item.get("abstract_inverted_index")),
        url=item["id"],
        ts=ts,
        raw=item,
    )


def _params(mailto: str | None) -> dict[str, str]:
    email = mailto if mailto is not None else os.environ.get("OPENALEX_MAILTO")
    return {"mailto": email} if email else {}


def _filter_value(query: str) -> str:
    """Escape `query` for use as an OpenAlex `filter=key:<value>` value.

    OpenAlex's filter DSL splits on a bare `,` to separate multiple
    filters, applied to the raw parameter value regardless of percent-
    encoding (`%2C` is rejected as an "unescaped comma" too -- confirmed
    live, see `search_papers`'s docstring). Wrapping the whole value in
    double quotes, as OpenAlex's own 400 response suggests, avoids that
    without needing to know their encoding internals. This does change
    the match from an OR-of-terms to a stemmed phrase match, which is a
    real precision/recall tradeoff -- but a query with a comma in it (e.g.
    "gradient descent, adaptive learning rate") is already closer to a
    phrase than a bag of words, so that tradeoff lands on the reasonable
    side. Queries without a comma are left exactly as `search_papers`
    passes them, matching the unquoted behavior this bead's recall check
    was run against.
    """
    return f'"{query}"' if "," in query else query


def fetch_work_by_doi(
    doi: str,
    *,
    mailto: str | None = None,
    http: _HttpClient | None = None,
    store: Store | None = None,
    ttl_days: int = DEFAULT_NEGATIVE_TTL_DAYS,
) -> Record:
    """Look up a single OpenAlex work by DOI, mapped to a Record.

    `doi` may be a bare DOI ("10.1145/3442188.3445922") or a full
    "https://doi.org/..." URL -- either form is accepted. Raises
    `OpenAlexFetchError` (with `not_found=True`) if the DOI is not in
    OpenAlex, or (with `not_found=False`) for any other non-200 response.
    `http` defaults to the `requests` module; pass a fake with a `.get`
    method to test without the network.

    If `store` is given, the cache is keyed on the normalized bare DOI (the
    `mailto` param is a polite-pool hint, not part of the query's identity,
    so it is excluded from the cache key). A cached found-work replays from
    `Store.get_record`. "Not in OpenAlex" is itself a search outcome -- a
    404 is cached as a `status="ok"`, zero-`ids` row, same as a zero-result
    search on the other fetchers -- so a not-yet-expired cached absence
    re-raises `OpenAlexFetchError(not_found=True)` without a network call;
    see `einstein/store.py` for why negatives expire and positives do not.
    """
    bare_doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    if store is not None:
        cached = store.lookup_query(source=SOURCE, query=bare_doi, params={}, ttl_days=ttl_days)
        if cached.status == "positive":
            record = store.get_record("paper", cached.ids[0])
            if record is not None:
                return record
        if cached.status == "negative":
            raise OpenAlexFetchError(
                404, f"OpenAlex lookup failed (cached not-found): doi={doi!r}", not_found=True
            )

    client: _HttpClient = http if http is not None else requests
    url = f"{OPENALEX_WORKS_URL}/doi:{bare_doi}"

    response = client.get(url, params=_params(mailto), timeout=DEFAULT_TIMEOUT)

    if response.status_code != 200:
        not_found = response.status_code == 404
        message = f"OpenAlex lookup failed: {response.status_code} for doi={doi!r}"
        if not_found:
            logger.info(message)
        else:
            logger.error(message)
        if store is not None:
            if not_found:
                status = "ok"
            elif response.status_code == 429:
                status = "rate_limited"
            else:
                status = "error"
            store.upsert_query(source=SOURCE, query=bare_doi, params={}, status=status, record_type="paper")
        raise OpenAlexFetchError(response.status_code, message, not_found=not_found)

    record = _to_record(response.json())
    if store is not None:
        store.upsert_record(record)
        store.upsert_query(
            source=SOURCE, query=bare_doi, params={}, status="ok", record_type="paper", ids=[record.id]
        )
    return record


def search_papers(
    query: str,
    *,
    max_results: int = 10,
    mailto: str | None = None,
    http: _HttpClient | None = None,
    store: Store | None = None,
    ttl_days: int = DEFAULT_NEGATIVE_TTL_DAYS,
) -> list[Record]:
    """Free-text search OpenAlex works matching `query`, mapped to Records.

    Matches `einstein.novelty_auditor.SearchFn`
    (`Callable[[str], list[Record]]`) -- pass this directly as
    `audit_idea(search_papers=search_papers)`. Uses the
    `title_and_abstract.search` filter, not the plain `search` parameter;
    see the module docstring for the recall/precision check that decided
    that.

    Raises `OpenAlexFetchError` for any non-200 response (`not_found` is
    always `False` here -- a search has no "not found" case distinct from
    a genuine zero-result search, unlike a single-DOI lookup). A query that
    matches nothing returns `[]`, not an exception: see `Audit.note`'s
    "no match found within this search" framing, which depends on `[]`
    meaning exactly that and nothing else.

    If `store` is given, a cached outcome for this exact `(query,
    max_results)` short-circuits the network call -- same cache-then-fetch
    contract as `uspto_fetcher.fetch_patents` and `arxiv_fetcher.fetch_papers`.
    `mailto` is a polite-pool hint, not part of the query's identity, and is
    excluded from the cache key (same reasoning as `fetch_work_by_doi`).

    A `query` containing a comma is quote-wrapped before being sent (see
    `_filter_value`) -- OpenAlex's filter syntax uses an unescaped `,` to
    separate multiple filters, and live-checked by hand, its edge proxy
    rejects even a percent-encoded `%2C` in that position with a 400
    ("A filter value contains an unescaped comma"). Wrapping in double
    quotes, which OpenAlex's own error message suggests, is the one thing
    that was confirmed (by hand) to work.
    """
    cache_params = {"max_results": max_results}
    if store is not None:
        cached = store.lookup_query(source=SOURCE, query=query, params=cache_params, ttl_days=ttl_days)
        if cached.status == "positive":
            return [
                record
                for record in (store.get_record("paper", id_) for id_ in cached.ids)
                if record is not None
            ]
        if cached.status == "negative":
            return []

    client: _HttpClient = http if http is not None else requests
    params = {
        **_params(mailto),
        "filter": f"title_and_abstract.search:{_filter_value(query)}",
        "per-page": max_results,
    }

    response = client.get(OPENALEX_WORKS_URL, params=params, timeout=DEFAULT_TIMEOUT)

    if response.status_code != 200:
        message = f"OpenAlex search failed: {response.status_code} for query={query!r}"
        logger.error(message)
        if store is not None:
            status = "rate_limited" if response.status_code == 429 else "error"
            store.upsert_query(source=SOURCE, query=query, params=cache_params, status=status, record_type="paper")
        raise OpenAlexFetchError(response.status_code, message, not_found=False)

    payload = response.json()
    results = payload.get("results") or []
    if not results:
        logger.info("OpenAlex search returned zero results for query=%r", query)

    records = [_to_record(item) for item in results[:max_results]]
    if store is not None:
        for record in records:
            store.upsert_record(record)
        store.upsert_query(
            source=SOURCE,
            query=query,
            params=cache_params,
            status="ok",
            record_type="paper",
            ids=[record.id for record in records],
        )

    return records


@dataclass(frozen=True, slots=True)
class Edge:
    """One directed citation: `from_id` cites `to_id` (both OpenAlex short IDs)."""

    from_id: str
    to_id: str


def fetch_citation_edges(
    doi: str,
    *,
    mailto: str | None = None,
    http: _HttpClient | None = None,
    store: Store | None = None,
    ttl_days: int = DEFAULT_NEGATIVE_TTL_DAYS,
) -> list[Edge]:
    """Fetch the seed work at `doi` and return its outbound citation edges.

    One edge per entry in the work's `referenced_works` -- i.e. "this paper
    cites that paper". An empty list means the work was found but cites
    nothing in OpenAlex's index; a lookup failure raises `OpenAlexFetchError`
    instead of returning `[]`, so the two are never conflated. `store` (if
    given) is passed straight through to `fetch_work_by_doi` -- edges
    themselves are not persisted, only the underlying work `Record`.
    """
    record = fetch_work_by_doi(doi, mailto=mailto, http=http, store=store, ttl_days=ttl_days)
    referenced = record.raw.get("referenced_works") or []
    return [Edge(from_id=record.id, to_id=_short_id(ref)) for ref in referenced]
