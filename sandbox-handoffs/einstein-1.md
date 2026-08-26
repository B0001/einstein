# Handoff: einstein-1 — Define normalized Record schema + repo skeleton

**Status: closed** (`bd close einstein-1`). Acceptance criteria met and verified below.

## What I found at start

Repo was a bare `uv init` skeleton: `main.py` (hello-world stub), empty
`pyproject.toml` with no dependencies, no `einstein/` package, no `tests/`
directory. `bd show einstein-1` was `open`, never previously claimed — no
partial work to reconcile.

## What I changed

- **`einstein/__init__.py`** — new package, one-line docstring, no logic.
- **`einstein/schema.py`** — new module. `Record` is a `frozen=True,
  slots=True` dataclass with exactly the fields the bead specifies:
  `type: Literal["paper","repo","patent"]`, `id`, `title`, `summary`, `url`,
  `ts`, `raw: dict[str, Any]`. `__post_init__` asserts every field's shape
  (non-empty `id`/`title`/`url`, `type` in the allowed set, `ts` parses via
  `datetime.fromisoformat`, `raw` is a dict — `summary` may be empty, since
  an empty abstract is a fact about a source, not a bug). `RECORD_TYPES` is
  exported as the tuple of allowed type strings for reuse by fetchers/tests.
  `python -m einstein.schema` runs `_self_check()`: builds one Record of
  each type, then asserts that six deliberately-bad constructions (bad type,
  empty id/title/url, unparseable ts, non-dict raw) each raise
  `AssertionError`. Prints `einstein.schema self-check: OK` on success.
- **`tests/test_schema.py`** — new. 10 unittest cases covering the same
  ground as the self-check (one test per validation rule, plus frozen-ness
  and the empty-summary-is-legal case), so `unittest discover` also exercises
  the schema, not just the ad hoc self-check script.
- **`pyproject.toml`** — added the five deps the bead names, pinned exact
  (`==`), via `uv add requests arxiv scikit-learn numpy pandas` then
  hand-edited `>=` to `==`: `arxiv==4.0.1`, `numpy==2.5.2`, `pandas==3.0.5`,
  `requests==2.34.2`, `scikit-learn==1.9.0`. Re-ran `uv lock` after pinning.
- **`uv.lock`** — regenerated, now tracks the full pinned dependency closure
  (scipy, joblib, threadpoolctl, lxml, etc. come along transitively).
- Did **not** touch `main.py` — it's an unrelated `uv init` stub, out of
  scope for "normalized Record schema"; left as-is.

## Commands and their output (the evidence)

Clean import (acceptance criterion 1):

```
$ uv run python -c "import einstein; import einstein.schema; print('ok')"
ok
$ uv run python -W error -c "import einstein.schema"
(no output — no warnings, no errors)
```

Assert-based self-check (acceptance criterion 2):

```
$ uv run python -m einstein.schema
einstein.schema self-check: OK
```

Full project test suite (per CLAUDE.md: `uv sync && uv run python -m
unittest discover tests`):

```
$ uv sync
Resolved 18 packages in 0.74ms
Checked 16 packages in 0.12ms
$ uv run python -m unittest discover tests -v
test_all_three_types_construct (test_schema.RecordSchemaTest.test_all_three_types_construct) ... ok
test_empty_summary_is_allowed (test_schema.RecordSchemaTest.test_empty_summary_is_allowed) ... ok
test_record_is_frozen (test_schema.RecordSchemaTest.test_record_is_frozen) ... ok
test_rejects_empty_id (test_schema.RecordSchemaTest.test_rejects_empty_id) ... ok
test_rejects_empty_title (test_schema.RecordSchemaTest.test_rejects_empty_title) ... ok
test_rejects_empty_url (test_schema.RecordSchemaTest.test_rejects_empty_url) ... ok
test_rejects_non_dict_raw (test_schema.RecordSchemaTest.test_rejects_non_dict_raw) ... ok
test_rejects_non_iso_ts (test_schema.RecordSchemaTest.test_rejects_non_iso_ts) ... ok
test_rejects_unknown_type (test_schema.RecordSchemaTest.test_rejects_unknown_type) ... ok
test_ts_round_trips_through_fromisoformat (test_schema.RecordSchemaTest.test_ts_round_trips_through_fromisoformat) ... ok

----------------------------------------------------------------------
Ran 10 tests in 0.000s

OK
```

**Final line verbatim: `Ran 10 tests in 0.000s` / `OK` — 10 passed, 0 failed,
0 errored.** No network access was used or required by any test — every
assertion is against in-process `Record` construction.

Every number in this report (10 tests, 5 pinned deps, 6 bad-field cases in
the self-check) comes directly from the commands shown above; none is
asserted from memory.

## Where I was tempted to overclaim, and what I wrote instead

The only place novelty/gap language could have crept in was describing why
`raw` and `ts` exist (provenance for later gap-detection claims). I wrote
factual docstring language ("for provenance and re-derivation") rather than
any claim about what the pipeline will or won't find — this bead produces no
gap-detection output, so there was no live claim to hedge.

## Design decisions worth flagging for later beads

- **`ts` is a `str`, not `datetime`.** Chose ISO-8601 string over a
  `datetime` object so `Record` is trivially JSON/SQLite-serializable
  (relevant to einstein-2, the SQLite persistence bead) without a custom
  encoder, at the cost of validating format only at construction time via
  `datetime.fromisoformat`, not carrying a real datetime object downstream.
  If a later bead needs actual date arithmetic, it should parse `ts` at the
  point of use rather than this module changing shape.
- **`raw` has no default value** — fetchers must supply it explicitly. This
  is a deliberate provenance requirement (per the repo's "a number is only
  allowed to exist if the code produces it, or the document says where it
  came from" standard extended to records): a fetcher that can't cite its
  raw source payload shouldn't produce a Record silently. Flagging this now
  because it means fetcher beads (einstein-3/4/5/6) cannot take a
  `raw={}` shortcut for convenience.
- **`Record` is frozen.** Immutability by construction — nothing downstream
  (indexing, gap detection) should be mutating a normalized record in place.
  Confirmed by `test_record_is_frozen`.

## What I decided not to do, and why

- Did not add a `TypedDict` alternative alongside the dataclass — the bead
  says "dataclass/TypedDict" (either), and a dataclass gets runtime
  validation via `__post_init__`, which a TypedDict cannot do without an
  extra validation layer. One schema representation, not two.
- Did not build fetcher stubs (arXiv/GitHub/USPTO/OpenAlex) — those are
  einstein-3/4/5/6, separate beads, explicitly out of scope here.
- Did not touch `main.py`, `README.md`, or add a `[build-system]` table to
  `pyproject.toml` — none of these are named in the bead's acceptance
  criteria, and `uv run`/`uv sync`/`unittest discover` all work today
  without a build backend (the project resolves as a uv "virtual" source,
  confirmed in `uv.lock`: `source = { virtual = "." }`). Adding packaging
  machinery nobody asked for yet would be scope creep.
- Did not add a linter/formatter config — not requested, and CLAUDE.md's
  "Build & Test" section is still an empty placeholder, so there's no
  existing convention to conform to.

## What I could not verify

- I did not verify these exact dependency versions (`arxiv==4.0.1`,
  `numpy==2.5.2`, `pandas==3.0.5`, `requests==2.34.2`, `scikit-learn==1.9.0`)
  against any external changelog — they are simply whatever `uv add`
  resolved as latest-compatible against PyPI at run time
  (2026-08-10, inside this sandbox). If the fetcher beads need older/newer
  pins for compatibility reasons discovered later, that's a fresh decision
  for those beads, not something this bead's evidence can speak to.
- I have not run this against a real fetcher yet (none exist), so I cannot
  claim the `Record` shape survives contact with real arXiv/GitHub/USPTO
  payload quirks (e.g. GitHub repos with no description, arXiv papers with
  multiple timestamps). That's the job of einstein-3/4/5/6 to prove or
  disprove.

## Git state (nothing committed — per policy)

```
$ git status --porcelain
 M .beads/interactions.jsonl
 M .gitignore
?? .beads/issues.jsonl
?? .claude/settings.local.json
?? .python-version
?? README.md
?? einstein/
?? main.py
?? pyproject.toml
?? sandbox-prompt.md
?? tests/
?? uv.lock
```

New/relevant files from this session: `einstein/__init__.py`,
`einstein/schema.py`, `tests/test_schema.py`, `pyproject.toml` (deps added),
`uv.lock` (regenerated). `main.py`, `README.md`, `.python-version`,
`sandbox-prompt.md` predate this session's schema work and were not
authored by this task (left untouched).

Suggested commands for a human to run (not run by me, per git policy):

```
git add einstein/ tests/ pyproject.toml uv.lock
git commit -m "einstein-1: normalized Record schema + pinned deps"
```

(`main.py`, `README.md`, `.python-version`, `sandbox-prompt.md`,
`.claude/settings.local.json`, `.beads/issues.jsonl` are pre-existing
sandbox/session artifacts, not part of this bead's diff — a human should
decide separately whether those belong in a commit.)
