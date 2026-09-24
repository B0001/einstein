# einstein-0.6 handoff

## What changed

`README.md`, "Gap-detection evaluation harness" section only (lines ~60-165).
No code changed.

1. Added a third bullet describing the adjacent arm (`adjacent_pairs`, n=19,
   `einstein-0.4`) to the corpus description, which previously listed only
   the positive and negative arms. Updated "Both arms were fetched..." to
   "All three arms were fetched...".
2. Added per-sample resolution ("1 sample = 5.3 percentage points") next to
   *every* rate quoted in the section — the two pre-existing rows (FDR 37%,
   flag_rate 100%) previously had none.
3. Added the adjacent arm's flag_rate and abstention rate at the shipped
   default (threshold=0.20, margin=0) as new table rows.
4. Added a new "Adjacent arm, and which hypothesis it supports" paragraph
   with a small threshold-sweep table (0.01-0.05) and an explicit verdict:
   **the result supports TOPIC DISTANCE, not OPPORTUNITY** — see reasoning
   below.

## The hypothesis call, and why

At the shipped default (threshold=0.20) the adjacent arm and the cooking
(negative) arm are *both* saturated at flag_rate=1.00 — they don't diverge
at that specific point. It would be dishonest to stop there and call it
"no collapse, therefore opportunity" — that's an artifact of the default
being generous enough that everything above threshold~0.05 gets flagged
regardless of which hypothesis is true, the same way raising the threshold
can't fix the positive arm's floor FDR (already documented in the README
before this bead).

The actual divergence einstein-0.4 was built to detect appears below the
shipped default, in thresholds 0.01-0.04, where the cooking arm has already
saturated (flag_rate=1.00 from 0.02) but the adjacent arm collapses well
below it (32%-84%). At threshold=0.02 specifically — the point
`RealCorpusMeasurementTest.test_floor_false_discovery_rate_at_threshold_0_02`
asserts directly — cooking is 1.00 and adjacent is 7/19=0.368. Per
einstein-0.4's own framing in its module docstring and bead description
("if flag_rate collapses here while staying 1.00 on the cooking arm, the
score is topic distance, not opportunity"), that is the topic-distance
signature, and it's the only place in the swept grid where the two
hypotheses actually make different predictions. So the README states the
verdict as TOPIC DISTANCE, sourced from that band, and explains that the
shipped default's agreement is silence, not counter-evidence, because it's
past the point where the effect is observable at all.

I did not tune anything to get this answer — these are the sweep's own
numbers, unchanged from what `test_gap_benchmark.py` already asserted
(`test_floor_false_discovery_rate_at_threshold_0_02`,
`test_adjacent_arm_reaches_saturation_at_shipped_default_threshold`) before
this bead. I did not touch `einstein/gap_benchmark.py` or its tests.

## Where I was tempted to say "novel" / overclaim

Nowhere in the README text did I write "novel" or "no prior art" (this bead
doesn't touch the ideator). The one place I was tempted to soften language
was the hypothesis verdict itself — the shipped default's 1.00/1.00 reads
superficially like "no problem here," and it would have been easy to lead
with that and bury the 0.01-0.04 collapse as a footnote. I did the opposite:
led with the collapse as the actual finding and explained why the shipped
default's agreement doesn't override it, per this repo's standing instruction
to report the bad/complicating number rather than the flattering one.

## Verification

Full suite:

```
$ uv run python -m unittest discover tests
Ran 282 tests in 0.337s
OK
```

(The `GitHub search failed: 429...` / `OpenAlex lookup failed...` /
`USPTO search failed...` lines interleaved in that output are stubbed
fixture responses exercising the fetchers' own error-handling paths, not
live network calls — confirmed by reading `tests/` fixtures; the suite
already ran in 0.337s, which live API calls could not do.)

Every number quoted in the new/changed README text was copied verbatim from
this run (same session, same command):

```
$ uv run python -m einstein.gap_benchmark
positive arm (known paper<->repo links):        n=19  (1 sample = 5.3 pts)
negative arm (paper<->unrelated/cooking repo):  n=19  (1 sample = 5.3 pts)
adjacent arm (paper<->adjacent-domain ML repo): n=19  (1 sample = 5.3 pts)

 margin threshold    FDR flag_rate  adj_flag abstain(+) abstain(-) abstain(adj) trustworthy
-------------------------------------------------------------------------------------------
   0.00      0.00   0.00      0.00      0.00       0.00       0.00         0.00          no
   0.00      0.01   0.16      0.79      0.32       0.00       0.00         0.00          no
   0.00      0.02   0.16      1.00      0.37       0.00       0.00         0.00          no
   0.00      0.03   0.21      1.00      0.63       0.00       0.00         0.00          no
   0.00      0.04   0.26      1.00      0.84       0.00       0.00         0.00          no
   0.00      0.05   0.26      1.00      1.00       0.00       0.00         0.00          no
   0.00      0.20   0.37      1.00      1.00       0.00       0.00         0.00          no
   ...
No operating point in this grid clears FDR<=0.1 and flag_rate>=0.9.
```

(threshold=0.20 row extracted from the full sweep table; full table has 30
rows across margin={0.00,0.02} x 15 thresholds, unchanged in shape from
einstein-0.4.)

Acceptance-criteria evidence, run at close time:

```
$ grep -n adjacent README.md
```
→ 9 matches (lines 84, 117, 134, 135, 138, 145, 147, 157, 162), including
the flag_rate table row and the "supports TOPIC DISTANCE" verdict paragraph.

```
$ grep -n '5.3\|resolution\|pts' README.md
```
→ every rate line in the section (both the summary table and the 0.01-0.05
sweep table) carries a "1 sample = 5.3 pts" or "resolution" annotation.

## What I did not do, and why

- Did not touch `einstein/gap_benchmark.py`, its tests, or the fixture —
  einstein-0.4's acceptance criteria 1 and 2 (harvester, per-arm sweep
  columns) were already confirmed met by the bead description before I
  started; re-verifying them was in scope for reading, not for changing.
- Did not add a patent-axis adjacent arm or otherwise expand benchmark
  coverage — out of scope for this bead (README wording only), and the
  existing "What this measurement does not cover" section already states
  the patent-axis gap honestly; I left it untouched since it's accurate.
- Did not attempt to raise/lower `DEFAULT_REPO_THRESHOLD` to find an
  operating point where the two arms diverge favorably — that would be
  tuning the demo to look better, which CLAUDE.md explicitly forbids ("Do
  not tune a threshold until a demo looks good and call that a finding").
- Did not commit or push, per this session's git policy. Tree is left dirty
  with only `README.md` modified (plus pre-existing unrelated
  `.beads/issues.jsonl` / `.claude/settings.local.json` changes not from
  this session — see `git status` below).

## What I could not verify

Nothing outstanding. The hypothesis verdict is a documented inference from
numbers the test suite already asserts and the live benchmark run
reproduces exactly — not a new measurement I ran unchecked.

## git status at close

```
$ git status
On branch main
Your branch is up to date with 'origin/main'.
Changes not staged for commit:
  modified:   .beads/issues.jsonl   (pre-existing, not from this session)
  modified:   README.md             (this session)
Untracked files:
  .claude/settings.local.json       (pre-existing, not from this session)
```

Suggested commands for a human to run (not run by me, per this session's git
policy):

```bash
git add README.md
git commit -m "einstein-0.6: document adjacent-arm benchmark result in README"
bd close einstein-0.6
```

Note: this container's `git config` did not have `safe.directory
/workspace` set, which makes every git command (including plain `git
status`) fail with "detected dubious ownership" — I added that one global
config entry (`git config --global --add safe.directory /workspace`) to be
able to run `git status`/inspect the tree at all. That is the only git
config change made; no commit, push, or dolt sync was run.
