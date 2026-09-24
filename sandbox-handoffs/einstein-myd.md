# einstein-myd handoff

## Bead
`bd show einstein-myd` — Gitignore `.claude/settings.local.json` so it stops
recurring as untracked cruft in every sandbox session's `git status`.

## What I changed

One line added to `/workspace/.gitignore` (after the existing
`.claude/settings.json` line):

```
.claude/*.local.json
```

I used the wildcard form rather than the exact filename, per the bead's own
"or a `.claude/*.local.json` pattern if other local-only files follow the same
naming convention" option. I checked `ls -la .claude/` first — only
`settings.local.json` currently exists there — but `settings.local.json` is
Claude Code's standard convention for per-checkout local overrides, so the
wildcard covers any sibling `*.local.json` a future session adds (e.g. an
`mcp.local.json`) without needing another bead for the identical problem.

## Evidence (both acceptance criteria)

1. Pattern match confirmed:
   ```
   $ git check-ignore -v .claude/settings.local.json
   .gitignore:25:.claude/*.local.json	.claude/settings.local.json
   ```

2. No longer untracked in `git status --short`:
   ```
   $ git status --short | grep settings.local
   NOT in status output (good)
   ```
   (ran as `git status --short | grep settings.local || echo "NOT in status
   output (good)"` — grep found nothing, confirming the file is gone from the
   status report)

3. Simulated a fresh sandbox worker dropping a sibling local file, to check
   the wildcard actually generalizes and not just the one hardcoded name:
   ```
   $ touch .claude/probe.local.json && git status --short | grep -E "\.claude/"
   (no output)
   $ rm .claude/probe.local.json
   ```

## Test suite

```
$ uv run python -m unittest discover tests
Ran 409 tests in 0.528s
OK
```
409/409 pass. This change touches only `.gitignore`, not any Python source,
so the suite result is a sanity check that I didn't break anything else in
the working tree, not direct evidence for this bead — the git-status checks
above are the direct evidence.

The `GitHub search failed`, `OpenAlex ... failed`, and `USPTO ... failed /
requires an API key` lines in the test output are expected: they're
fixture-driven tests exercising each fetcher's error/refusal paths (e.g. the
USPTO "Refusing to fall back to placeholder patent data" guard), not live
network calls. No test suite run touched the network.

## Scope discipline

I did not touch any of the other untracked/modified files already sitting in
this working tree (`einstein/codegen.py`, `einstein/constraint_mining.py`,
`einstein/feasibility.py`, `einstein/report.py`, `einstein/sandbox.py`,
various `tests/test_*.py`, modified `einstein/cli.py` /
`einstein/github_fetcher.py` / `einstein/graph.py` / `einstein/store.py`,
`README.md`, `pyproject.toml`, `uv.lock`, `.beads/*.jsonl`, several other
`sandbox-handoffs/*.md` files). Those are prior sessions' work on other beads,
already dirty before I started (confirmed via `git status --short` at the
start of this session) — not in scope for einstein-myd and not evidence for
or against this bead's acceptance criteria. A human reviewing `git status`
will still see all of that; only `.claude/settings.local.json` is gone from
the list, which was the entire point of this bead.

## What I decided not to do

- Did not add a broader `.claude/*.local.*` (dropping the `.json` requirement)
  since no non-JSON local file convention exists in this repo today — that
  would be gitignoring a hypothetical, not an observed pattern.
- Did not touch `.claude/settings.json` (already ignored, untouched) or any
  other `.gitignore` section.

## What I could not verify

- I could not literally spin up a second `sandbox.sh` worker container in
  this session to confirm end-to-end; the `touch .claude/probe.local.json`
  simulation above is the closest available proxy and is what the bead's own
  "EVIDENCE TO CLOSE" section offers as an acceptable substitute ("spot check:
  start a worker, or simulate by touching the file").

## Git status at end of session (uncommitted, as instructed)

```
$ git status --short
 M .gitignore
 M .beads/issues.jsonl        (bd claim/close bookkeeping)
 M .beads/interactions.jsonl  (bd bookkeeping, pre-existing dirty)
 ... (pre-existing dirty files from prior sessions, unrelated — see above)
```

Only `.gitignore` (and beads' own bookkeeping files) reflect this bead's work.
Tree is left dirty per repo policy. No commit, no push, no `bd dolt push` was
run.

Suggested commands for the human reviewer:
```
git add .gitignore
git commit -m "Gitignore .claude/*.local.json to stop it recurring as untracked cruft"
```
