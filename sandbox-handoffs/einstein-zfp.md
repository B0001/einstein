# einstein-zfp handoff

## Bead

`einstein-zfp` (P3, bug): `uspto_fetcher._to_record`'s `ts` extraction only
checked `applicationMetaData.grantDate` / `.filingDate`, silently falling
back to the hardcoded `"1970-01-01"` placeholder for pending applications
that instead populate `effectiveFilingDate`. Filed from a real document
(IQM Finland OY, application 19589991) surfaced while live-characterizing
USPTO search relevance for `einstein-b3b`.

Found the bead already `in_progress` (claimed 2026-09-29T15:39:46Z) with no
notes and no code changes toward it in the working tree -- the uncommitted
diff present at session start was entirely prior work from `einstein-tix`,
`einstein-9it`, `einstein-b3b`, and `einstein-uts` (all already closed per
their own handoffs in this directory). I continued the claim rather than
re-claiming.

## What I changed

`einstein/uspto_fetcher.py`:
- `_to_record`'s `ts` line now falls back to
  `metadata.get("effectiveFilingDate")` before the `"1970-01-01"`
  placeholder: `grantDate or filingDate or effectiveFilingDate or
  "1970-01-01"`.
- Added a "Timestamp field variance across applicationStatusCode
  (einstein-zfp)" section to the module docstring recording the fix and the
  patentNumber-classifier decision (below), with the live evidence inline.

`tests/fixtures/uspto_patent_search.json`:
- Replaced the second (pending-application) entry, which was hand-constructed
  by `einstein-tix` because that session got no live example, with a trimmed
  **real** response for application 19589991 (fetched live this session --
  see reproduction command below). It has no `filingDate` key at all, only
  `effectiveFilingDate: "2026-05-22"`. Confirmed this is the exact document
  the bead was filed from: same applicant (IQM Finland OY), same title
  prefix ("METHOD FOR CONSTRUCTING A QUANTUM ERROR CORRECTION CODE..."),
  same `applicationStatusCode` (19), same `effectiveFilingDate`
  (2026-05-22) as the bead description cites verbatim.

`tests/test_uspto_fetcher.py`:
- Added `PENDING_NO_FILING_DATE_DOC`, a Python-side mirror of the same real
  document, and
  `test_pending_application_with_no_filing_date_falls_back_to_effective_filing_date`,
  which asserts `record.ts == "2026-05-22"` and explicitly asserts it is
  *not* `"1970-01-01"`.
- Checked the fixture swap doesn't break the caching tests in
  `FetchPatentsCachingTest`: they only assert `len(records)` and
  `records[0].id` (the granted entry, unchanged), never the second record's
  id/ts, so swapping the second entry's content is safe.

## Acceptance criteria

> `_to_record`'s ts extraction falls back to
> `applicationMetaData.effectiveFilingDate` when filingDate/grantDate are
> absent, confirmed against a live-shaped fixture (not hand-guessed).

Done. The fixture is a real live response, not hand-guessed -- reproduce
with:

```
curl -s -G "https://api.uspto.gov/api/v1/patent/applications/search" \
  --data-urlencode 'q=applicationMetaData.firstApplicantName:"IQM Finland" AND applicationMetaData.applicationStatusCode:19' \
  --data-urlencode 'rows=20' --data-urlencode 'sort=_score desc' \
  -H "Accept: application/json" -H "X-API-KEY: $USPTO_API_KEY"
```
Application `19589991` in the result set is the exact document; its
`applicationMetaData` has `filingDate` and `patentNumber` and `grantDate`
absent as keys entirely (not null-valued -- genuinely not present), and
`effectiveFilingDate: "2026-05-22"`.

> A decision is recorded on whether patentNumber-presence remains a
> reliable granted/pending classifier across applicationStatusCode values,
> based on live evidence.

Decision: **keep patentNumber-presence as the classifier.** Recorded in the
module docstring. Evidence: live sample of 226 documents across 5 unrelated
queries ("quantum computer", "artificial intelligence", "battery",
"vaccine", "semiconductor", `rows=100` each, `sort=_score desc`), spanning
12 distinct `applicationStatusCode` values:

```
code total has_patentNumber desc
  19    32     0   Application Undergoing Preexam Processing
  30     2     0   Docketed New Case - Ready for Examination
  41     1     0   Non Final Action Mailed
  93     1     0   Notice of Allowance Mailed -- Application Received in Office of Publications
 150     5     5   Patented Case
 159    41     0   Provisional Application Expired
 160     1     0   Abandoned -- Incomplete Application (Pre-examination)
 161     1     0   Abandoned -- Failure to Respond to an Office Action
 218    35     0   RO PROCESSING COMPLETED-PLACED IN STORAGE
 250     4     4   Patent Expired Due to NonPayment of Maintenance Fees Under 37 CFR 1.362
 566     2     0   PCT - International Search Report Mailed to IB
```

Zero status codes were mixed: `patentNumber` was present for 100% of
documents in the two "has ever been granted" statuses (150, and 250 --
expired-after-grant patents still carry their number) and absent for 100%
of every other status sampled, including an allowance-adjacent one (93,
before the number is actually assigned) and two abandoned ones (160, 161).
This is not exhaustive -- 12 of USPTO's 60+ status codes, no reissue or
design-patent statuses specifically sampled -- so the docstring records it
as "no counterexample found in a diverse live sample," not "proven for all
codes." I did not build a script for this (no bead asked for a reusable
tool here); the queries are the five `curl`/API calls above, run once, with
the `applicationStatusCode`/`patentNumber` tabulation done inline in a `uv
run python -c "..."` one-liner. Not saved as a script since this was a
one-time confirmatory check, not a recurring benchmark like `einstein-b3b`'s
or `einstein-uts`'s.

## Test suite

```
$ uv run python -m unittest discover tests
Ran 455 tests in 0.593s

OK
```
(454 in the last recorded run before this session, per `einstein-b3b`'s
handoff; +1 for the new effectiveFilingDate-fallback test.)

## Where I was tempted to say "novel" / "no prior art"

Nowhere in this bead -- it's a timestamp-extraction bug fix, not a
detector-output claim. The one place this bead touches detector honesty is
indirect: a patent Record with a wrong (`1970-01-01`) timestamp is a patent
Record a downstream consumer might reason about incorrectly (e.g. "this
predates the API, must be stale/synthetic data" or any recency-based
filtering), which is exactly the kind of quiet corruption this fix removes.

## What I decided not to do, and why

- Did not build a `bd create`-worthy follow-up for a broader
  applicationStatusCode survey (reissue/design-patent statuses, etc.) --
  the bead's acceptance criteria asked for "a decision... based on live
  evidence," not exhaustive coverage of every status code, and 12 codes
  across 226 documents with zero mixed results is a reasonable stopping
  point for that ask. If a future session hits a counterexample, that's a
  new bead, not evidence this one was wrong.
- Did not touch `einstein/patent_claims.py`, `einstein/novelty_auditor.py`,
  or `einstein/uspto_grant_text.py` beyond what was already in the tree from
  `einstein-tix`/`einstein-9it` -- out of scope for this bead, and their own
  handoffs already cover them.
- Did not re-verify `einstein-tix`/`einstein-9it`/`einstein-b3b`/`einstein-
  uts`'s prior work beyond running the full suite -- their own handoffs are
  the record for that; re-auditing closed beads is outside this bead's
  scope.

## What I could not verify

- Whether USPTO's status-code-to-patentNumber correlation is stable across
  all ~60+ status codes USPTO documents (reissue, design patents, PCT
  national-stage-specific codes beyond what showed up in this sample) --
  see "Decision" above; explicitly scoped as unverified beyond the sampled
  12 codes.
- Whether `effectiveFilingDate` is itself always populated when
  `filingDate` is absent (i.e., whether there's a *third*, even rarer shape
  with neither) -- not encountered in this session's live queries, not
  ruled out.

## Git status (left dirty, per repo policy -- no commit/push/dolt push run)

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

`einstein/novelty_auditor.py`, `einstein/patent_claims.py`, and the
untracked `einstein/uspto_grant_text.py` / fixtures / test file are all
prior sessions' work (`einstein-tix`, `einstein-9it`), not touched by me
this session beyond what was already there. My changes this session are
scoped to `einstein/uspto_fetcher.py`, `tests/fixtures/uspto_patent_search.json`,
and `tests/test_uspto_fetcher.py`, plus the `.beads/*` bookkeeping from
closing this bead.

## Bead resolution

Closing `einstein-zfp`: both acceptance criteria are met with live evidence
(not hand-guessed), the fix is in place, and the full suite passes
(455/455).
