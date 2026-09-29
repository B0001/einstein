# einstein-uts handoff

Decide: does `novelty_auditor` need additional down-weighting of USPTO
patent-side 'pass' verdicts, even after the einstein-b3b `sort=_score` fix?

**This bead is labeled `human` and asks a product/risk-tolerance question
("not something the numbers alone resolve") explicitly.** I did not answer
it. Answering it myself would be exactly the failure mode the `human` label
and the `bd human` subsystem exist to prevent: an agent quietly resolving a
question that was deliberately routed around agent judgment. What I did
instead is close the *measurement* gap the bead names as unresolved (its
point #2), so whoever does answer it has real numbers instead of none.

Left `in_progress`, not closed, not answered via `bd human respond`.

## What I changed

**New: `scripts/uspto_relevance_benchmark.py`** — a hand-run live benchmark,
broader than einstein-b3b's handful of hand-picked queries. Method (also in
the script's own docstring):

1. For each of 20 `TOPIC` strings, find one real patent via a **quoted**
   exact-phrase query against the live USPTO API — a different query mode
   from the unquoted OR-of-terms mode under test (confirmed by b3b: quoting
   collapses a ~900k-hit unquoted count to a few hundred, all on-topic), used
   here only to establish "a real, on-topic patent with this ID exists."
   This is the best ground-truth oracle available without hand-recalling
   patent numbers from memory, which this repo's standard treats as guessing
   — but it is *not* fully independent of the search engine under test; a
   ground truth sourced from e.g. news coverage of real inventions,
   cross-checked by number, would be stronger. Not built here — flagged, not
   hidden.
2. Build a deterministic paraphrase of that patent's own title (different
   surface words, not a copy) as a stand-in `idea.method`-shaped paragraph.
3. Run the paraphrase through the real `fetch_patents` — the exact function
   `novelty_auditor.audit_idea` defaults to — at `max_results=10` (what
   `audit_idea` actually gets: `search_patents(idea.method)` is called with
   no override) and again at `max_results=25` for comparison.
4. `recall@k` = fraction of topics whose ground-truth patent ID appears in
   its own paraphrase's top-k.

A second set of 2 generic "distractor" paragraphs (no expected specific
conflict) is run through the same path as an unscored qualitative
precision spot-check.

**`einstein/uspto_fetcher.py`** — updated the module docstring's "Search
relevance" section. It previously said precision/recall across a broad
sample was "unmeasured" and pointed only at this bead as an open question.
That claim is now false — I measured it — so per this repo's standard ("a
number is only allowed to exist in a document if the code produces it, or
the document says where it came from") I replaced it with the numbers below,
their caveats, and a pointer back to this bead and this handoff for the full
methodology. No other change to this file; `sort=_score desc` behavior is
untouched.

## The numbers (justifying command below)

```
USPTO_API_KEY=... uv run python scripts/uspto_relevance_benchmark.py
```

Measured live 2026-09-29. Of 20 topics, 10 had a verbatim ground-truth
phrase hit in the live index today (the other 10 printed "no ground-truth
patent found via quoted search today -- skipped" — the topic phrases I
picked don't occur verbatim in any patent title; this lowers the effective
sample size, it does not mean anything about search quality). Scored on
those 10:

```
recall@10: 9/10 = 90%
recall@25: 10/10 = 100%
```

`recall@10` is the number that matters for the live pipeline:
`novelty_auditor.audit_idea` calls `search_patents(idea.method)` with no
`max_results` override, i.e. `fetch_patents`'s default of 10, not 25.

The one recall@10 miss, concretely: ground truth `D809041` "SURGICAL ROBOT
END EFFECTOR" — a paraphrase of its own exact title still ranked outside the
top 10 of `sort=_score desc` results, appearing only once `max_results` was
raised to 25 (the script only logs top-10 titles, so its exact rank between
11 and 25 wasn't captured — rank-11-to-25 vs. rank-26+ is the one thing this
run didn't distinguish). This is not a
hypothetical failure mode from the bead description — it happened, live,
this session, in the easiest case in the sample (a paraphrase of the
patent's own title, i.e. maximal vocabulary overlap).

**Important caveat, stated in the script and the updated docstring**: because
each paraphrase is built from its own ground-truth patent's title, it shares
more vocabulary with that patent than an independently-generated
`idea.method` paragraph would with whatever real patent might conflict with
it. 90% is an upper bound on real recall@10 for this API path, not a
representative estimate of what `novelty_auditor` sees in practice. Pinning
down the real number would need either real `idea.method` outputs from the
ideator run against a corpus of independently-known conflicts, or a
ground-truth set built without title-paraphrasing — neither built here.

**Distractor spot-check** (2 generic unrelated paragraphs, not scored): top-10
results were plausible-sounding patents sharing a keyword or two, not
genuinely on-topic matches — consistent with b3b's "count/precision
unvalidated for queries with no real target" concern. Lower severity than it
sounds, though: `novelty_auditor`'s reject/force_pivot decision is gated on
claims-text embedding similarity (theta=0.85), not title, so an irrelevant
top-10 title alone doesn't produce a false reject — the cost here is more
likely wasted lazy grant-text-enrichment fetches (einstein-9it) against
USPTO's ~20-downloads/year/file quota on candidates that were never going to
score above theta anyway. Not measured end-to-end this session — would need
the full `audit_idea` claims-comparison path exercised against these
candidates to confirm, not just the search step.

## Final test-run line

```
uv run python -m unittest discover tests
```
→ `Ran 454 tests in 1.551s` / `OK`

Unchanged from before this session (454 — this session added no new unit
tests, only a hand-run script excluded from `discover tests` by convention,
and a docstring edit). The USPTO-key-missing / 429 / 500 / 404 lines printed
during the run are expected output from tests deliberately exercising those
error paths, not failures — same as every prior USPTO-related handoff in
this repo notes.

## Where I was tempted to write "novel" / "no prior art" and what I wrote instead

Describing the recall@10 miss, I was tempted to write something like "the
sort fix doesn't reliably find conflicting patents." I did not write that —
one miss in a sample of 10, using an easier-than-real paraphrase strategy,
supports a narrower claim: "a near-exact title match can still rank outside
top-10 of a sort=_score result set," not a general reliability verdict.
Describing the overall numbers, I was tempted to write "90% recall is
probably good enough." I did not write that either — that sentence is
exactly the risk-tolerance call this bead reserves for a human, and I have
no basis to assert what tolerance is acceptable for this product. I wrote
the numbers, the methodology, the caveat that they're an upper bound, and a
recommendation labeled explicitly as non-binding instead.

## What I decided not to do, and why

- **Did not implement a lower-confidence signal on `Audit` for patent-side
  "pass" verdicts.** This is the literal fork the bead's own question poses
  ("is the ranking fix sufficient, or does Audit need a lower-confidence
  signal"). Even though it would be a small, purely additive, non-behavior-
  changing diff (a note string, not a change to reject/force_pivot/pass
  logic), shipping it unilaterally on a bead explicitly labeled `human`
  would be answering the question instead of informing the answer. Recorded
  as a recommendation in `bd update --notes` and here, not as code.
- **Did not close this bead or run `bd human respond`.** Both would end this
  bead's life as a visible human-decision item. Per the task's own
  instruction ("If you cannot finish it, leave it open... stop") and the
  bead's own `human` label, left `in_progress` with these notes attached.
- **Did not build a fully independent ground-truth set** (e.g. from
  real-world news coverage of specific inventions, cross-checked against
  USPTO by number) — flagged above as the stronger version of this
  benchmark that wasn't attempted this session, for scope/time reasons, not
  because it wouldn't help.
- **Did not exercise the full `audit_idea` claims-comparison path** against
  the distractor queries to measure end-to-end false-reject risk or
  grant-text-quota waste — noted as unmeasured above, would need its own
  scoped check (and burns real grant-text-fetch quota per candidate, so
  should be deliberate, not incidental to this benchmark).
- **Did not re-run/expand the benchmark further** once a representative,
  clean 10-topic sample was captured (after fixing a scoring bug — see
  below) — diminishing returns against API load for a hand-run,
  point-in-time script; a human re-running this later will see different
  numbers as the index drifts, by design.

## A bug I found and fixed in my own measurement, not in production code

First run of the script had `scored += 1` execute before the two live
`fetch_patents` calls it was about to attempt. A transient timeout or
`IncompleteRead` on either call then hit `continue` *after* `scored` had
already been incremented, without ever computing a hit/miss — silently
counting a network hiccup as a "scored miss" and deflating recall. Caught
because a full run crashed on an uncaught `ReadTimeoutError` (added broader
`except` handling for that too, since raw `requests`/`urllib3` errors aren't
`USPTOFetchError`), re-read the summary math, and noticed `scored` could
exceed `hits` for a reason unrelated to search quality. Fixed by moving
`scored += 1` to after both fetches succeed (see the script). Mentioning
this because it's exactly the kind of "your reasoning is not evidence"
mistake this repo's standard warns about — a passing script that silently
produces a wrong number is worse than a crashing one.

## What I could not verify

- Whether the true (non-upper-bound) recall@10 for organically-generated
  `idea.method` text is anywhere near 90% — flagged repeatedly above as
  unmeasured; the paraphrase-of-title methodology structurally can't answer
  this.
- Whether an independently-sourced ground truth (not derived from a
  different query mode of the same search engine) would change these
  numbers materially.
- End-to-end false-reject or quota-waste risk from irrelevant top-10
  candidates reaching claims comparison — the distractor spot-check only
  looked at titles, not the full audit path.
- Day-to-day stability of these numbers — USPTO's live index changes; this
  is a 2026-09-29 snapshot, reproducible but not guaranteed to reproduce
  identically on a later date.

## Git status (left dirty, per repo policy — no commit/push/dolt push run)

```
$ git status --short
 M .beads/interactions.jsonl
 M .beads/issues.jsonl
 M einstein/novelty_auditor.py
 M einstein/patent_claims.py
 M einstein/uspto_fetcher.py
 M tests/fixtures/uspto_patent_search.json
 M tests/test_novelty_auditor.py
 M tests/test_uspto_fetcher.py
?? einstein/uspto_grant_text.py
?? sandbox-handoffs/einstein-9it.md
?? sandbox-handoffs/einstein-b3b.md
?? sandbox-handoffs/einstein-tix.md
?? sandbox-handoffs/einstein-uts.md
?? scripts/uspto_relevance_benchmark.py
?? scripts/uspto_search_recall_check.py
?? tests/fixtures/uspto_grant_text.xml
?? tests/fixtures/uspto_grant_text_no_abstract.xml
?? tests/test_uspto_grant_text.py
```

Everything except `einstein/uspto_fetcher.py` (docstring section only),
`scripts/uspto_relevance_benchmark.py` (new), and this handoff predates this
session (einstein-tix / einstein-b3b / einstein-9it work, still uncommitted
in this tree, described in their own handoffs).

Suggested commands for a human to review and run (not executed):

```
git add einstein/uspto_fetcher.py scripts/uspto_relevance_benchmark.py sandbox-handoffs/einstein-uts.md
git commit -m "einstein-uts: benchmark USPTO search recall@10, document numbers, leave decision open"
```

(This is a separate, smaller commit from the einstein-tix/b3b/9it work
already sitting in the tree — a human may prefer to squash them together
instead; not my call to make.)

## Bead resolution

Not closed. `bd show einstein-uts` still shows `in_progress`, still appears
in `bd human list`. The notes attached via `bd update einstein-uts --notes`
summarize this handoff for anyone triaging via `bd` directly. A human
answering the actual question should read this handoff, optionally re-run
`scripts/uspto_relevance_benchmark.py` for a fresh snapshot, and then either
`bd human respond einstein-uts` with the decision or implement the
lower-confidence-signal change in `einstein/novelty_auditor.py` themselves
(the recommendation above sketches where it would go: `_note()`'s "pass"
branch, or a new field on `Audit`) and close from there.

---

## Second session (2026-09-29, same day, separate worker)

Picked this bead back up because it was left `in_progress` with no decision
recorded. Per this repo's standard ("do not assume its partial work is
correct"), independently re-verified every claim above rather than trusting
the notes:

- `uv run python -m unittest discover tests` -> `Ran 454 tests in 0.563s` /
  `OK`. Matches the claimed count.
- `scripts/uspto_relevance_benchmark.py` exists and its docstring matches the
  methodology described above verbatim.
- `einstein/uspto_fetcher.py`'s "Search relevance" docstring section is
  present, cites recall@10=90%/recall@25=100%, and states the same caveats.
- `bd human list` still shows `einstein-uts` flagged.
- `git diff einstein/novelty_auditor.py` and `patent_claims.py` confirmed
  those changes belong to the already-closed einstein-9it work (grant-text
  enrichment), not this bead -- no confidence-signal code exists anywhere in
  the tree, confirming the first session's claim that it recommended but did
  not implement one.

**No code changes made this session.** Did not attempt the actual
risk-tolerance decision, for the same reason the first session declined to:
it is explicitly reserved for a human by this bead's `human` label, and "the
numbers alone" -- which now exist and check out -- are stated in the bead's
own text as insufficient to resolve it. Considered doing the two further
measurements the first session flagged as not-done (an independently-sourced
ground-truth set; exercising the full `audit_idea` claims-comparison path
against distractor queries end-to-end) -- decided against spending on either
this session, because neither would resolve the actual blocker (a decision,
not a measurement gap) and both cost scarce, non-renewable resources: live
USPTO grant-text fetches are rate-limited to roughly 20 downloads/year per
specific file (see `uspto_grant_text.py`'s docstring), so spending that quota
exploratively on a bead whose real blocker is a human judgment call, rather
than on a live pipeline run that needs it, would not be responsible use of a
resource that can't be gotten back this year.

Test suite: unchanged, still 454/454, still OK -- rerun above.

Left `in_progress`, still not closed, still not responded to via `bd human
respond`. Nothing in this session's re-check found the first session's work
to be wrong, incomplete, or overstated.
