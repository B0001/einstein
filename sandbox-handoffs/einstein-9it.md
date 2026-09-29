# einstein-9it handoff

## Status

The tree, as handed to me, already contained a complete implementation of
this bead. I verified it rather than writing it from scratch — no code
changes made this session. `bd show einstein-9it` had no notes recorded by
whoever did the work, so this handoff documents what's there and the
verification I ran.

## What's in the tree (all pre-existing, unmodified by me)

**Decision made (acceptance criterion 1):** lazy enrichment inside
`einstein/novelty_auditor.py`, not eager in `uspto_fetcher.fetch_patents`,
and not touched in `graph.py`. Recorded in `novelty_auditor.py`'s module
docstring under "Grant-text enrichment (einstein-9it)" (lines ~80-99) and in
`_enrich_with_grant_text`'s docstring (line ~225).

Rationale as written: `graph.py`'s `ingest` node only ever reads
title/summary off a patent Record for gap detection — it never calls
`independent_claims_for_record`, so eagerly enriching every search result
there would spend the ~20/year/file grant-text quota on patents nothing
downstream needs. `novelty_auditor._candidate_claims` is the only caller
that ever needs claim text, and only for patents that actually reach claim
comparison, so that's where the fetch is triggered — one attempt, only on a
`ClaimsNotFoundError`, only if a `grant_text_fetcher` was supplied (it
defaults to the real `uspto_grant_text.fetch_grant_text`, confirmed by
`test_default_grant_text_fetcher_is_the_real_uspto_module_function` checking
parameter-default identity, not by exercising it).

Confirmed `graph.py` itself needed no change:
`grep -n "fetch_patents\|uspto" einstein/graph.py` shows it still calls
`fetch_patents` directly for `ingest`, and nothing in that file calls
`novelty_auditor.audit`/`audit_idea`/`build_novelty_auditor_agent_node` — the
agentic-loop wiring into `build_graph` is a separate, larger concern this
bead doesn't claim to close (confirmed via
`grep -rn "build_novelty_auditor_agent_node" --include=*.py .`, only hits in
`novelty_auditor.py` itself and docstring mentions elsewhere).

**Test proving live-shaped enrichment (acceptance criterion 2):**
`tests/test_novelty_auditor.py::GrantTextEnrichmentTest`, five tests:

- `test_live_shaped_patent_is_enriched_and_its_claim_text_is_compared` — runs
  a `LIVE_SHAPED_PATENT` Record (shaped exactly like real `fetch_patents`
  output: `applicationMetaData` + `grantDocumentMetaData.fileLocationURI`,
  no `claimsText` anywhere) through `audit_idea` with an injected
  `grant_text_fetcher`. Asserts the *outcome* depends on the enriched claim
  text (`verdict == "reject"`, specific `claim_number`) rather than just
  peeking at `raw` — stronger evidence that `independent_claims_for_record`
  actually consumed what enrichment wrote into `raw["claimsText"]` via
  `record_with_grant_text` (confirmed that function sets that key at
  `einstein/uspto_grant_text.py:167-168`).
- `test_grant_text_fetcher_is_not_called_for_a_patent_that_already_has_claims_text`
  — quota consciousness: a fetcher that calls `self.fail()` if invoked,
  proving no wasted fetch for a patent that already has claim text.
- `test_grant_text_fetch_failure_is_warned_not_silently_treated_as_no_conflict`
  — a `GrantTextError` from the fetcher becomes `verdict == "unsearched"` with
  a warning naming the patent, not a false "pass"/"no conflict".
- `test_grant_text_fetcher_none_disables_enrichment` — explicit opt-out
  preserves the pre-einstein-9it behavior.
- `test_default_grant_text_fetcher_is_the_real_uspto_module_function` — checks
  `audit_idea`/`audit`/`build_novelty_auditor_agent_node` all default
  `grant_text_fetcher` to the actual `uspto_grant_text.fetch_grant_text`
  object (identity check via `inspect.signature(...).parameters[...].default`),
  so the live pipeline path is provably wired, not just wireable.

**Quota noted (acceptance criterion 3):** `novelty_auditor.py`'s module
docstring states "a key rate-limited to ~20 downloads/year *per specific
file*" and cites `uspto_grant_text`'s own docstring; that figure traces back
to `sandbox-handoffs/einstein-tix.md` (lines 144, 201), the prior bead that
confirmed it live. `uspto_grant_text.py`'s own docstring additionally
explains *why* enrichment is a separate opt-in step and not folded into
`fetch_patents`: one extra network round-trip (two counting the redirect)
against the same quota'd key, for data most bare-search callers don't need.

## Verification I ran this session

```
uv run python -m unittest discover tests
```
→ `Ran 453 tests in 0.666s` / `OK`. (Logged lines about injected 429/500/403
errors and one "grant text fetch failed: 404" are from error-path tests
deliberately exercising failure handling, not real failures.)

```
uv run python -m unittest tests.test_novelty_auditor -v
```
→ `Ran 34 tests in 0.039s` / `OK`, including all 5
`GrantTextEnrichmentTest` cases listed above.

```
grep -n "fetch_patents\|uspto" einstein/graph.py
grep -rn "build_novelty_auditor_agent_node" --include=*.py /workspace
grep -n "claimsText" einstein/uspto_grant_text.py
```
→ confirmed the scope claims above (graph.py untouched, agent node not yet
wired into `build_graph`, `record_with_grant_text` sets `raw["claimsText"]`).

## What I did not do

- No code changes — the acceptance criteria were already met by whatever
  produced this working-tree state before I claimed the bead. I did not
  find any notes on the bead recording who/when; `bd show einstein-9it
  --json` returned no `notes` field.
- Did not touch `graph.py` to wire `build_novelty_auditor_agent_node` into
  `build_graph` — out of scope per the documented decision (that node isn't
  called from `build_graph` at all yet; making the agentic loop actually run
  end-to-end is a separate, larger piece of work than "populate claims text
  when novelty_auditor reaches claim comparison").
- Did not re-verify the live USPTO grant-text endpoint myself (no network
  access from this session per repo policy); relying on `einstein-tix`'s
  prior live confirmation, which this bead's own tests exercise only against
  fixtures/stubs, consistent with the "tests must not touch the network"
  rule.

## Where I was tempted to write "novel" / "no prior art" and what I wrote instead

Nowhere in this session — I made no pipeline-output changes. The existing
code's own language stays consistent with the standard: `novelty_auditor.py`
warnings read "grant-text enrichment skipped: {reason}", not "no
conflicting claims"; a fetch failure yields `verdict == "unsearched"`, never
a fabricated "pass".

## Git status

Working tree is dirty exactly as it was received (all from the prior
einstein-tix session, per `git status`): modified
`.beads/interactions.jsonl`, `.beads/issues.jsonl`,
`einstein/novelty_auditor.py`, `einstein/patent_claims.py`,
`einstein/uspto_fetcher.py`, `tests/fixtures/uspto_patent_search.json`,
`tests/test_novelty_auditor.py`, `tests/test_uspto_fetcher.py`; untracked
`einstein/uspto_grant_text.py`, `sandbox-handoffs/einstein-tix.md`,
`scripts/uspto_search_recall_check.py`,
`tests/fixtures/uspto_grant_text.xml`,
`tests/fixtures/uspto_grant_text_no_abstract.xml`,
`tests/test_uspto_grant_text.py`, plus this file. Per repo git policy, I did
not commit or push anything. Suggested commands for a human to run:

```
git add einstein/ tests/ scripts/ sandbox-handoffs/ .beads/
git commit -m "einstein-9it: wire uspto_grant_text lazily into novelty_auditor"
```

(Note: this bundles einstein-tix's uncommitted work too, since the two were
never split into separate commits in this tree — a human reviewing the diff
may want to split `uspto_grant_text.py`/`uspto_fetcher.py` fixes from the
`novelty_auditor.py` wiring into two commits instead of one.)

## Bead resolution

Closing `einstein-9it` as done: all three acceptance criteria are met by
code already in the tree, verified by the test run above.
