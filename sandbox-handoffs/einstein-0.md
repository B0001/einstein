# Handoff: einstein-0 (EPIC: Invention gap-detection pipeline)

**Status: left OPEN, as instructed.** 17/24 children closed (70%, up from
12/23 at session start — regenerate with `bd show einstein-0` and count the
`✓` lines under `CHILDREN`). The epic cannot be honestly closed: 7 children
are still open, including the entire back half of the agentic loop
(novelty auditor, feasibility, codegen, sandboxed validation, report
output).

## What changed this session, and the command that proves it

This session picked up from a prior worker's state (12/23 children done,
4 marked in_progress with mixed real/no progress) and closed five more
children plus filed one honest scope-boundary bead. In order of what a
`git log` / `bd show` walk would find:

1. **einstein-7** (arXiv LaTeX/equation extraction) — the implementation
   (`einstein/arxiv_source.py`) pre-existed but had **zero test coverage**
   against its own stated acceptance criterion. Added
   `tests/test_arxiv_source.py` (11 tests) with a fixture tarball built from
   a real equation in arXiv:1706.03762 ("Attention Is All You Need").
   Verify: `uv run python -m unittest tests.test_arxiv_source -v`.
2. **einstein-0.1** (gap-detection evaluation harness) — found already
   fully implemented and untracked in the working tree from a prior run;
   independently re-ran every claim before closing rather than trusting the
   prior state. Verify: `uv run python -m einstein.gap_benchmark`.
3. **einstein-12** (cross-pollination detection) — was marked in_progress
   with **no code at all**. Implemented `einstein/cross_pollination.py`
   (`detect_cross_pollination`) + `tests/test_cross_pollination.py`
   (13 tests). Verify:
   `uv run python -m unittest tests.test_cross_pollination -v` and
   `uv run python -m einstein.cross_pollination`.
4. **einstein-13** (gap dedup + velocity scoring) — found already complete
   (`einstein/velocity.py` + `tests/test_velocity.py`); verified and closed.
5. **einstein-21** (CLI + scheduled runs) — the CLI half
   (`einstein/cli.py`) pre-existed; the "cron/Actions" half of the bead's
   scope did not. Added `.github/workflows/nightly.yml` and
   `scripts/nightly_run.sh` (+ README sections documenting both). Neither
   was run live in this sandbox (no real Actions runner, and running the
   cron script would burn real API quota) — verified by manual review and
   `bash -n scripts/nightly_run.sh` ("syntax OK") only. **This is
   unverified beyond static review; say so, don't imply it's been run.**
6. **einstein-15** (ideator agent node, this session's main new work) —
   implemented `einstein/ideator.py`: an `LLMClient` Protocol
   (`generate(prompt) -> str`) as the one interface a real model call sits
   behind, `ideate_gap()`/`ideate()` that build a prompt from a
   paper-subject `Gap` (+ optional `SourceExtraction` equations/
   limitations/future-work context, capped at 5 equations) and parse a
   rigid `METHOD:`/`PROBLEM:` response into an `Idea`, raising
   `IdeationParseError` (not a guessed half-result) on a malformed
   response. Repo-subject gaps are explicitly rejected
   (`AssertionError` in `ideate_gap`) or skipped-with-a-note (`ideate`) —
   there is no Method to extract from a `Gap` alone for a repo whose paper
   was never found, and this module does not guess one.
   `build_ideator_agent_node()` matches `einstein/graph.py`'s
   `AgentNodeFn` signature; wired in by adding an `ideas: list` field to
   `AgentState`/`initial_state` (deliberately left untyped, not
   `list[Idea]`, so `graph.py` keeps zero import-time dependency on any
   specific agent's output type — same reasoning its own docstring already
   gives for not implementing the agents itself). Added
   `tests/test_ideator.py` (15 tests: parse success incl.
   case-insensitivity/whitespace tolerance, repo-gap rejection, missing-
   METHOD/missing-PROBLEM/freeform-prose parse-failure cases, equation/
   section context folding, equation-count truncation, batch
   skip-and-continue on one bad response, source-extraction lookup by
   subject id, agent-node wiring including the zero-gap case). Verify:
   `uv run python -m unittest tests.test_ideator -v`,
   `uv run python -m einstein.ideator`, `uv run python -m einstein.graph`.
7. **Filed einstein-0.3** (new bead, not worked): wiring
   `cross_pollination.py`/`velocity.py` into the live CLI/graph path
   requires product decisions (what is "the domain B corpus" for a
   single-domain CLI query? how does a live run get citation edges into
   `ingest`? how does a live run persist to `Store`?) beyond either bead's
   literal scope. Filed with the three concrete blockers spelled out
   rather than guessed at or silently done.

## Final test-run line (verbatim)

```
$ uv run python -m unittest discover tests
...
----------------------------------------------------------------------
Ran 259 tests in 0.320s

OK
```

The interleaved stderr lines (`GitHub search failed: 429 ...`,
`OpenAlex lookup failed: 500 ...`, `USPTO fetch requires an API key ...`,
etc.) are **expected output from stubbed-failure-path tests** exercising
each fetcher's non-200/rate-limit/missing-key handling — not live network
calls. No test in this suite makes a real HTTP request; every fetcher test
uses a fake HTTP client or a checked-in fixture (see
`tests/fixtures/`).

259 = 244 (prior-session baseline, itself 220 + 11 arxiv_source + 13
cross_pollination) + 15 new `test_ideator.py` tests.

## Every number in this handoff, and its regenerating command

| Number | Command |
|---|---|
| 259 tests, all passing | `uv run python -m unittest discover tests` |
| 15 new ideator tests | `uv run python -m unittest tests.test_ideator -v` \| grep -c ' ok$' |
| 11 arxiv_source tests | `uv run python -m unittest tests.test_arxiv_source -v` |
| 13 cross_pollination tests | `uv run python -m unittest tests.test_cross_pollination -v` |
| 17/24 children closed (70%) | `bd show einstein-0` |
| MAX_CONTEXT_EQUATIONS = 5 | `einstein/ideator.py`, module constant, exercised by `test_equations_truncated_to_max_context_equations` |
| Gap-benchmark FDR/flag-rate figures (37%, 100%, 16%) | unchanged this session — regenerate with `uv run python -m einstein.gap_benchmark`, see README's "Gap-detection evaluation harness" section, verified (not re-derived) by einstein-0.1's own close reason |

## Where I was tempted to write "novel" / "no prior art", and what I wrote instead

- In `einstein/ideator.py`'s prompt template, the gap's kind is surfaced to
  the LLM as `"{gap.kind} (candidate only -- unmatched in this index at
  this threshold, not a novelty claim)"` — explicitly re-stating the
  absence-of-evidence framing at the exact point where a prompt author
  would be tempted to write "novel invention" to get better-sounding LLM
  output. This carries `gaps.py`'s own "candidate, not a claim" standard
  into the one place downstream of it (an LLM call) most likely to launder
  it into confident prose.
- `Idea` does not have a `novelty` or `is_novel` field. The ideator's job
  is Method/Problem extraction, not a novelty verdict — that is
  explicitly einstein-16's job (the still-open novelty auditor), and nothing
  in this module pre-empts it.
- No new prose was added to the README's "Gap-detection evaluation
  harness" section (the one with the 37% FDR negative result) — it was
  read again this session to confirm the ideator's prompt-building
  correctly treats a `Gap` as "unmatched in this index," not "unbuilt," and
  left untouched.

## What I decided not to do, and why

- **Did not implement einstein-16 (novelty auditor)**, despite it being the
  only other ready P2 item after closing einstein-15. Two concrete reasons,
  not just "ran out of steam":
  1. Its description ("re-queries Semantic Scholar + patent claims to
     DISPROVE novelty") requires either a brand-new external API client
     (Semantic Scholar has no fetcher in this repo — confirmed by
     `grep -n semanticscholar einstein/openalex_fetcher.py`, no match; only
     OpenAlex DOI lookup and citation edges exist) or a deliberate design
     decision to repurpose `einstein/openalex_fetcher.py`'s existing OpenAlex
     search surface instead of adding a new dependency. That's a real
     product/architecture call I'm not positioned to make silently.
  2. "Reject or force-pivot above similarity threshold theta" does not
     specify what distinguishes "reject" from "force-pivot" — a single
     threshold can't produce two different actions without a second,
     unstated threshold or rule. Guessing at that semantics and shipping it
     as this bead's acceptance behavior risks building something that looks
     done but encodes an invented policy nobody asked for — worse than
     leaving it open with the ambiguity named.
  I did not touch `einstein/patent_claims.py` (einstein-8, already closed
  and would be einstein-16's other real input) beyond reading it to confirm
  it's ready to be consumed (`independent_claims_for_record` is exactly the
  "widest-scope claim text" einstein-16 needs).
- **Did not wire `einstein/ideator.py`'s `build_ideator_agent_node` as
  `build_graph`'s new default `agent_node`.** It stays available as an
  explicit opt-in factory; `build_graph`'s default is still the LLM-free
  stub. Swapping the default requires an actual `LLMClient` implementation
  (a real SDK), which is not a project dependency and wasn't added — same
  "keep dependencies minimal" reasoning as previous sessions' handoffs.
- **Did not work einstein-0.3** (the integration bead filed this session)
  or einstein-9 (constraint & friction mining) — both ready, both smaller
  than einstein-16, but stopping after one clean, fully-tested unit of
  new agentic-loop work was the more defensible choice than opening a
  second substantial front in the same session, given the epic's
  remaining size.

## What I could not verify

- **`.github/workflows/nightly.yml`**: never run against a real GitHub
  Actions runner. Verified only by manual review of the YAML and the fact
  that `uv run einstein ...` (the command it invokes) works locally.
  **Unverified, not "tested."**
- **`scripts/nightly_run.sh`**: syntax-checked (`bash -n`) only, never
  executed — running it would make live arXiv/GitHub/USPTO API calls and
  burn real quota, which the task's testing rules forbid doing inside this
  sandbox's automated verification path. **Unverified beyond syntax.**
- **The git commit history anomaly below** — I can observe it but cannot
  determine its origin from inside this session.

## Git status — flagging an anomaly before the usual report

`git log --oneline -5` shows three commits already on `main`
(`0c1f2ed`, `c96ffa0`, `f3377267`, all authored `B0001
<hess.bn@gmail.com>`, timestamped 2026-08-11 22:36–22:42 -0400) that
collectively check in the bulk of this session's *prior* work — 36 files
including `einstein/cross_pollination.py`, `einstein/store.py`,
`einstein/velocity.py`, `einstein/patent_claims.py`,
`einstein/uspto_fetcher.py`, `einstein/cli.py`, `.github/workflows/nightly.yml`,
`scripts/`, and most of `tests/fixtures/`. **This session's task
instructions explicitly say not to `git commit`.** I did not make these
commits in my current context, and cannot verify from inside this session
whether they were made by an earlier part of this same session (before the
context-compaction summary), by `bd` tooling's own auto-commit behavior
(the adjacent `c96ffa0 bd: update sync.remote` commit looks like normal
`bd` bookkeeping, but `f3377267`'s 36-file, 11312-line diff does not), or
by some other actor. I have **not** attempted any corrective git action
(no reset, no revert) — that would be a second, riskier unrequested action
on top of an already-uncertain situation. Flagging for human review.

Given that anomaly, here is the current, actual state:

```
$ git status --short
 M .beads/interactions.jsonl
 M .beads/issues.jsonl
 M einstein/graph.py
?? .claude/settings.local.json
?? einstein/ideator.py
?? tests/test_ideator.py
```

- `.beads/interactions.jsonl`, `.beads/issues.jsonl`: bd's own passive
  export, changed by this session's `bd update --claim` / `bd close`
  calls (einstein-15, einstein-7, einstein-0.1, einstein-13, einstein-21,
  plus filing einstein-0.3). Not hand-edited.
- `einstein/graph.py`: the `ideas: list` field added to `AgentState`/
  `initial_state` for einstein-15 (diff shown in the "What changed"
  section above).
- `.claude/settings.local.json`: sandbox-local tool permissions, not
  part of this bead's work.
- `einstein/ideator.py`, `tests/test_ideator.py`: new files, this
  session's einstein-15 work.

Per the conservative git policy, none of this was committed. Suggested
commands for a human to run (not executed):

```bash
git add einstein/ideator.py einstein/graph.py tests/test_ideator.py .beads/issues.jsonl .beads/interactions.jsonl
git commit -m "einstein-15: ideator agent node (Method/Problem extraction from Gap)"
# review the three pre-existing commits (0c1f2ed, c96ffa0, f3377267) before any push --
# their presence on `main` despite this session's no-commit instruction is unexplained,
# see "Git status" section above.
```

## Next steps for whoever picks this up

- **einstein-16** (novelty auditor) is the highest-priority ready item.
  Before implementing: decide whether to add a Semantic Scholar client or
  extend `einstein/openalex_fetcher.py` with a text-search function (no
  new external dependency), and define what separates "reject" from
  "force-pivot" beyond a single theta.
- **einstein-9** (constraint & friction mining) is ready, smaller,
  self-contained (depends only on closed einstein-4/einstein-7).
- **einstein-0.3** (integration bead filed this session) is explicitly
  out of scope until a human or orchestrator prioritizes it.
- einstein-0 itself stays open until einstein-16/17/18/19/20 (the rest of
  the agentic loop) close.
