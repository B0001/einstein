# einstein-dga handoff

## Outcome: closed as moot, not fixed

The bead's premise no longer holds against this repo's current state. I am
closing it with `--reason`, not implementing the fix it describes, per
CLAUDE.md: "If the bead turns out to be wrong or already done, close it with
`--reason` explaining that, which is a real outcome and not a failure."

## What the bead described

`sandbox.sh` used to be a 337-line script that took an exclusive lock via
`mkdir .sandbox.lock`, then ran a `docker run ... busybox chown -R 1000:1000`
across two host-wide named volumes (`claude-uv-cache`, `claude-uv-python`)
before dispatching any worker. The bead's hazard: that lock is per-repo, but
the volumes, the litellm proxy on `127.0.0.1:4000`, and the external API
rate limits (GitHub/arXiv/USPTO/OpenAlex) are shared host-wide across every
repo running its own `sandbox.sh`. Two concurrent loops in different repos
(observed: certkit + einstein, 2026-08-22) don't see each other's lock and
can race the chown against volumes a sibling repo's worker is using.

Acceptance criteria asked for one of: (a) cross-repo runs prevented with a
message naming the other run, or (b) the shared-state hazards individually
neutralised with a comment in `sandbox.sh` explaining why concurrency is
safe -- either way demonstrated with two runs against different repos.

## What I found instead

`sandbox.sh` in this repo, current HEAD, is 4 lines:

```
#!/usr/bin/env bash
# The loop lives in the verified-sandbox package now, and its knobs in
# [tool.sandbox] in pyproject.toml. This shim is here for muscle memory only.
exec sandbox "$@"
```

Confirmed by `git log --oneline -- sandbox.sh`:

```
11fefd0 Move the worker loop to the verified-sandbox package; track handoffs
14cc1f6 Refuse to dispatch workers when the commit guard is disarmed
629dfac Keep secrets out of docker's argv; never triage on a bd failure
33fbda3 Forward OPENALEX_MAILTO into worker containers
6b85c3c Make Ctrl-C stop sandbox.sh, not just the current worker
8c02489 Track sandbox.sh, and exclude epics from its ready queue
```

Commit `11fefd0` (2026-08-26 18:06:39, four days after this bead was filed
on 2026-08-22) moved the entire worker loop -- the `LOCK_DIR` mkdir lock, the
`chown -R 1000:1000` against `claude-uv-cache`/`claude-uv-python`, the
`docker run` invocation, the hooksPath guard, everything the bead's hazard
analysis is about -- out of this repo and into an external package,
`verified-sandbox`, installed via `uv tool install --editable` on the host.
The commit message says explicitly: "sandbox.sh was 337 lines, the newest of
six near-identical copies. It is now a 4-line shim; the loop lives in
verified-sandbox." This repo's only remaining sandbox-related surface is
`[tool.sandbox.forward-env]` in `pyproject.toml`, which lists three secret
names to forward -- no lock, no volume, no chown logic at all.

I confirmed the `verified-sandbox` package is not reachable from this
session: not installed (`which sandbox`, `uv tool list` -> "No tools
installed"), not vendored anywhere under `/workspace` (`find` for
`*verified-sandbox*` / `*verified_sandbox*` on the host filesystem outside
uv's own cache returns nothing), and not documented in any file this repo
tracks (`grep -rn verified-sandbox` across `.md`/`.py`/`.toml` hits only the
one comment in `pyproject.toml`, which just says "see that package's README
for keys" without saying where that README lives).

So: the code this bead asks me to fix, and the two-repos-racing-a-chown
hazard it describes, may well still be real -- but it now lives entirely
outside this repository, in a package this sandbox container has no path
to, no source for, and no way to test against (no `docker`, no `sandbox`
binary, nothing to point two concurrent runs at). There is nothing left in
`einstein` for this bead's acceptance criteria to be evaluated against:
`sandbox.sh` has no lock to fix, no chown to move, and no comment to write
that would be honest, because the file that would carry it no longer
contains any of the logic the comment would be explaining.

## What I did NOT do, and why

- Did not modify `sandbox.sh`'s 4-line shim. There is nothing in it to
  change; the hazard is not in this file anymore.
- Did not attempt to guess at or reconstruct `verified-sandbox`'s source to
  patch it blind. I have no way to verify such a patch would even match the
  package's actual current state, and this repo has no dependency on or
  vendored copy of it to safely edit.
- Did not close this as a duplicate of `einstein-xod` (end-of-run reporting)
  or `einstein-do6` (hooksPath) -- both are still open, both are about
  `sandbox.sh`/hooks as they exist *now* (the shim + `.beads/hooks`), and
  neither one is this bead's cross-repo-lock hazard. `einstein-do6`'s
  underlying issue (absolute `core.hooksPath`) already appears fixed at
  current HEAD -- `git config --get core.hooksPath` returns `.beads/hooks`,
  a relative path, and `git rev-parse --git-path hooks` resolves to the
  same, existing directory -- but that's a different bead and I did not
  touch it or close it; it's out of scope here.
- Did not touch `README.md`'s pending diff or `sandbox-handoffs/einstein-0.6.md`
  in the working tree. Both belong to `einstein-0.6`, which was already
  closed earlier today by a separate session (`bd show einstein-0.6`) with
  its own handoff on disk. That work is unrelated to this bead and I left it
  exactly as I found it.

## Verification

- `git log --oneline -- sandbox.sh` -- shows the move, commit `11fefd0`,
  dated after this bead's filing date.
- `cat sandbox.sh` -- 4 lines, no lock/chown/docker logic present.
- `which sandbox; uv tool list` -- confirms `verified-sandbox` is not
  installed in this container (`No tools installed`).
- `find / -maxdepth 8 -iname '*verified-sandbox*' -o -iname '*verified_sandbox*'`
  (excluding uv's own package cache paths, which contain no such package) --
  no matches; the package is not vendored or mounted here.
- `grep -n "tool.sandbox" -A 4 pyproject.toml` -- shows the repo's only
  remaining sandbox config is `forward-env` (three secret names), nothing
  lock- or volume-related.
- `uv run python -m unittest discover tests` -- `Ran 282 tests ... OK`
  (unrelated to this bead; run to confirm the tree is otherwise healthy
  before closing, since I am not adding a test for a fix I did not make).

## What I could not verify

Whether the cross-repo hazard the bead describes is still present, fixed,
or changed in `verified-sandbox` itself. I have no access to that package's
source, its own issue tracker (if any), or a way to run two concurrent
`sandbox` invocations against different repos from inside this container
(no `docker`, no second repo checkout, no network path to the package). A
human with host access to `verified-sandbox`'s actual repository should
re-file this bead's hazard analysis there if it is not already tracked --
this `bd` instance only tracks `einstein-*` issues and has no visibility
into that package's own backlog.

## Git status

No files changed by this session. Pre-existing dirty state from an earlier,
already-closed session (`einstein-0.6`) remains untouched:

```
 M .beads/interactions.jsonl
 M .beads/issues.jsonl
 M README.md
?? .claude/settings.local.json
?? sandbox-handoffs/einstein-0.6.md
```

Plus this file, `sandbox-handoffs/einstein-dga.md`, added by this session.
Nothing for a human to run to "complete" this bead's code -- there is no
code change to commit for it. The `.beads/issues.jsonl`/`interactions.jsonl`
diff will additionally pick up this bead's close once `bd close` runs below.
