# einstein-18: Code generation agent node (Python/Qiskit)

## Bead state at start

`bd show einstein-18 --json` showed the bead already `in_progress`, `started_at:
2026-09-24T09:58:21Z` (this session — no prior worker's partial code was in the
tree; `git status` at session start was clean and `einstein/codegen.py` did not
exist). So this was a fresh claim, not a resume. Depends on einstein-16
(closed). Blocks einstein-19 (sandboxed execution + self-heal loop, still
open).

## What I changed

- **New: `einstein/codegen.py`** — the codegen agent node. `generate_code(idea,
  audit, *, llm, target_library=...)` synthesizes one `GeneratedCode` from an
  `Idea` (einstein-15) and its `Audit` (einstein-16); `generate(ideas, audits,
  ...)` is the batch form; `build_codegen_agent_node(...)` is the
  `AgentNodeFn` factory for `einstein.graph.build_graph`'s `agent_node=` slot,
  same pattern as `einstein.ideator`/`einstein.novelty_auditor`.
- **Modified: `einstein/graph.py`** — added `generated_code: list` to
  `AgentState` and `initial_state(...)`, docstring updated to match (same
  treatment `ideas`/`audits` got when einstein-15/16 landed).
- **New: `tests/test_codegen.py`** — 24 tests, no network, no real LLM (a
  `StubLLM` records prompts and returns canned text, per this repo's "the
  agent nodes need an LLM, and the tests must not").

## Design decisions this bead's spec left open, and what I did instead of guessing

1. **What feeds codegen, given the bead graph.** `bd show einstein-18` lists
   `DEPENDS ON: einstein-16` only — **not** einstein-17 (feasibility
   evaluator, still open). einstein-17 is a *sibling* dependent on einstein-16,
   not a predecessor of einstein-18. I read that literally: `generate_code`
   takes an `Idea` + its `Audit` directly and does not wait on, or invent, a
   feasibility go/no-go it has no input for. If a later bead wires
   ideator → auditor → feasibility → codegen into one sequential chain in
   `graph.py`, that's an integration decision for that bead, same reasoning
   `novelty_auditor.py` gives for not being wired as `build_graph`'s default.

2. **Which audits get codegen'd.** The bead's own words — "Synthesize a
   reference implementation from **the surviving proposal**" — I read as: only
   `Audit.verdict == "pass"` has survived. `generate_code` asserts this (same
   defensive-assert pattern as `ideate_gap` asserting `subject_type ==
   "paper"`); the batch `generate()` skips `force_pivot`/`reject`/no-audit
   cases with a note instead of raising, so nothing is silently dropped. Test:
   `test_rejected_idea_is_skipped_with_a_note_not_treated_as_pass`,
   `test_generates_for_passing_ideas_only`.

3. **The pinned target-library version.** "target library version pinned
   explicitly" — I picked `qiskit==2.5.2`, which is not guessed: I ran
   `curl -s https://pypi.org/pypi/qiskit/json | grep -o '"version":"[^"]*"'`
   on 2026-09-24 from inside this sandbox and it returned `2.5.2`. That
   command and date are recorded in `DEFAULT_TARGET_LIBRARY`'s comment in
   `codegen.py` so a reviewer can re-run it and see whether the pin is stale.
   `qiskit` is **not** added to `pyproject.toml` — it's a library the
   *generated* code targets, not one this pipeline imports or runs, same
   reasoning `novelty_auditor.py`'s docstring gives for not adding a Semantic
   Scholar client. `target_library` is a keyword arg (a knob, like
   `novelty_auditor.DEFAULT_THETA`), not a hardcoded constant — see
   `test_custom_target_library_overrides_default_and_prompt`.

4. **Response format.** One fenced ` ```python ... ``` ` block, same
   rigid-format-over-regex-guessing choice `einstein.ideator` made for
   `METHOD:`/`PROBLEM:` lines. No fence → `CodegenParseError` carrying
   `raw_response`, not a guess that the whole response is code. A fence with
   no `python` language tag is still accepted (some models omit it) — see
   `test_non_python_fence_is_still_accepted`.

## What this bead explicitly does NOT claim

`GeneratedCode.code` is a string nothing here executes, imports, or even
parses as Python. `GeneratedCode.note` says "candidate reference
implementation, not run or validated -- see einstein-19" — never "working
code," never "implementation" unqualified. Running it against `qiskit-aer` and
feeding stderr back in a self-heal loop is einstein-19's job (it already
depends on einstein-18 in the bead graph); I did not touch it and did not
attempt any exec/subprocess sandboxing here, since that would be scope creep
into a bead already filed.

## Verification

```
$ uv run python -m einstein.codegen
einstein.codegen self-check: OK

$ uv run python -m unittest discover tests
Ran 315 tests in 1.499s

OK
```

315 passed, 0 failed, 0 errors (up from the 282 recorded at einstein-16's
close on 2026-08-22; `tests/test_codegen.py` added 18 of that increase
(`grep -c "def test_" tests/test_codegen.py`), the remaining 15 predate this
session — other work landed on `einstein-0`'s children between 2026-08-22 and
now that I did not audit. Not my count to explain, just noting the baseline
moved for anyone diffing against the einstein-16 handoff.) The `arXiv search failed` / `GitHub search
failed` / `USPTO search failed` / `OpenAlex lookup failed` lines interleaved
in that output are expected: they're fixture-injected fake-transport-failure
tests in the fetcher suites (asserting the fetchers handle 429/500/etc.
correctly), not real network calls and not failures — the final `OK` line is
the authoritative result.

## What I decided not to do, and why

- **Did not wire codegen into `build_graph` as a default, or chain it after
  the ideator/auditor nodes.** Same reasoning `einstein.ideator` and
  `einstein.novelty_auditor` give for not being the default `agent_node=`:
  that's a pipeline-integration decision, and per the bead's own dependency
  graph codegen doesn't have a defined relationship to einstein-17's
  feasibility gate yet — sequencing that without a spec would be guessing.
- **Did not touch einstein-17 (feasibility evaluator) or einstein-19
  (sandboxed execution).** Out of scope for this bead; einstein-19 already
  exists as a separate open bead that depends on this one.
- **Did not add `qiskit` to `pyproject.toml`.** It's a target for generated
  code, not a dependency of this pipeline — see design decision #3 above.
- **Did not implement any static validation of the generated code (e.g.
  `ast.parse` to check it's syntactically valid Python) here.** The bead text
  frames validation as einstein-19's job ("sandboxed execution... without this
  the pipeline ships non-functional code"); adding a partial check here would
  either duplicate that work or create two divergent notions of "valid."

## What I could not verify

- **Whether a real LLM, given this exact prompt, reliably produces
  Qiskit 2.5.2-compatible code with no deprecated-API drift.** That claim is
  fundamentally untestable without calling a real model and then running the
  result against `qiskit-aer` — which is exactly what einstein-19 is for. This
  module's tests verify prompt construction, parsing, and routing
  (deterministic, LLM-free per this repo's standard) — not code quality or
  correctness of anything an LLM would actually generate. Flagging this
  explicitly rather than letting "24 passing tests" read as "codegen works."
- **Whether `qiskit==2.5.2` is still PyPI's latest by the time this is
  reviewed.** It was as of the `curl` command above at authoring time; pins
  drift, that's the entire premise of the bead's "deprecated-API drift is the
  top failure mode" sentence. A reviewer should re-run the `curl` command
  before trusting the number if much time has passed.

## Git status — per repo instructions, NOT committed

```
$ git status --porcelain=v1
 M einstein/graph.py
?? einstein/codegen.py
?? tests/test_codegen.py
```

(`.claude/settings.local.json` also shows untracked in raw `git status` — that
is harness-generated permission config from this session, not part of this
bead's work; I did not create or edit it and it is not part of the diff to
commit.)

Suggested commands for a human to run (not run by me — git policy says do not
commit/push):

```
git add einstein/codegen.py einstein/graph.py tests/test_codegen.py
git commit -m "einstein-18: codegen agent node (Idea+Audit -> candidate Qiskit reference impl)"
```

## Beads

- `bd update einstein-18 --claim` at session start.
- Closing `einstein-18` now: acceptance criteria met (reference-implementation
  synthesis from a surviving proposal, prompt carries `context_equations`,
  target library version pinned explicitly and documented) and the full suite
  is green (315 tests, `uv run python -m unittest discover tests`).
- Did not close einstein-19 or einstein-17 — both remain open, out of scope
  for this bead.
