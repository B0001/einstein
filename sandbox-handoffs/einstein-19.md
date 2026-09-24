# einstein-19: Sandboxed execution + self-heal loop

## Bead state at start

`bd show einstein-19` was `OPEN`, not `in_progress` -- a fresh claim, not a
resume. `git status` at session start already showed einstein-18's uncommitted
work in the tree (`einstein/codegen.py`, `einstein/graph.py` modified,
`tests/test_codegen.py`, `sandbox-handoffs/einstein-18.md`) -- that work is
closed (`bd show einstein-18` confirms `status: closed`) and I read it, then
built on top of it. I did not touch `einstein/codegen.py` or
`tests/test_codegen.py`. Depends on einstein-18 (closed). Blocks einstein-20
(still open, out of scope here).

## What I changed

- **New: `einstein/sandbox.py`** -- `self_heal(generated, *, llm, run=...,
  max_retries=2)` runs one `GeneratedCode` (einstein-18) through a real
  interpreter via an injectable `run: RunFn`, and on failure feeds the exact
  stderr back to an `LLMClient` for a repair, up to `max_retries` times.
  `build_sandbox_agent_node(...)` is the `AgentNodeFn` factory for
  `einstein.graph.build_graph`'s `agent_node=` slot, same pattern as
  `einstein.codegen`/`einstein.ideator`/`einstein.novelty_auditor`.
  `_default_run` is the real implementation: writes the candidate code to a
  file in a fresh `tempfile.TemporaryDirectory`, runs it via `uv run
  --no-project --isolated --with qiskit==2.5.2 --with qiskit-aer==0.17.2
  python <file>` (ephemeral env -- qiskit/qiskit-aer are never added to this
  repo's `pyproject.toml`), with a stripped environment (no
  `GITHUB_TOKEN`/`USPTO_API_KEY`/`OPENALEX_MAILTO`), a wall-clock timeout
  (`TIMEOUT_SECONDS = 60.0`), and `start_new_session=True` so a timeout kills
  the whole process group (`uv run` + the `python` it spawns), not an orphan.
- **Modified: `einstein/graph.py`** -- added `sandbox_outcomes: list` to
  `AgentState` and `initial_state(...)`, docstrings updated to match (same
  treatment `generated_code` got at einstein-18's close).
- **New: `tests/test_sandbox.py`** -- 17 tests, no network, no real
  subprocess, no real LLM: every `run` passed in is a fake `RunFn` (a plain
  function returning a canned `RunResult`), same "the agent nodes need an
  LLM, and the tests must not" reasoning extended to "and the sandboxed run
  must not touch the network either" (`_default_run` shells out to `uv run
  --with`, which resolves packages over the network).
- **New: `scripts/sandbox_live_check.py`** -- a hand-run script (same pattern
  as `scripts/harvest_gap_benchmark.py`/`openalex_search_recall_check.py`,
  NOT part of `discover tests`) that exercises `self_heal` with the *real*
  `_default_run` against real `qiskit`/`qiskit-aer` in a real subprocess.
  This is where the bead's acceptance criterion is actually demonstrated end
  to end -- see "Verification" below.

## Design decisions the bead's spec left open, and what I did instead of guessing

1. **"An isolated subprocess/container"** -- the bead's own wording accepts
   either. I chose subprocess: a fresh `uv run --with` ephemeral environment
   per attempt gives real package isolation (the generated code's `import
   qiskit` resolves against the exact pinned version, nothing this repo's own
   venv has) without a container runtime this sandbox may not have available.
   Documented explicitly in the module docstring what this subprocess
   isolation does NOT do -- no CPU/memory rlimit, no network egress block
   from inside the generated code (only wall-clock timeout catches a hang) --
   rather than let "isolated subprocess" read as more sandboxing than it is.

2. **What "N" is.** The bead says "retry up to N times" without naming N.
   `DEFAULT_MAX_RETRIES = 2` (3 total attempts) is a keyword-arg knob, same
   pattern as `novelty_auditor.DEFAULT_THETA` -- not a proven constant, a
   caller overrides with `self_heal(..., max_retries=N)`.

3. **Which qiskit-aer version.** Pinned `qiskit-aer==0.17.2` the same way
   einstein-18 pinned `qiskit==2.5.2`: I ran `curl -s
   https://pypi.org/pypi/qiskit-aer/json | grep -o '"version":"[^"]*"'` on
   2026-09-24 from inside this sandbox and it returned `0.17.2`. Recorded in
   `DEFAULT_AER_VERSION`'s comment in `sandbox.py` so a reviewer can re-run it
   and see whether the pin is stale. qiskit-aer is the execution backend a
   generated `QuantumCircuit` needs to actually run against -- the einstein-18
   codegen prompt doesn't ask the model to import it, so the sandbox supplies
   it as part of the run environment rather than depending on the generated
   code getting that import right itself.

4. **What feeds the repair prompt.** Only the failing code and its stderr
   (plus `idea_method`/`idea_problem`/`target_library` for grounding) -- not
   `stdout`, not the original `raw_response`. A model repairing code from a
   traceback doesn't need the prose that originally justified the idea, and
   including stale stdout from a crashed run would be noise, not signal.

5. **Verdict vocabulary.** Three states, not two: `"validated"` (attempt 0
   succeeded -- nothing was repaired, don't call it "repaired"),
   `"repaired"` (a later attempt succeeded), `"failed"` (bead's "fails after
   N"). Collapsing `"validated"`/`"repaired"` into one `"passed"` would hide
   whether the LLM's first codegen attempt was actually any good, which is
   exactly the kind of measurement this repo's standard asks for.

6. **A repair response with no parseable code block.** Rather than treat that
   as "loop forever" or "silently reuse the last code and retry the same
   thing", `self_heal` stops immediately and records `"failed"` -- one wasted
   attempt is enough evidence the model isn't going to produce something
   parseable; per this repo's "prefer abstention to a confident answer",
   retrying blind against a response we can't even read is not real progress.
   See `test_unparseable_repair_response_stops_early_and_is_recorded_as_failed`.

7. **`own copy of the fence regex, not an import from `einstein.codegen`.**
   `codegen.py`'s `_CODE_FENCE_RE`/`_extract_code` are private (underscore).
   Rather than reach into another module's internals, `sandbox.py` keeps its
   own copy of the same regex (`_CODE_FENCE_RE`, `_extract_repaired_code`,
   raising this module's own `RepairParseError` -- not `CodegenParseError`),
   following this repo's existing pattern of one exception type per parse
   boundary (`IdeationParseError`, `CodegenParseError`, now this one).

## Verification

Unit suite (deterministic, no network, no subprocess):

```
$ uv run python -m unittest discover tests
Ran 332 tests in 0.353s

OK
```

332 passed, 0 failed, 0 errors (up from 315 at einstein-18's close;
`tests/test_sandbox.py` added 17 of that increase --
`grep -c "def test_" tests/test_sandbox.py`). The `GitHub search failed` /
`OpenAlex ... failed` / `USPTO ... failed` lines interleaved in that output
are fixture-injected fake-transport-failure tests (same as noted in
einstein-18's handoff), not real network calls and not failures.

```
$ uv run python -m einstein.sandbox
einstein.sandbox self-check: OK
```

The bead's acceptance criterion -- **"deliberately broken snippet is repaired
or fails after N with the traceback recorded"** -- demonstrated with the real
`_default_run` (real `uv run` subprocess, real `qiskit`/`qiskit-aer`, real
stderr) via `scripts/sandbox_live_check.py`, not just a fake `RunFn`:

```
$ uv run python scripts/sandbox_live_check.py
target_library='qiskit==2.5.2' aer_version='qiskit-aer==0.17.2'

=== Scenario 1: expect verdict='repaired' ===
verdict='repaired' attempts=3 note='ran clean after 2 repair attempt(s)'
  attempt 0: is_repair=False succeeded=False returncode=1
    stderr tail: "SyntaxError: '(' was never closed"
  attempt 1: is_repair=True succeeded=False returncode=1
    stderr tail: "NameError: name 'circuit' is not defined"
  attempt 2: is_repair=True succeeded=True returncode=0

=== Scenario 2: expect verdict='failed' with traceback recorded ===
verdict='failed' attempts=3 note='failed after 3 attempt(s) (2 repair retries used of 2 allowed) -- see traceback, not run successfully'
traceback (last attempt's real stderr):
Traceback (most recent call last):
  File "/tmp/einstein-sandbox-fiwer1kg/candidate.py", line 1, in <module>
    import qiskit_this_package_will_never_exist_xyz_v3
ModuleNotFoundError: No module named 'qiskit_this_package_will_never_exist_xyz_v3'

Both scenarios matched einstein-19's acceptance criterion.
```

(Real total wall-clock for that script: `1.147s` real, per `time uv run
python scripts/sandbox_live_check.py` -- fast because `uv`'s package cache
was already warm from earlier manual checks this session; a cold-cache first
run pays a one-time wheel download.)

Two more manual checks not printed by the script above, run directly against
`_default_run`:

- **Timeout kill path**: `_default_run("import time\ntime.sleep(120)\n",
  timeout=3.0)` returned `timed_out=True, returncode=-9`, stderr ending
  `"[sandbox] killed after exceeding 3.0s timeout"` -- the process-group kill
  fires and `communicate()` returns promptly rather than hanging.
- **Secret-stripping**: with `GITHUB_TOKEN=secret123 USPTO_API_KEY=secret456`
  set in this shell's env, `_sandbox_env()` returned a dict containing
  neither key (`'GITHUB_TOKEN' in env` and `'USPTO_API_KEY' in env` both
  `False`), while `PATH` was present.

Every number above is reproducible with the exact command shown next to it.

## What I decided not to do, and why

- **Did not wire `build_sandbox_agent_node` into `build_graph` as a default,
  or chain it after codegen/auditor/ideator.** Same reasoning every prior
  agent-node bead in this repo gives: that's a pipeline-integration decision
  for a later bead (einstein-20, which this bead's own `BLOCKS` edge hands
  off to), not this one's job.
- **Did not enforce CPU or memory rlimits on the sandboxed subprocess**, only
  a wall-clock timeout. The bead's acceptance criterion is about repair/fail
  correctness, not resource-exhaustion hardening; adding `resource.setrlimit`
  without a stated threat model to size it against would be guessing at a
  number and calling it security. Flagged explicitly in the module docstring
  as a known gap rather than silently absent.
- **Did not block outbound network access from inside the generated code.**
  Only the timeout would catch code that, say, opens a socket and hangs;
  code that makes a quick network call and returns is not stopped. Same
  reasoning as above -- flagged, not silently solved.
- **Did not add `qiskit`/`qiskit-aer` to `pyproject.toml`.** Same reasoning
  einstein-18 gave for `qiskit`: it's a library the *generated* code runs
  against via an ephemeral `uv run --with` environment, not a dependency this
  repo's own code imports.
- **Did not implement a real LLM-driven repair check** (i.e. did not call an
  actual model to see whether a real repair from a real traceback would
  succeed). There is no LLM credential wired into this sandbox environment.
  `scripts/sandbox_live_check.py` uses a scripted stand-in
  (`_ScriptedRepairLLM`) that returns pre-written responses in order -- it
  proves the *loop* (real subprocess, real stderr capture, real retry-until-
  success-or-exhaustion) works, not that a real model's repairs would
  succeed. Flagged explicitly in that script's own docstring, same
  unverifiable-without-a-real-model gap einstein-18's handoff already noted
  for codegen itself.

## What I could not verify

- **Whether a real LLM, given a real traceback, reliably produces a working
  repair.** Fundamentally untestable without a real model credential --
  flagged above, not asserted either way.
- **Whether SIGKILLing the process group leaves zero orphaned `uv`/`python`
  processes.** The timeout path was verified functionally (`timed_out=True`,
  correct message, `communicate()` returned promptly rather than hanging),
  but this container has no `ps` (`/bin/bash: line 1: ps: command not
  found`), so I could not independently confirm the process tree is fully
  reaped after the kill. `start_new_session=True` + `os.killpg(..., SIGKILL)`
  is the standard correct pattern for this, but "standard pattern" is not the
  same as "measured on this box" -- noting the gap rather than claiming it.
- **Whether `qiskit-aer==0.17.2` is still PyPI's latest by the time this is
  reviewed.** It was as of the `curl` command above at authoring time; same
  caveat einstein-18 raised for its own pin.

## Git status -- per repo instructions, NOT committed

```
$ git status --porcelain=v1
 M .beads/interactions.jsonl
 M .beads/issues.jsonl
 M einstein/graph.py
?? .claude/settings.local.json
?? einstein/codegen.py
?? einstein/sandbox.py
?? sandbox-handoffs/einstein-18.md
?? sandbox-handoffs/einstein-19.md
?? scripts/sandbox_live_check.py
?? tests/test_codegen.py
?? tests/test_sandbox.py
```

`einstein/codegen.py`, `tests/test_codegen.py`, `sandbox-handoffs/einstein-18.md`
are einstein-18's prior, already-closed work -- untouched by me, included
here only because the tree was already dirty with them at session start.
`.claude/settings.local.json` is harness-generated permission config, not
part of any bead's work.

Suggested commands for a human to run (not run by me -- git policy says do
not commit/push):

```
git add einstein/codegen.py einstein/graph.py einstein/sandbox.py \
        scripts/sandbox_live_check.py tests/test_codegen.py tests/test_sandbox.py \
        sandbox-handoffs/einstein-18.md sandbox-handoffs/einstein-19.md
git commit -m "einstein-18: codegen agent node; einstein-19: sandboxed execution + self-heal loop"
```

(Or split into two commits along the einstein-18/einstein-19 file boundary
above if a reviewer prefers that history.)

## Beads

- `bd update einstein-19 --claim` at session start.
- Closing `einstein-19` now: acceptance criterion met and demonstrated with a
  real subprocess against real qiskit-aer (`scripts/sandbox_live_check.py`,
  output captured above) -- a deliberately broken snippet is repaired across
  two rounds, and a deliberately unfixable one fails after N retries with its
  real traceback recorded in `SandboxOutcome.traceback`. Full suite green:
  332 tests (`uv run python -m unittest discover tests`).
- Did not close einstein-20 -- out of scope for this bead, next in the chain.
