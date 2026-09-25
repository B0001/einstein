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
  relevance score. Terms are NOT all required to match.
- `GET /works?filter=title_and_abstract.search:<q>` restricts matching to
  title + abstract, and every term must match.

History, so the next person does not flip this back: einstein-0.5 chose the
filter, on two SHORT queries checked by hand 2026-09-24 ("tensor network
contraction for transformer attention": filter 7 on-topic hits vs `search=`
2,515 hits topped by an unrelated supplement-marketing paper). That check
never tried the query `audit_idea` actually sends -- `idea.method`, a whole
method paragraph. Bead einstein-av1 records a live measurement the same day: a 14-word
query returned 0 results via the filter against 44 via `search=`; 9 words
gave 3 vs 3,893. Every-term-must-match collapses to zero as the query
grows, and the auditor turned that `[]` into a "pass" -- a fail-open on
missing evidence. `search_papers` therefore uses `search=` (einstein-av1).

The precision cost is real but it is the safe side of the trade: an
off-topic hit is scored by cosine similarity against theta in
`novelty_auditor` and simply fails to reach it, whereas a missing hit is
invisible. It is not free, though: only the top `max_results` are
returned, so if relevance ranking buries the real prior art under
off-topic hits, a "pass" is still only as good as that ranking. An empty
result is additionally never a "pass" (see
`novelty_auditor`'s "unsearched" verdict). See
`scripts/openalex_search_recall_check.py` to re-run the comparison by hand,
including on paragraph-length queries.
"""

from __future__ import annotations

import logging
import os
import re
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


_QUERY_SYNTAX_RE = re.compile(r"[^\w\s]+")


def _search_value(query: str) -> str:
    """Reduce `query` to a plain lowercase bag of words for `search=`.

    `search_papers` sends a free-text method paragraph, not a hand-written
    query, so any character OpenAlex's search might read as syntax is
    neutralized rather than passed through: punctuation (including `,`,
    `"`, parentheses, `-`) becomes whitespace, and the text is lowercased so
    a stray "AND"/"OR"/"NOT" in prose cannot act as an uppercase boolean
    operator. This deliberately does not depend on knowing exactly which of
    those OpenAlex treats specially -- a punctuation-free, lowercase string
    of words is the plainest query there is under any reading of their
    syntax, and relevance ranking does not use punctuation or case anyway.

    The comma case in particular: the old filter path had to quote-wrap a
    comma (`filter=` splits on a bare `,`, confirmed live in einstein-0.5).
    Quote-wrapping is wrong here -- in `search=` quotes mean exact phrase,
    which would reintroduce the zero-recall failure this module moved away
    from -- so the comma is simply dropped.
    """
    return " ".join(_QUERY_SYNTAX_RE.sub(" ", query).lower().split())


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
    `audit_idea(search_papers=search_papers)`. Uses the relevance-ranked
    `search=` parameter, not the every-term-must-match
    `title_and_abstract.search` filter; see the module docstring for the
    live measurements that decided that.

    Raises `OpenAlexFetchError` for any non-200 response (`not_found` is
    always `False` here -- a search has no "not found" case distinct from
    a genuine zero-result search, unlike a single-DOI lookup). A query that
    matches nothing returns `[]`, not an exception -- and
    `novelty_auditor.audit_idea` turns an empty paper search into an
    "unsearched" verdict, never a "pass". A query with no word characters
    left after `_search_value` also returns `[]` without a network call:
    an empty `search=` would not be a search of anything.

    If `store` is given, a cached outcome for this exact `(query,
    max_results)` short-circuits the network call -- same cache-then-fetch
    contract as `uspto_fetcher.fetch_patents` and `arxiv_fetcher.fetch_papers`.
    `mailto` is a polite-pool hint, not part of the query's identity, and is
    excluded from the cache key (same reasoning as `fetch_work_by_doi`).

    The query is normalized by `_search_value` before being sent
    (punctuation, commas and quotes included, become whitespace; see
    there). The cache key keeps the caller's original `query` plus
    `"param": "search"`, so outcomes cached under the old filter-based
    search (keyed on `max_results` alone) -- in particular its spurious
    zero-result negatives -- are never replayed as this search's answer.
    """
    cache_params = {"max_results": max_results, "param": "search"}
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

    search_value = _search_value(query)
    if not search_value:
        logger.info("OpenAlex search skipped: no searchable words in query=%r", query)
        return []

    client: _HttpClient = http if http is not None else requests
    params = {
        **_params(mailto),
        "search": search_value,
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
