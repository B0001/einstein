# einstein-8m9: Remove unused 'pandas' dependency from pyproject.toml

## What changed

- `pyproject.toml`: removed the `"pandas==3.0.5"` line from `[project].dependencies`.
- `uv.lock`: regenerated via `uv lock`. This dropped pandas and the packages
  that existed in the lockfile only to support it (no other package in the
  graph depended on them): `python-dateutil`, `six`, `tzdata`.

Diff is scoped to exactly these two files:

```
$ git -c safe.directory=/workspace diff --stat pyproject.toml uv.lock
 pyproject.toml |  1 -
 uv.lock        | 78 ----------------------------------------------------------
 2 files changed, 79 deletions(-)
```

## Evidence

1. No source file imports pandas (checked before and after the change):

   ```
   $ grep -rn 'pandas' --include=*.py . | grep -v __pycache__
   (no output)
   ```

2. No remaining references to pandas anywhere in the dependency files:

   ```
   $ grep -n pandas pyproject.toml uv.lock
   (no output, exit code 1)
   ```

3. `uv lock` output, confirming pandas and its lockfile-only transitive deps
   were dropped (not just unpinned):

   ```
   $ uv lock
   Using CPython 3.12.13
   Resolved 44 packages in 417ms
   Removed pandas v3.0.5
   Removed python-dateutil v2.9.0.post0
   Removed six v1.17.0
   Removed tzdata v2026.3
   ```

4. `uv sync` reinstalled the environment from the regenerated lockfile —
   pandas does not appear in the resolved package set (checked the full
   sync output, tail included below; no `pandas` line).

5. Full test suite, run after the `uv sync` above, against the pandas-free
   environment:

   ```
   $ uv run python -m unittest discover tests
   Ran 366 tests in 0.355s

   OK
   ```

   (The bead's evidence template cited "Ran 282 tests" as the baseline; the
   suite has grown since that bead was filed — other beads landed more tests
   in the meantime. 366/366 pass, 0 failures, 0 errors. The interleaved
   stderr lines in the run — `arXiv search failed...`, `GitHub search
   failed...`, `USPTO fetch requires an API key...`, etc. — are expected
   output from fixture-simulated fetcher-error tests, not real network calls
   or failures; the final line is `OK`.)

## Claims of "novel" / "no prior art"

None — this bead is a dependency-hygiene change with no gap-detection output
involved. Nothing to phrase carefully here.

## What I decided not to do, and why

- Did not touch `numpy` or `scikit-learn`, which are also in the dependency
  list — the bead's scope was pandas only, and both of those are plausibly
  used elsewhere in the codebase (not verified as part of this bead; out of
  scope).
- Did not investigate why pandas was added in the first place (no git log
  authority was requested and it's not needed to fix the issue).
- Did not commit or push. Per this repo's git policy the tree is left dirty
  and ready to commit.

## What I could not verify

- Nothing. Both acceptance-criteria checks (grep returns no matches; full
  suite passes after `uv sync` with the regenerated lockfile) were run
  directly and both hold.

## Pre-existing dirty tree (not part of this bead)

`git status` at claim time already showed uncommitted changes from other
beads (`einstein/graph.py`, `einstein/codegen.py`, `einstein/feasibility.py`,
`einstein/report.py`, `einstein/sandbox.py`, several new test files, and
several other handoff files under `sandbox-handoffs/`). None of that was
touched by this session; it's left exactly as found.

## Commands for a human to run

```bash
git -c safe.directory=/workspace diff pyproject.toml uv.lock   # review this bead's change
git -c safe.directory=/workspace add pyproject.toml uv.lock
git -c safe.directory=/workspace commit -m "Remove unused pandas dependency (einstein-8m9)"
```

(Not run — git policy for this session is: do not commit, do not push.)
