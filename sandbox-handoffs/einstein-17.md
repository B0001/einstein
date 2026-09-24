# einstein-17: Feasibility evaluator agent node

## What I changed

New module `einstein/feasibility.py`: `evaluate_feasibility(idea, audit, *,
llm)` / `evaluate(ideas, audits, *, llm)` / `build_feasibility_agent_node(*,
llm)`, mirroring the existing ideator/novelty_auditor/codegen agent-node
pattern in this repo.

- `Feasibility` dataclass: `gap_key`, `subject_id`, `idea_method`,
  `idea_problem`, `verdict` (`"go"` | `"no_go"`), `compute_requirement`,
  `dataset_availability`, `constraints`, `blueprint`, `raw_response`, `note`.
- `evaluate_feasibility` takes an `Idea` (einstein-15) and its `Audit`
  (einstein-16) directly, asserts `audit.gap_key == idea.gap_key` and
  `audit.verdict == "pass"` — same shape and same reasoning as
  `einstein.codegen.generate_code`: a force_pivot/reject idea hasn't survived
  the novelty audit, so there's nothing to assess feasibility for yet. This
  makes feasibility a *sibling* of codegen (both consume `ideas`+`audits`
  independently), not a predecessor — matching the bead dependency graph
  (`einstein-17 DEPENDS ON einstein-16` only, not on einstein-18) and
  `gemini_convo.md`'s own diagram (Feasibility only receives the "Novel
  Concept" branch out of the Novelty Auditor box).
- Response format: five required labeled sections, `COMPUTE:` / `DATA:` /
  `CONSTRAINTS:` / `VERDICT:` (`GO` or `NO_GO`, case-insensitive) /
  `BLUEPRINT:` (free text to end of response). Any section missing, or an
  unparseable `VERDICT:` line, raises `FeasibilityParseError` (carries
  `gap_key` + `raw_response`) instead of guessing — same "prefer abstention"
  precedent as `IdeationParseError`/`CodegenParseError`, own exception type
  per this repo's "one exception type per parse boundary" convention
  (`einstein.sandbox.RepairParseError` does the same).
- `evaluate()` (batch form) pairs `ideas` to `audits` by `gap_key`, skips (with
  a note, not silently) an idea with no matching audit or a non-`"pass"`
  audit, and catches any per-idea exception (parse failure or whatever the
  injected `llm.generate` raises) into a note rather than aborting the batch
  — same pattern as `einstein.novelty_auditor.audit` / `einstein.codegen.generate`.
- `build_feasibility_agent_node(*, llm)` — factory for `einstein.graph
  .build_graph`'s `agent_node=` slot, returns `{"feasibility": ...,
  "agent_notes": [...]}`. **Not** wired as `build_graph`'s default, same
  reasoning as the ideator/auditor/codegen nodes: chaining stages into one
  graph is a separate integration decision for a later bead.
- `einstein/graph.py`: `AgentState` gained a `feasibility: list` field
  (alongside the existing `ideas`/`audits`/`generated_code`/`sandbox_outcomes`),
  `initial_state()` now sets `feasibility=[]`. Docstring updated to explain
  `feasibility` is populated independently of `generated_code`, not
  downstream of it.

### Why the LLM does the actual judgment here (unlike the novelty auditor)

`einstein/novelty_auditor.py`'s theta comparison is pure, measured code (a
`cosine_similarity` number) with the LLM only narrating a verdict the code
already computed. There is no equivalent number anywhere in this repo (or in
`gemini_convo.md`) for "compute cost" or "dataset availability" — nothing
here measures GPU-hours or crawls dataset registries. So `evaluate_feasibility`
puts the actual go/no-go judgment behind the LLM, structured-and-parsed the
same way `einstein.ideator`/`einstein.codegen` do (labeled sections, strict
parse, `*ParseError` on malformance) — not the auditor's "LLM narrates a
precomputed fact" shape, because there is no precomputed fact here.

### Where I was tempted to overclaim, and what I wrote instead

The obvious failure mode for this bead is a "go" verdict reading as "this
will work." I did not let that stand: `Feasibility.note` is fixed to read
*"assessed by LLM from the stated method/problem text -- not measured; no
compute was provisioned and no dataset was actually located -- see
einstein-19 for the only measured run evidence this pipeline produces"* on
every `Feasibility` returned by `evaluate_feasibility`, `go` or `no_go`
alike. Nothing in this module calls a "go" verdict feasible, validated, or
measured — it's the model's own qualitative read of the method/problem text,
full stop.

## Test evidence

`tests/test_feasibility.py` — 18 new tests, no network, no real LLM (a
`StubLLM` records prompts and returns canned text, same pattern as
`tests/test_codegen.py`'s `StubLLM`). Covers: full section parsing on `go`
and `no_go`, case-insensitive `VERDICT:` parsing, the note's "not measured"
language, prompt carries method/problem/context_equations, `AssertionError`
on `force_pivot`/`reject`/mismatched-`gap_key` audits, `FeasibilityParseError`
on a missing section / missing blueprint / unparseable verdict (with
`gap_key`/`raw_response` on the exception), batch pairing-by-`gap_key`,
skip-with-note for a missing or non-pass audit, one bad idea not aborting the
batch, empty-input, and the `build_feasibility_agent_node` factory's
`AgentState`-shaped output on both a populated and a zero-idea state.

Module self-check (`uv run python -m einstein.feasibility`):
```
einstein.feasibility self-check: OK
```

Full suite:
```
$ UV_PROJECT_ENVIRONMENT=/tmp/venv uv run python -m unittest discover tests
Ran 350 tests in 0.367s

OK
```
(332 tests existed on disk before this session per einstein-19's close note;
+18 from `tests/test_feasibility.py` = 350. The arXiv/GitHub/OpenAlex/USPTO
"search failed"/"fetch requires an API key" lines in the run output are
`tests/` fixtures deliberately simulating 429/500/401/403 responses and a
missing-API-key refusal — not live network calls, not failures; every one of
those tests asserts on the resulting error-handling behavior.)

`einstein/graph.py`'s own self-check also still passes after the `AgentState`
change:
```
$ UV_PROJECT_ENVIRONMENT=/tmp/venv uv run python -m einstein.graph
einstein.graph self-check: OK
```

## What I decided not to do, and why

- **Did not wire `build_feasibility_agent_node` into `build_graph`'s default
  chain.** Same reasoning the ideator/auditor/codegen nodes give for not
  being the default: assembling ideator -> auditor -> {feasibility, codegen}
  -> sandbox into one compiled graph is an integration decision, not this
  bead's. It's a natural candidate for a future "wire the agentic loop
  end-to-end" bead alongside einstein-20.
- **Did not gate on anything besides `audit.verdict == "pass"`.** I
  considered also requiring `idea.context_equations` to be non-empty (richer
  grounding), but the ideator and codegen modules both treat empty
  `context_equations` as a normal, expected case (no `SourceExtraction` was
  supplied), so gating feasibility on it would be inventing a stricter
  requirement than any sibling module enforces.
- **Did not add a numeric "confidence" or cost estimate field.** Neither the
  bead nor `gemini_convo.md` names one, and inventing a number with no
  measured basis (no benchmark, no priced compute) is exactly the "a number
  is only allowed to exist if the code produces it" violation this repo's
  standard forbids. `compute_requirement`/`dataset_availability`/
  `constraints` are free text for that reason, not scored fields.

## What I could not verify

- I did not run this against a real LLM — only the injected `StubLLM`/
  fixture responses in the test suite and module self-check, per this
  repo's "the agent nodes need an LLM, and the tests must not" rule. Whether
  a real model reliably produces the five-section format under the prompt in
  `_build_prompt` is unverified; a malformed real response is exactly what
  `FeasibilityParseError` exists to catch rather than silently misparse.
- I did not wire this node into `build_graph` or run it end-to-end against
  real `Idea`/`Audit` objects produced by a live ideator/auditor run —
  out of scope per "what I decided not to do" above.

## Git status — commands for a human to run

Tree is dirty and uncommitted per this repo's git policy (no commit/push
from inside the sandbox). `einstein/codegen.py`, `einstein/sandbox.py`,
`tests/test_codegen.py`, `tests/test_sandbox.py`,
`scripts/sandbox_live_check.py`, and `sandbox-handoffs/einstein-{18,19}.md`
are untracked leftovers from prior (einstein-18/19) sessions, not touched by
this session — left as found. `.claude/settings.local.json` is also
pre-existing and untouched.

This session's own changes:
- New: `einstein/feasibility.py`, `tests/test_feasibility.py`,
  `sandbox-handoffs/einstein-17.md`
- Modified: `einstein/graph.py` (added `feasibility: list` to `AgentState` +
  `initial_state`), `.beads/issues.jsonl` / `.beads/interactions.jsonl` (bd
  claim/close bookkeeping)

Suggested commands for a human reviewer:
```
git add einstein/feasibility.py tests/test_feasibility.py einstein/graph.py \
        sandbox-handoffs/einstein-17.md .beads/issues.jsonl .beads/interactions.jsonl
git commit -m "einstein-17: feasibility evaluator agent node"
```
(Leaving `einstein/codegen.py`, `einstein/sandbox.py`, and their tests for
whoever owns closing out the einstein-18/19 commit separately — bundling
three beads' worth of unrelated new files into one commit would make the
diff harder to review than it needs to be.)

## bd status

Closed `einstein-17` — acceptance criteria (go/no-go + execution blueprint
from compute/dataset/constraints assessment) are met by
`einstein/feasibility.py`, evidenced by the passing test suite above.
