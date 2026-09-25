"""Hand-run script (einstein-av1): count OpenAlex paper searches cached in an
`einstein.db` that came back with zero results.

`Audit`s are not persisted by `einstein.store.Store` (its tables are
`records`, `gaps`, `queries`), so there is no stored `queried_paper_ids` to
count directly. What IS persisted, when `search_papers` ran with a `store`,
is every search outcome in `queries`. A `status="ok"` OpenAlex search row
with empty `ids` is exactly a search whose `audit_idea` saw zero candidate
papers -- i.e. an audit that was a vacuous "pass" before einstein-av1 and is
"unsearched" after it.

Rows are split by which search produced them: rows whose params carry
`"param": "search"` are the relevance-ranked `search=` backend (einstein-av1);
the rest are the old every-term-must-match `title_and_abstract.search`
filter. OpenAlex DOI lookups (`fetch_work_by_doi`, params `{}`) are excluded.

Read-only: opens the database with `mode=ro`.

Usage: uv run python scripts/count_empty_paper_searches.py [path/to/einstein.db]
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    path = Path(argv[1] if len(argv) > 1 else "einstein.db")
    if not path.exists():
        print(f"{path}: not found -- nothing to count")
        return 1

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT query, params, status, ids FROM queries WHERE source = 'openalex' AND record_type = 'paper'"
    ).fetchall()
    conn.close()

    counts = {"filter": [0, 0], "search": [0, 0]}  # backend -> [total ok searches, zero-result]
    empty_examples: list[str] = []
    for query, params_json, status, ids_json in rows:
        params = json.loads(params_json)
        if "max_results" not in params or status != "ok":
            continue  # a DOI lookup, or a search that never completed
        backend = "search" if params.get("param") == "search" else "filter"
        counts[backend][0] += 1
        if not json.loads(ids_json):
            counts[backend][1] += 1
            empty_examples.append(f"  [{backend}] {len(query.split())} words: {query[:100]!r}")

    for backend, (total, empty) in counts.items():
        print(f"{backend:>6}: {empty} of {total} completed OpenAlex paper search(es) returned zero results")
    for line in empty_examples:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
