"""One-time, hand-run script: compare OpenAlex's `/works?search=` (full-text)
against `/works?filter=title_and_abstract.search:` (title+abstract only) for
method-text queries, to justify which one `einstein.openalex_fetcher.search_papers`
(einstein-0.5) uses.

This is NOT part of the test suite -- it hits the live OpenAlex API and is
subject to its rate limits (anonymous search gets 429'd hard; this script
sleeps and retries). Re-run by hand only if the choice needs re-checking,
e.g. because a new query class performs badly on `title_and_abstract.search`
too and Semantic Scholar needs reconsidering (see einstein-0.5's notes).

Usage: uv run python scripts/openalex_search_recall_check.py
"""

from __future__ import annotations

import sys
import time
import urllib.parse
import urllib.request

BASE = "https://api.openalex.org/works"
MAILTO = "einstein-recall-check@example.com"

QUERIES = [
    "tensor network contraction for transformer attention",
    "quantum error correction surface code",
]


def _get(params: dict[str, str], *, retries: int = 6) -> dict:
    url = f"{BASE}?{urllib.parse.urlencode(params)}"
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                import json

                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < retries - 1:
                time.sleep(8)
                continue
            raise
    raise RuntimeError("unreachable")


def _run_one(query: str, params: dict[str, str], label: str) -> None:
    payload = _get({**params, "mailto": MAILTO, "per-page": "5"})
    count = payload.get("meta", {}).get("count", "?")
    print(f"  [{label}] query={query!r} total_count={count}")
    for item in payload.get("results", [])[:5]:
        print(f"    - {item.get('display_name')!r} (relevance_score={item.get('relevance_score')})")


def _run_comma_check() -> None:
    # OpenAlex's filter DSL splits on a bare comma to separate multiple
    # filters -- even a percent-encoded %2C in that position is rejected by
    # OpenAlex's edge proxy with a 400 ("A filter value contains an
    # unescaped comma"). Quote-wrapping the value (their own error message's
    # suggested fix) is what einstein.openalex_fetcher._filter_value does.
    query = "gradient descent, adaptive learning rate"
    print(f"=== comma-handling check: {query!r} ===")
    try:
        _run_one(query, {"filter": f"title_and_abstract.search:{query}"}, "unquoted (expected to fail)")
    except Exception as exc:  # noqa: BLE001 -- demonstrating the failure mode, not handling it
        print(f"  [unquoted] failed as expected: {exc}")
    _run_one(query, {"filter": f'title_and_abstract.search:"{query}"'}, "quote-wrapped (expected to succeed)")
    print()


def main() -> None:
    for query in QUERIES:
        print(f"=== {query!r} ===")
        _run_one(query, {"search": query}, "search= (fulltext)")
        _run_one(query, {"filter": f"title_and_abstract.search:{query}"}, "title_and_abstract.search filter")
        print()
    _run_comma_check()


if __name__ == "__main__":
    sys.exit(main())
