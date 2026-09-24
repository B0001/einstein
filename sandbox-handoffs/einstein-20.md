# einstein-20: Proposal report output

## What I changed

New module `einstein/report.py`: `build_proposals(gaps, ideas, audits,
feasibility, generated_code, sandbox_outcomes)` / `render_json(proposals,
notes)` / `render_markdown(proposals, notes)` / `build_report_agent_node()`.

- `Proposal` dataclass (frozen, slots) carries one flattened record per
  `SandboxOutcome`: gap classification (`gap_kind`, `gap_matches`), the idea
  (`idea_method`, `idea_problem`, `idea_context_equations`), the novelty
  verdict (`novelty_verdict`, `novelty_note`, `novelty_rationale`),
  feasibility (`feasibility_verdict`/`_compute`/`_data`/`_constraints`/
  `_blueprint`/`_note`, all `str | None`), the generated code
  (`target_library`, `code`, `codegen_note`), and the run evidence
  (`sandbox_status`, `sandbox_attempts`, `sandbox_note`, `traceback`). A
  fixed `note` field (`REPORT_NOTE`) is attached to every proposal and to
  the top-level JSON/Markdown output.
- **Why it iterates `sandbox_outcomes`, not `ideas`/`audits`/
  `generated_code`**: only a `SandboxOutcome` constitutes "run evidence" —
  the bead's own acceptance language. An idea that was never codegen'd or
  never sandboxed has no working code and no run evidence to report, so it
  produces no `Proposal`.
- `build_proposals` correlates everything by `gap_key` (the existing
  cross-stage key from `einstein.gaps.Gap.gap_key`). For each sandbox
  outcome it looks up the matching gap/idea/audit/generated_code (and,
  optionally, feasibility) and asserts `audit.verdict == "pass"` (a caller
  pairing-bug check — codegen only ever runs on a passing audit's idea per
  `einstein.codegen`'s own precondition). Two failure paths, both produce a
  **note, not an exception**, and neither aborts the batch:
  - missing gap/idea/audit/generated_code for an outcome's `gap_key` → note
    naming exactly what's missing, outcome skipped.
  - a pairing-bug `AssertionError` (e.g. a non-`"pass"` audit incorrectly
    paired with an outcome) → caught, turned into a note containing
    `"pairing bug"`, outcome skipped.
- Feasibility is optional: `einstein.feasibility`'s own docstring documents
  feasibility and codegen as siblings (both consume `audits` independently,
  neither depends on the other), so a sandboxed proposal may have no
  feasibility assessment. When absent, all six `feasibility_*` fields are
  `None` — never a fabricated verdict.
- `render_json` emits `{"note": REPORT_NOTE, "counts": {validated: n,
  repaired: n, failed: n}, "proposals": [...], "notes": [...]}`.
  `render_markdown` emits one `##` section per proposal (gap classification,
  method/problem, novelty verdict, feasibility block or an explicit "not
  assessed for this idea" line, sandbox status, a fenced ```python code
  block, and — only when `sandbox_status == "failed"` — a "Traceback from
  the final attempt" fenced block) plus a `## Notes` section when `notes` is
  non-empty.
- `build_report_agent_node()` — the first `build_*_agent_node` factory in
  this codebase that takes **no `llm` argument**: report.py is pure
  aggregation over already-produced records, calls no model. Returns
  `{"proposals": [...], "agent_notes": [summary, *notes]}`. **Not** wired
  into `build_graph`'s default chain — same "integration is a later bead's
  decision" pattern every prior stage (ideator/auditor/feasibility/codegen/
  sandbox) already follows; `einstein/cli.py` still only wires
  `ingest -> analyze -> agents(stub)` and none of those five modules are
  wired in either, so report.py joining them as unwired is consistent, not
  a gap I'm leaving behind.
- `einstein/graph.py`: `AgentState` gained a `proposals: list` field,
  `initial_state()` now sets `proposals=[]`. Docstring extended to describe
  `proposals` as the last stage, correlating every prior field by `gap_key`.

### Where I was tempted to write "novel" / "validated" and what I wrote instead

The bead title says **"validated proposals"**. The literal reading would
filter to `sandbox_status == "validated"` only, hiding `"repaired"` and
`"failed"` runs. I did not do that — this is a self-directed design
decision, not yet reviewed by a human, so flagging it here explicitly:

- `sandbox_status` is carried through verbatim (`"validated"` /
  `"repaired"` / `"failed"`) on every `Proposal`, including failed ones.
- A failed proposal is rendered in full: its real code, its real
  `traceback` (whatever `einstein.sandbox.SandboxOutcome.traceback`
  recorded from the actual subprocess run), not suppressed or summarized
  away. This matches the repo standard: *"report bad/negative results
  honestly rather than tuning until a demo looks good."* Filtering failures
  out of the report would be exactly that kind of tuning.
- Nothing in this module's output ever uses the word "novel" on its own —
  `novelty_verdict`/`novelty_note` are copied verbatim from
  `einstein.novelty_auditor.Audit`, whose own note already reads "no match
  found within this index, these sources, this threshold" rather than
  "novel" (that phrasing discipline was established in einstein-16, not
  reinvented here — `report.py` just passes it through unchanged).
- `feasibility_verdict` of `"go"` is never re-labeled "feasible" or
  "validated" in the Markdown/JSON output — it's rendered as exactly what
  `einstein.feasibility.Feasibility.note` already says it is: an LLM's
  qualitative read, "not measured."
- `REPORT_NOTE` (attached to every output) spells out explicitly that
  `"validated"`/`"repaired"` means the code ran without raising in a
  sandboxed subprocess — not that the underlying idea is correct, useful,
  or novel — and that `"failed"` proposals are included deliberately, not
  filtered, so a reader doesn't need to infer the filtering policy from the
  data.

## Test evidence

`tests/test_report.py` — 16 new tests, no network, no LLM (report.py never
imports or calls one). Covers: full chain correlation into one `Proposal`
with every field checked against its source record; `repaired` and `failed`
outcomes both appearing in `proposals` (not just `validated`), including
the failed proposal's real code and traceback; feasibility present vs.
absent (`None` fields, not fabricated); a sandbox outcome with no matching
`generated_code` → skipped with a note naming what's missing; a sandbox
outcome with no matching gap/idea/audit at all → skipped with a note naming
all four; a non-`"pass"` audit paired with an outcome → caught as a
"pairing bug" note, not raised; empty-input → empty output;
`render_json`'s JSON validity, `counts` correctness (including a mixed
validated/failed case), and `REPORT_NOTE` framing; `render_markdown`'s
headers, code fence, per-status verdict text, "not assessed" text only when
feasibility is absent, traceback block only on failed proposals, and the
`## Notes` section appearing only when notes exist; `build_report_agent_node
()`'s `AgentState`-shaped output on both a populated and an all-empty state.

Module self-check:
```
$ UV_PROJECT_ENVIRONMENT=/tmp/venv uv run python -m einstein.report
einstein.report self-check: OK
```

New test file standalone:
```
$ UV_PROJECT_ENVIRONMENT=/tmp/venv uv run python -m unittest tests.test_report -v
...
Ran 16 tests in 0.001s

OK
```

`einstein/graph.py`'s own self-check still passes after the `AgentState`
change:
```
$ UV_PROJECT_ENVIRONMENT=/tmp/venv uv run python -m einstein.graph
einstein.graph self-check: OK
```

Full suite:
```
$ UV_PROJECT_ENVIRONMENT=/tmp/venv uv run python -m unittest discover tests
Ran 366 tests in 0.348s

OK
```
(350 tests existed on disk before this session per einstein-17's close note;
+16 from `tests/test_report.py` = 366. The GitHub/OpenAlex/USPTO "search
failed"/"fetch requires an API key" lines in the run output are `tests/`
fixtures deliberately simulating 429/500/401/403 responses and a
missing-API-key refusal — not live network calls, not failures; every one
of those tests asserts on the resulting error-handling behavior.)

Every number in this handoff (16 new tests, 366 total, the two self-checks)
is reproducible by re-running the exact commands shown above; none is
hand-computed or asserted without a command backing it.

## What I decided not to do, and why

- **Did not filter `proposals` down to `sandbox_status == "validated"`**
  despite the bead title saying "validated proposals" — see the section
  above. If a human reviewer disagrees, the fix is a one-line filter at the
  call site (`build_report_agent_node` or `build_proposals`'s caller), not
  a redesign — I did not want to bake an irreversible information loss into
  the aggregation layer itself when the un-filtered form is one filter-in
  away from either interpretation.
- **Did not wire `build_report_agent_node` into `build_graph`'s default
  chain**, and did not wire `report.py` into `einstein/cli.py`. Same
  reasoning as every prior stage bead (ideator/auditor/feasibility/codegen/
  sandbox): assembling the full ingest -> ... -> report chain into one
  compiled graph or one CLI command is an integration decision for a later
  bead, not this one. `cli.py` currently stops at the LLM-free stub agent
  node; none of the five upstream agent modules are wired in either, so
  leaving report.py unwired keeps it consistent with its siblings rather
  than introducing a one-off partial integration.
- **Did not add a file-writing helper.** `render_json`/`render_markdown`
  return strings; nothing in this module opens a file or picks a path. A
  future integration bead (or a human today) can decide where proposals get
  persisted — `report.py`'s job per the bead description is "emit... as
  structured JSON + markdown," which a string return value satisfies without
  presuming a destination.
- **Did not add a numeric "confidence" or ranking score** across proposals.
  Neither the bead nor any upstream module produces such a number, and
  inventing one would violate this repo's "a number may only exist if code
  produces it" standard. `counts` in `render_json` is the only aggregate
  number, and it's a plain `len()` per status, fully traceable to the input
  list.

## What I could not verify

- No end-to-end run against real LLM-produced ideas/audits/feasibility/
  codegen/sandbox output exists — only the deterministic fixture-based
  module self-check and unit tests, per this repo's "agent nodes need an
  LLM, tests must not" rule (this module in particular needs no LLM at all,
  so there is no real-model behavior to verify here — but the upstream
  records it consumes were all hand-built fixtures, not real model output).
- Whether the Markdown output renders acceptably in a real Markdown viewer
  at realistic proposal counts (dozens of proposals, longer code bodies)
  is unverified — only checked structurally (substring assertions) against
  small fixtures.

## Git status — commands for a human to run

Tree is dirty and uncommitted per this repo's git policy (no commit/push
from inside the sandbox). This session's own changes:

- New: `einstein/report.py`, `tests/test_report.py`,
  `sandbox-handoffs/einstein-20.md`
- Modified: `einstein/graph.py` (added `proposals: list` to `AgentState` +
  `initial_state`), `.beads/issues.jsonl` / `.beads/interactions.jsonl` (bd
  claim/close bookkeeping)

Everything else showing as untracked/modified in `git status` predates this
session (per einstein-17's and einstein-19's own handoffs) and was not
touched here.

Suggested commands for a human reviewer:
```
git add einstein/report.py tests/test_report.py einstein/graph.py \
        sandbox-handoffs/einstein-20.md .beads/issues.jsonl .beads/interactions.jsonl
git commit -m "einstein-20: proposal report output"
```

## bd status

Closing `einstein-20` — acceptance criteria (validated proposals as
structured JSON + markdown: gap classification, novelty verdict,
feasibility, working code, run evidence) are met by `einstein/report.py`,
evidenced by the passing test suite above. See "Where I was tempted to
write... validated" above for the one substantive design call a reviewer
should sanity-check: failed sandbox runs are included in the report, not
filtered out.
