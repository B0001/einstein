"""Local SQLite persistence: records + seen-gaps.

The pipeline runs repeatedly against APIs that rate-limit and truncate; without
somewhere to remember what has already been fetched and what gaps have already
been surfaced, every run starts from zero ("the statelessness gap" from
gemini_convo.md). This module is the fix, deliberately small: one SQLite file,
two tables. SQLite, not Neo4j -- promote to a graph database only when a stage
actually needs graph traversal, not in anticipation of it.

``records`` is keyed by ``(type, id)``. The bead that specifies this module
calls the key "(source, id)"; `Record` (einstein/schema.py) has no separate
`source` field, and its own docstring already establishes the uniqueness
contract this table needs: "id ... stable and unique within `type`, not
globally unique across types." `type` is exactly the discriminator the bead
means by "source" here -- today the two sources that share a `type`
("paper": arXiv, OpenAlex) mint ids in visibly different formats
(``2508.00001`` vs ``W2741809807``), so no collision is expected in practice.
If a future source mints an id that collides under this key, that is a real
bug for whichever bead adds that source to surface, not something this module
can guess how to resolve.

``gaps`` is keyed by an opaque ``gap_key`` the caller provides. Gap
classification does not exist yet (einstein-11); this module does not
guess its shape. A gap is stored as a `kind` string plus a JSON `payload`
blob, so einstein-11/13 can define whatever `Gap` shape they need without
this table changing.

Both `records` and `gaps` track `first_seen` / `last_seen`. Upserting an
existing key updates its data and `last_seen`; `first_seen` is set once, on
first insert, and never changes. That is the entire "kill the statelessness
gap" contract: run the pipeline twice, get one row, not two, with
`last_seen` moved forward.

``queries`` (einstein-0.2) is the piece `records` cannot express: a search
that returns nothing writes no `Record`, so nothing distinguishes "never
queried" from "queried, found nothing" from "queried, got rate-limited and
the caller swallowed it". Each row is one cache entry keyed by
`(source, query, params)` -- `params` is a caller-supplied dict (e.g.
`max_results`) JSON-encoded with sorted keys so equal dicts collide on the
same row regardless of key order. `status` is `"ok"`, `"rate_limited"`, or
`"error"`; `record_type` is the single `Record.type` this query searches
(one fetcher, one type) and `ids` is the JSON list of ids -- within that
`record_type` -- a `status="ok"` fetch returned, empty for a genuine
zero-result search. A `rate_limited` or `error` row carries no `ids` and
must never be read as a negative finding: `Store.lookup_query` keeps those
statuses distinct from `"negative"` for exactly that reason.

`status="ok"` with non-empty `ids` (a positive finding -- something exists)
does not expire: a patent found last month is still a patent. `status="ok"`
with empty `ids` (a negative finding -- nothing found) decays, because
sources publish continuously and a stale empty result silently becomes a
false claim of absence. `Store.lookup_query` enforces the decay with a
`ttl_days` parameter (unit: days since `fetched_at`, default 30); past it, a
negative reads as `"expired"`, not `"negative"`.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from einstein.schema import Record

DEFAULT_NEGATIVE_TTL_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    type TEXT NOT NULL,
    id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    url TEXT NOT NULL,
    ts TEXT NOT NULL,
    raw TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    PRIMARY KEY (type, id)
);

CREATE TABLE IF NOT EXISTS gaps (
    gap_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS queries (
    source TEXT NOT NULL,
    query TEXT NOT NULL,
    params TEXT NOT NULL,
    status TEXT NOT NULL,
    record_type TEXT NOT NULL,
    ids TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (source, query, params)
);
"""


def _default_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_params(params: dict) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


def _days_between(earlier: str, later: str) -> float:
    return (datetime.fromisoformat(later) - datetime.fromisoformat(earlier)).total_seconds() / 86400


@dataclass(frozen=True, slots=True)
class QueryLookup:
    """The result of `Store.lookup_query`.

    `status`:
      - "absent": this `(source, query, params)` has never been recorded.
      - "positive": recorded `status="ok"` with at least one id. Does not
        expire.
      - "negative": recorded `status="ok"` with zero ids, and `fetched_at`
        is within `ttl_days` of `now`. This is the evidence for a negative
        finding.
      - "expired": recorded `status="ok"` with zero ids, but `fetched_at` is
        past `ttl_days`. NOT usable as a negative finding -- re-fetch.
      - "rate_limited" / "error": recorded as such. NEVER usable as a
        negative finding regardless of age.

    `ids` is populated only for `status="positive"`; `record_type` is
    populated whenever a row was found (every status but "absent").
    """

    status: str
    ids: tuple[str, ...] = ()
    record_type: str | None = None
    fetched_at: str | None = None


class Store:
    """A single SQLite file holding `records` and `gaps`.

    `clock` is injectable so tests can control `first_seen`/`last_seen`
    without sleeping or mocking `datetime` globally -- the same dependency-
    injection pattern the fetchers use for their HTTP/API clients.
    """

    def __init__(self, db_path: str | Path, *, clock: Callable[[], str] = _default_clock) -> None:
        self._clock = clock
        self._conn = sqlite3.connect(str(db_path))
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def now(self) -> str:
        """This `Store`'s injected clock, for callers that need to score
        against the same notion of "now" the upserts below it use (e.g.
        `einstein.velocity.score_gaps`'s `now` parameter) -- reading the
        wall clock separately would skew age calculations against whatever
        clock a test injected here.
        """
        return self._clock()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def upsert_record(self, record: Record) -> None:
        """Insert `record`, or update it in place if `(type, id)` already exists.

        `first_seen` is set once and preserved across repeat upserts;
        `last_seen` moves forward every call. This is the "upsert twice ->
        one row, timestamps updated" acceptance criterion.
        """
        now = self._clock()
        self._conn.execute(
            """
            INSERT INTO records (type, id, title, summary, url, ts, raw, first_seen, last_seen)
            VALUES (:type, :id, :title, :summary, :url, :ts, :raw, :now, :now)
            ON CONFLICT(type, id) DO UPDATE SET
                title = excluded.title,
                summary = excluded.summary,
                url = excluded.url,
                ts = excluded.ts,
                raw = excluded.raw,
                last_seen = excluded.last_seen
            """,
            {
                "type": record.type,
                "id": record.id,
                "title": record.title,
                "summary": record.summary,
                "url": record.url,
                "ts": record.ts,
                "raw": json.dumps(record.raw),
                "now": now,
            },
        )
        self._conn.commit()

    def get_record(self, type: str, id: str) -> Record | None:
        row = self._conn.execute(
            "SELECT type, id, title, summary, url, ts, raw FROM records WHERE type = ? AND id = ?",
            (type, id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def record_seen_at(self, type: str, id: str) -> tuple[str, str] | None:
        """Return `(first_seen, last_seen)` for a record, or None if absent."""
        row = self._conn.execute(
            "SELECT first_seen, last_seen FROM records WHERE type = ? AND id = ?",
            (type, id),
        ).fetchone()
        return tuple(row) if row is not None else None

    def all_records(self) -> list[Record]:
        rows = self._conn.execute(
            "SELECT type, id, title, summary, url, ts, raw FROM records ORDER BY type, id"
        ).fetchall()
        return [_row_to_record(row) for row in rows]

    def upsert_gap(self, gap_key: str, kind: str, payload: dict) -> None:
        """Record that `gap_key` was seen this run, updating `last_seen`.

        `payload` is opaque to this module -- whatever JSON-serializable dict
        einstein-11/13 wants to remember about the gap. `kind` is a short
        classifier label (e.g. the gap class), kept as its own column so a
        caller can filter by it without deserializing `payload`.
        """
        now = self._clock()
        self._conn.execute(
            """
            INSERT INTO gaps (gap_key, kind, payload, first_seen, last_seen)
            VALUES (:gap_key, :kind, :payload, :now, :now)
            ON CONFLICT(gap_key) DO UPDATE SET
                kind = excluded.kind,
                payload = excluded.payload,
                last_seen = excluded.last_seen
            """,
            {
                "gap_key": gap_key,
                "kind": kind,
                "payload": json.dumps(payload),
                "now": now,
            },
        )
        self._conn.commit()

    def get_gap(self, gap_key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT gap_key, kind, payload, first_seen, last_seen FROM gaps WHERE gap_key = ?",
            (gap_key,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_gap(row)

    def all_gaps(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT gap_key, kind, payload, first_seen, last_seen FROM gaps ORDER BY gap_key"
        ).fetchall()
        return [_row_to_gap(row) for row in rows]

    def upsert_query(
        self,
        *,
        source: str,
        query: str,
        params: dict,
        status: str,
        record_type: str,
        ids: Sequence[str] = (),
    ) -> None:
        """Record the outcome of one `(source, query, params)` fetch attempt.

        `status` is `"ok"` (the fetch ran to completion -- `ids` empty means
        a genuine zero-result search), `"rate_limited"`, or `"error"`. A
        repeat call for the same key overwrites the row: a re-fetch that
        clears a stale `"rate_limited"`/`"expired"` cache entry is meant to
        replace it, not accumulate history.
        """
        assert status in ("ok", "rate_limited", "error"), f"unknown query status {status!r}"
        now = self._clock()
        self._conn.execute(
            """
            INSERT INTO queries (source, query, params, status, record_type, ids, fetched_at)
            VALUES (:source, :query, :params, :status, :record_type, :ids, :now)
            ON CONFLICT(source, query, params) DO UPDATE SET
                status = excluded.status,
                record_type = excluded.record_type,
                ids = excluded.ids,
                fetched_at = excluded.fetched_at
            """,
            {
                "source": source,
                "query": query,
                "params": _canonical_params(params),
                "status": status,
                "record_type": record_type,
                "ids": json.dumps(list(ids)),
                "now": now,
            },
        )
        self._conn.commit()

    def lookup_query(
        self,
        *,
        source: str,
        query: str,
        params: dict,
        ttl_days: int = DEFAULT_NEGATIVE_TTL_DAYS,
    ) -> QueryLookup:
        """Look up the cached outcome of `(source, query, params)`.

        See `QueryLookup` for what each returned `status` means. `ttl_days`
        (unit: days) governs only the `"negative"` -> `"expired"` decay;
        `"positive"` rows never expire. `now` for the age check comes from
        this `Store`'s injected clock, not a sleep -- tests control it the
        same way they control `first_seen`/`last_seen`.
        """
        row = self._conn.execute(
            "SELECT status, record_type, ids, fetched_at FROM queries "
            "WHERE source = ? AND query = ? AND params = ?",
            (source, query, _canonical_params(params)),
        ).fetchone()
        if row is None:
            return QueryLookup(status="absent")

        status, record_type, ids_json, fetched_at = row
        if status != "ok":
            return QueryLookup(status=status, record_type=record_type, fetched_at=fetched_at)

        ids = tuple(json.loads(ids_json))
        if ids:
            return QueryLookup(status="positive", ids=ids, record_type=record_type, fetched_at=fetched_at)

        age_days = _days_between(fetched_at, self._clock())
        if age_days > ttl_days:
            return QueryLookup(status="expired", record_type=record_type, fetched_at=fetched_at)
        return QueryLookup(status="negative", record_type=record_type, fetched_at=fetched_at)


def _row_to_record(row: tuple) -> Record:
    type_, id_, title, summary, url, ts, raw = row
    return Record(type=type_, id=id_, title=title, summary=summary, url=url, ts=ts, raw=json.loads(raw))


def _row_to_gap(row: tuple) -> dict:
    gap_key, kind, payload, first_seen, last_seen = row
    return {
        "gap_key": gap_key,
        "kind": kind,
        "payload": json.loads(payload),
        "first_seen": first_seen,
        "last_seen": last_seen,
    }


def _self_check() -> None:
    import tempfile

    ticks = iter(
        [
            "2026-08-08T00:00:00+00:00",
            "2026-08-09T00:00:00+00:00",
            "2026-08-10T00:00:00+00:00",
            "2026-08-11T00:00:00+00:00",
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        with Store(Path(tmp) / "einstein.db", clock=lambda: next(ticks)) as store:
            record = Record(
                type="paper",
                id="2508.00001",
                title="A Paper",
                summary="v1",
                url="https://arxiv.org/abs/2508.00001",
                ts="2026-08-08T00:00:00+00:00",
                raw={"v": 1},
            )
            store.upsert_record(record)
            updated = Record(
                type=record.type,
                id=record.id,
                title=record.title,
                summary="v2",
                url=record.url,
                ts=record.ts,
                raw=record.raw,
            )
            store.upsert_record(updated)
            assert len(store.all_records()) == 1, "upsert twice must yield one row"
            first_seen, last_seen = store.record_seen_at("paper", "2508.00001")
            assert first_seen == "2026-08-08T00:00:00+00:00", first_seen
            assert last_seen == "2026-08-09T00:00:00+00:00", last_seen
            assert store.get_record("paper", "2508.00001").summary == "v2"

            store.upsert_gap("gap-1", "cross_pollination", {"score": 0.9})
            store.upsert_gap("gap-1", "cross_pollination", {"score": 0.95})
            assert len(store.all_gaps()) == 1, "upsert twice must yield one gap row"

        clock_value = ["2026-08-08T00:00:00+00:00"]
        with Store(Path(tmp) / "queries.db", clock=lambda: clock_value[0]) as store:
            assert store.lookup_query(source="uspto", query="q", params={}).status == "absent"

            store.upsert_query(source="uspto", query="q", params={"rows": 10}, status="ok", record_type="patent", ids=[])
            negative = store.lookup_query(source="uspto", query="q", params={"rows": 10}, ttl_days=30)
            assert negative.status == "negative", negative

            clock_value[0] = "2026-09-10T00:00:00+00:00"  # 33 days later, past the 30-day TTL
            expired = store.lookup_query(source="uspto", query="q", params={"rows": 10}, ttl_days=30)
            assert expired.status == "expired", expired

            store.upsert_query(
                source="uspto", query="q2", params={}, status="ok", record_type="patent", ids=["US1", "US2"]
            )
            positive = store.lookup_query(source="uspto", query="q2", params={})
            assert positive.status == "positive" and positive.ids == ("US1", "US2"), positive

            store.upsert_query(source="github", query="q3", params={}, status="rate_limited", record_type="repo")
            rate_limited = store.lookup_query(source="github", query="q3", params={})
            assert rate_limited.status == "rate_limited", rate_limited

    print("einstein.store self-check: OK")


if __name__ == "__main__":
    _self_check()
