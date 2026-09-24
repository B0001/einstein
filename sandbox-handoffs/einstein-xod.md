# einstein-xod handoff

## Outcome: closed with reason — bead's target no longer exists in this repo

No code was changed. This is a "bead turned out to be wrong" close, not an
implementation.

## What the bead asked for

Make `sandbox.sh`'s end-of-run report (bead cited `sandbox.sh:251-257`) name
which beads closed during a run and show the uncommitted tree they left,
instead of the flat "Nothing was committed or pushed" line.

## What I found

`sandbox.sh` at HEAD is 4 lines:

```
#!/usr/bin/env bash
# The loop lives in the verified-sandbox package now, and its knobs in
# [tool.sandbox] in pyproject.toml. This shim is here for muscle memory only.
exec sandbox "$@"
```

`git log --oneline -- sandbox.sh`:
```
11fefd0 Move the worker loop to the verified-sandbox package; track handoffs
14cc1f6 Refuse to dispatch workers when the commit guard is disarmed
629dfac Keep secrets out of docker's argv; never triage on a bd failure
33fbda3 Forward OPENALEX_MAILTO into worker containers
6b85c3c Make Ctrl-C stop sandbox.sh, not just the current worker
8c02489 Track sandbox.sh, and exclude epics from its ready queue
```

Commit `11fefd0` (2026-08-26, after einstein-xod was filed on 2026-08-22 per
`4c6b7da`) moved the entire 337-line worker loop — including the end-of-run
report the bead describes — out of this repo into an external package
`verified-sandbox`, installed with `uv tool install --editable` per
`pyproject.toml`'s comment:

```
# The worker loop lives in the verified-sandbox package (`uv tool install
# --editable`), not in this repo and not in its dependency graph -- it is a
# tool the repo is driven by, not a library the repo uses...
```

I verified `verified-sandbox` is not reachable from this container at all,
not just "outside this bead's scope":

```
$ which sandbox            # empty
$ uv tool list
No tools installed
$ find / -xdev -iname "*verified-sandbox*" -o -iname "*verified_sandbox*"
                            # no results
```

No submodule (`.gitmodules` absent), no vendor directory, no sibling checkout
next to `/workspace`. There is no file in this repo or filesystem that
implements the end-of-run report anymore — the acceptance criteria describes
behavior that must be implemented in `verified-sandbox`'s own repo, which this
sandbox has no access to.

## Decision

Closed `einstein-xod` with `--reason` rather than leaving it open against code
that doesn't exist here, and rather than implementing anything — there is
nothing left in this repo to implement it in. Did not file a duplicate bead
for the same work: this repo's bd tracker has no git remote ("Issues are
saved locally only" per `bd prime`), so a bead filed here could never be
closed by a worker with access to `verified-sandbox`. The underlying problem
the bead described (a run's end-of-run report not naming what it closed or
left dirty) is real and, as far as I can tell, still unaddressed — just not
fixable from inside this container. A human with access to the
`verified-sandbox` repo needs to pick it up there.

## Commands run (all read-only / verification, nothing to review as a diff)

```
bd show einstein-xod
git log --oneline -- sandbox.sh
git show 11fefd0 --stat
cat pyproject.toml
which sandbox; uv tool list
find / -xdev -iname "*verified-sandbox*" -o -iname "*verified_sandbox*"
uv sync && uv run python -m unittest discover tests
```

## Test suite

```
$ uv run python -m unittest discover tests
Ran 282 tests in 0.350s

OK
```

282 passed, 0 failed. The `GitHub search failed`, `OpenAlex lookup failed`,
`USPTO search failed`/`fetch requires an API key` lines in the output are
expected — they're log output from tests exercising stubbed error-path
fixtures (bad-status-code, missing-key branches), not live network calls. No
network access was made; this run is offline-safe per repo convention.

## What I did not do, and why

- Did not touch `sandbox.sh` or `pyproject.toml`'s `[tool.sandbox]` block —
  there's nothing there implementing the report; touching the shim or the
  forward-env declarations would not move the needle on the acceptance
  criteria.
- Did not attempt to reconstruct or reimplement `verified-sandbox` inside
  this repo (e.g. vendoring a copy) — that would fork a tool that's
  explicitly designed to be shared across "six near-identical copies" per the
  `11fefd0` commit message, reintroducing the exact duplication that move was
  meant to end. Out of scope for a single bead in one downstream repo.
- Did not file a new bead for the underlying problem — see "Decision" above
  for why a locally-tracked bead in this repo can't carry the work forward.

## Unverified / left for the human

- Whether `verified-sandbox` already has an open issue/bead tracking this
  same gap in its own tracker — I have no access to that repo from here to
  check.
- Whether the `BEADS_ACTOR=sandbox` hook-guard problem flagged in this bead's
  cross-reference note (`einstein-do6`, `core.hooksPath` pointing at a
  host-only path) has been fixed in `verified-sandbox` alongside the move —
  also unverifiable from inside this container.

## Suggested commands for the human

```
git status        # expect: clean, nothing changed by this session
```
Nothing to commit or push from this session.
