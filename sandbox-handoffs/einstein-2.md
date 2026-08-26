# Handoff: einstein-2 — Local persistence layer (SQLite) for records + seen-gaps

**Status: closing** (`bd close einstein-2`). Acceptance criterion met and verified below.

## What I found at start

`bd show einstein-2` was `open`, never previously claimed — no partial work
to reconcile. No `store.py`, `test_store.py`, or any sqlite-related code
existed anywhere in `einstein/` or `tests/` (checked with
`grep -ril "sqlite\|store\|persist"`, zero hits before this session).

## What I changed

- **`einstein/store.py`** — new module. `Store` wraps one SQLite file with
  two tables, created via `CREATE TABLE IF NOT EXISTS` on connect:
  - `records(type, id, title, summary, url, ts, raw, first_seen, last_seen)`,
    `PRIMARY KEY (type, id)`.
  - `gaps(gap_key, kind, payload, first_seen, last_seen)`,
    `PRIMARY KEY (gap_key)`.
  - `upsert_record(record: Record)` and `upsert_gap(gap_key, kind, payload)`
    both use SQLite's `INSERT ... ON CONFLICT DO UPDATE`: a new key inserts
    with `first_seen = last_seen = now`; an existing key updates its data and
    `last_seen`, leaving `first_seen` untouched. That is the whole
    "kill the statelessness gap" contract.
  - `get_record`, `record_seen_at`, `all_records`, `get_gap`, `all_gaps` for
    reading state back. `Store` is a context manager (`with Store(path) as
    store:`) and exposes `close()`.
  - `clock: Callable[[], str]` is an injectable constructor argument
    (default `datetime.now(timezone.utc).isoformat`), the same
    dependency-injection pattern the fetchers use for their HTTP/API
    clients — lets tests assert exact `first_seen`/`last_seen` values
    without sleeping or patching `datetime` globally.
  - `record.raw` and gap `payload` are stored as `json.dumps(...)` text
    columns and decoded back to `dict` on read.
  - `python -m einstein.store` runs `_self_check()`.
- **`tests/test_store.py`** — new. 12 unittest cases: insert, upsert-twice
  (the acceptance criterion, for both `records` and `gaps`), first_seen
  surviving a third upsert, `(type, id)` composite key (same `id` under two
  different `type`s is two rows, not a collision), missing-key reads
  returning `None`, JSON round-trip of a nested `raw` dict, data surviving
  connection close + reopen against the same file, and the context manager
  closing the connection.

## Design decision the bead's wording required resolving

The bead says "records table keyed by `(source,id)`". `Record`
(`einstein/schema.py`, einstein-1) has no `source` field — it has `type:
Literal["paper","repo","patent"]`, and its own docstring already states the
uniqueness contract this table needs: "id ... stable and unique within
`type`, not globally unique across types." I keyed `records` on `(type, id)`
rather than inventing a new `source` column, because:

- `type` is exactly the discriminator "source" means here in context of the
  existing schema.
- Today two sources share a `type` (`"paper"`: arXiv via `arxiv_fetcher.py`,
  OpenAlex via `openalex_fetcher.py`), and their id formats don't collide
  in practice (`2508.00001` vs `W2741809807`) — confirmed by reading both
  fetchers' `_to_record`.
- Adding a `source` field to `Record` itself would have been out of scope —
  einstein-1 is closed, and other closed/in-progress beads (einstein-3/4/6)
  already build `Record`s without one.

This is flagged here, not buried, because it's a real judgment call: if a
future source ever mints an id colliding with an existing `(type, id)` pair
from a different origin, this table will silently merge them into one row.
That's a risk for whichever bead adds that source, not something store.py
can detect without more information than a `Record` currently carries.

`gaps` is keyed on an opaque `gap_key: str` the caller supplies, with a
`kind` string and a JSON `payload` blob, deliberately not modeling any
specific `Gap` shape — gap classification doesn't exist yet (einstein-11 is
still open). einstein-11/13 can shape `Gap` however they need without this
table's schema changing; `upsert_gap`/`get_gap`/`all_gaps` don't assume
anything about `payload`'s contents beyond "JSON-serializable dict".

## Commands and their output (the evidence)

Self-check (acceptance criterion, standalone):

```
$ uv run python -m einstein.store
einstein.store self-check: OK
```

Full project test suite (per CLAUDE.md: `uv sync && uv run python -m
unittest discover tests`):

```
$ uv sync
Resolved 18 packages in 0.55ms
Checked 16 packages in 0.14ms
$ uv run python -m unittest discover tests
..............................
----------------------------------------------------------------------
Ran 68 tests in 0.050s

OK
```

**Final line verbatim: `Ran 68 tests in 0.050s` / `OK` — 68 passed, 0 failed,
0 errored** (56 pre-existing + 12 new in `test_store.py`). No network access
was used or required — every test runs against a `tempfile.TemporaryDirectory`
SQLite file with an injected fake clock, no real API calls.

The "upsert twice -> one row, timestamps updated" acceptance criterion is
directly exercised by
`test_store.StoreTest.test_upsert_twice_yields_one_row_with_updated_timestamps`
and `test_upsert_gap_twice_yields_one_row_with_updated_timestamps`, both
passing above, plus the standalone self-check.

## Where I was tempted to state "novel" / "no prior art", and what I wrote instead

Nowhere in this bead's scope was there an opportunity to make a gap-existence
claim — this is a storage layer with no detection logic. The one place
language needed care was the module docstring's description of what `gaps`
tracking is *for*: I wrote "gap classification does not exist yet" and "does
not guess its shape" rather than describing what a stored gap *means*,
since this module has no opinion on that — it's einstein-11/13's job to
define and this bead's job to just remember whatever they hand it.

## What I decided not to do, and why

- **Did not add a `Gap` dataclass.** Gap classification is einstein-11's
  scope (three-way gap detection matrix), still open. Guessing its shape now
  risks locking in a design that einstein-11 then has to work around, or
  worse, silently constrains what einstein-11 can classify. `gap_key` +
  `kind` + opaque JSON `payload` is deliberately the minimum this bead's
  acceptance criteria need.
- **Did not add a CLI or `main.py` wiring** to actually populate the store
  from a fetcher run — no bead asked for that yet, and doing it now would
  mean guessing at a pipeline shape (which fetchers run when, how records
  flow into `Store`) that's several beads away (einstein-14, LangGraph state
  machine skeleton; einstein-21, CLI entrypoint).
- **Did not add indices beyond the two primary keys.** No caller exists yet
  that queries by anything other than `(type, id)` or `gap_key` — einstein-13
  (gap dedup + velocity scoring) is the first consumer and doesn't exist yet
  either. Adding an index for a query pattern nobody has written would be
  guessing.
- **Did not make `Store` thread-safe or add connection pooling.** Nothing in
  this bead or its dependents (einstein-13) implies concurrent writers; a
  single `sqlite3.Connection` per `Store` instance is the whole footprint
  needed today. SQLite's own file locking is the fallback if that's ever
  wrong.
- **Did not touch `Record` or `einstein/schema.py`.** Adding a `source`
  field would have been a cleaner literal match to the bead's wording, but
  it's a change to a closed bead's frozen contract that three other closed
  fetcher beads (einstein-3/4/6) already depend on — out of scope for a
  storage-layer bead to unilaterally decide. Flagged as a judgment call
  above instead.

## What I could not verify

- **Behavior under real concurrent writers** (e.g. two pipeline runs against
  the same DB file at once) is unverified — no test exercises this, and
  nothing in the bead's acceptance criteria asks for it. SQLite's default
  locking would serialize such writes, but I have not measured that here.
- **Whether `(type, id)` will hold as a collision-free key once USPTO
  (einstein-5, in progress) and further sources land** — argued above from
  the two id formats that exist today, not measured against a real USPTO id
  format, which I have not seen fetcher code for yet.

## Git state (nothing committed — per policy)

```
$ git status --porcelain
M  .beads/interactions.jsonl
A  .beads/issues.jsonl
M  .gitignore
A  .python-version
A  README.md
A  einstein/__init__.py
A  einstein/arxiv_fetcher.py
A  einstein/arxiv_source.py
A  einstein/embedding.py
A  einstein/github_fetcher.py
A  einstein/openalex_fetcher.py
A  einstein/schema.py
A  main.py
A  pyproject.toml
A  sandbox-prompt.md
A  tests/test_arxiv_fetcher.py
A  tests/test_embedding.py
A  tests/test_github_fetcher.py
A  tests/test_openalex_fetcher.py
A  tests/test_schema.py
A  uv.lock
?? .claude/settings.local.json
?? einstein/store.py
?? tests/test_store.py
```

The `A` entries above were already staged by a prior session before this one
started (this bead's task only added the two `??` files: `einstein/store.py`,
`tests/test_store.py`); I did not run `git add` on anything. I did not
commit, push, or run `bd dolt push`, per this repo's git policy.

Suggested commands for a human to run (not run by me, per git policy):

```
git add einstein/store.py tests/test_store.py
git commit -m "einstein-2: SQLite persistence layer for records + seen-gaps"
```
