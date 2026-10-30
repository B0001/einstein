# VOID: einstein passes dated before 2026-10-15

Written 2026-10-23 for portfolio bead `portfolio-yg7`. It applies the portfolio profit plan
(2026-10-17): §4 says "Mark every einstein pass dated before 2026-10-15 as VOID", and §5 C6
retires einstein: "Never hand einstein output to counsel as clearance."

## What counts as a pass

1. **A `novelty_auditor` verdict `"pass"`** (`einstein_agents/novelty_auditor.py`, `NoveltyVerdict`).
   It means no paper or patent scored at or above theta. Feasibility, codegen and report run
   only on `"pass"`. Before 2026-10-15, `REPORT_NOTE` told readers that each proposal
   "survived an adversarial novelty audit".
2. **A `true_invention_gap` label from the gap detector** (`einstein/gaps.py`). The code does
   not call it a pass, but it makes the same claim: no matching repo and no matching patent,
   so the idea is new. It fails open in the same way, so it is voided too.
3. **Not passes:** the triage labels `read`, `search_empty` and `search_failed`. They are
   evidence packs, not verdicts (`ca42bbf`). They are listed below only because they are
   einstein output.

## Why the cutoff is 2026-10-15

| Date (US Eastern) | Commit | What was wrong before it |
|---|---|---|
| 2026-09-24 20:38 | `bb7a29b` (PR #1, merged as `50fb37a`) | The OpenAlex filter ANDed every term, so a method paragraph returned 0 papers, and an empty search was a `"pass"` (einstein-av1). It is now `"unsearched"`. |
| 2026-09-24 20:52 | `eabd6a3` (same PR) | The patent side could pass with no claims compared. It is now `"unsearched"`. |
| 2026-09-29 18:47 | `2725699` | The USPTO search URL and response shape were wrong, and results were unranked (einstein-tix, b3b). The rule that one enriched patent is enough for a pass is still unvalidated (einstein-uts, einstein-w77). |
| **2026-10-15 21:57** | **`c5a2d03`** (einstein-wvc) | **theta was 0.85, and at 0.85 the auditor caught 0% of ideas built from a paper's own text. Every earlier pass is vacuous, even when the search returned papers. This commit is the cutoff.** |
| 2026-10-15 22:07 | `323b6b9` (einstein-eg9) | A paper in a run with no patent evidence was labelled `true_invention_gap`. It is now `unbuilt_no_patent_evidence`. |

Passes made after the cutoff are not validated either. The patent side reuses theta 0.25
without calibration (einstein-yy0), and the paper side was calibrated only on near-verbatim
restatements.

## Where passes were recorded or cited

| Date | What | Where | Status |
|---|---|---|---|
| 2026-09-24 10:37–12:11 UTC | 2 `true_invention_gap` rows: `paper:p1` "SGD convergence bounds" and `paper:p2` "Tensor network attention". Both have patent `best_similarity` 0.0 and `best_id` null, so there was no patent evidence. | `einstein.db`, table `gaps` (local file, gitignored). This is test-fixture data that MainTest wrote into the working directory (fixed in `81421ed`). The third row in the table is `unformalized_code`, which is not a pass. Tables `records` and `queries` are empty. | VOID |
| 2026-08-12 to 2026-10-15 | Nightly gap reports in the **job logs**: every version of `nightly.yml` ends the run step with `cat report.json`, so each run's full report is in its log. 66 runs started before 2026-10-17, the oldest at 2026-08-12T05:04Z; 55 succeeded. GitHub keeps logs about 90 days, so the oldest last until about 2026-11-10. | GitHub Actions job logs of `.github/workflows/nightly.yml` (public repo) | VOID: every `true_invention_gap` in them |
| 2026-09-24 to 2026-10-14 | The same reports as artifacts: the 21 `gap-report-<run id>` artifacts still unexpired on 2026-10-23 (30-day retention); earlier artifacts have expired, but their reports are still in the job logs above. A 2026-10-23 read of the job logs found 15 artifacts holding 28–30 `true_invention_gap` rows each (431 in all) and 6 empty uploads from failed runs (not re-counted here). When the `USPTO_API_KEY` secret is absent, nightly runs with `--skip-patents`, so these labels carry no patent evidence. | GitHub Actions artifacts of `.github/workflows/nightly.yml` | VOID: every `true_invention_gap` in them |
| 2026-10-15 and 2026-10-16 | Nightly runs on `f38c47b` (2026-10-15 08:21Z) and `c648e9b` (2026-10-16 08:13Z). `f38c47b` predates the theta fix `c5a2d03` (committed 2026-10-16 01:59Z). `c648e9b` includes it but predates `323b6b9`, so its `true_invention_gap` labels still had no patent evidence. | Job logs and artifacts as above | VOID: both runs ran code with one of the defects the cutoff exists for |
| Before 2026-10-15 | `novelty_auditor` `"pass"` verdicts | No store exists. `einstein.db` has no audits table, and the agent stages were never wired into the CLI and never had a concrete `LLMClient` (`f807961`). Passes appear only in tests, live-check scripts and sandbox sessions: `sandbox-handoffs/einstein-17.md`, `-18.md` and `-20.md` (code that gates on `"pass"`), and `-0.5.md`, `-9it.md`, `-tix.md`, `-zfp.md`, `-b3b.md` and `-uts.md` (patent-side pass semantics, and live USPTO checks on 2026-09-29). | VOID wherever quoted |
| Unknown | Ad-hoc runs by the user or agents outside this repo | Not found | VOID under the same rule |
| 2026-09-24 | Triage evidence packs (`read` labels) | `portfolio/notes/discovery-triage.md`. `portfolio/notes/discovery-shortlist.md:49-50` already calls the passes vacuous. | Not passes. Never clearance. |
| 2026-09-24 | "Found by a prior-art check (einstein-827 triage)" | certabstain bead `certabstain-3d6` | A defect report, not a pass. Not voided. |

No einstein citation was found in `portfolio/docket/patent-docket.xlsx`, in certabstain `main`
(`PROVISIONAL_OUTLINE.md`, `SPEC.md`, `TECHNICAL_NOTE.md`, `README.md`), in chem `main`
(including `specs/`), or in mathgraph, certkit or narrator.

## What VOID means

- A void pass is not evidence of novelty, of absence of prior art, or of freedom to operate.
  It must never go to counsel as clearance (plan C6). Prior-art reads use PQAI, Google Patents
  and an agent reading papers (plan §3 Q5).
- Rerunning on current code does not revive a void pass. einstein is retired, and its patent
  side is uncalibrated.

## Nightly CI stopped (2026-10-30)

`.github/workflows/nightly.yml` was deleted from `main` on 2026-10-30 (portfolio bead
`portfolio-6hc.1`), which ends the daily scheduled run; until then it ran every day and
published a gap report from a retired tool. The only workflow left is `tests.yml`, which runs
on push and pull request, never on a schedule.

Removing the workflow stops new runs only. The existing job logs and artifacts above stay
public until they expire (logs about 90 days, so the oldest until about 2026-11-10; artifacts
30 days) or the runs are deleted (`gh run delete <run-id> -R B0001/einstein`, the owner's call).
