# einstein-0.1 handoff — Gap-detection evaluation harness with a genuine negative arm

## Status

Closed. The implementation was already present in the working tree at session
start (untracked files, bead marked `in_progress` since 2026-08-11 with no
notes and no handoff file — a previous worker did the implementation but
never recorded evidence or closed the bead). This session did **not** write
new implementation code. It read every file involved, re-ran every command
the acceptance criteria require, checked the printed numbers against the
README's claims by hand, and only then closed the bead. Below is what was
verified and how.

## What exists

- `einstein/gap_benchmark.py` — three-valued `classify()` (match/gap/abstain
  with a margin band), `evaluate()`/`sweep()` producing `ThresholdReport`
  rows (FDR, flag rate, positive/negative abstention rate per
  threshold×margin), `main()` printing the full grid and a trustworthy/not
  verdict.
- `tests/test_gap_benchmark.py` — 17 tests, no network.
- `scripts/harvest_gap_benchmark.py` — one-time, hand-run script that hit
  live arXiv + GitHub APIs to build the fixture. Not part of the test suite.
- `tests/fixtures/gap_benchmark.json` — 19 positive pairs (real paper ↔ its
  own canonical reference-implementation repo, e.g. BERT ↔
  `google-research/bert`) and 19 negative pairs (the same 19 papers, each
  paired with a real repo from an unrelated domain: cooking, knitting,
  dotfiles, etc.). Real fetched text, not fabricated — see verification below.
- README.md § "Gap-detection evaluation harness (`einstein-0.1`)" — states
  the measured numbers and the negative finding.

## Verification performed this session (commands and their output)

**Full suite, no network:**
```
uv sync
uv run python -m unittest discover tests
```
→ `Ran 220 tests in 0.310s` / `OK`. (17 of those are `tests/test_gap_benchmark.py`,
also run in isolation: `uv run python -m unittest tests.test_gap_benchmark -v`
→ 17/17 ok.) `gap_benchmark.py` and its test module only call `json.loads` on
the checked-in fixture — no `requests`/`arxiv` import in the hot path, and the
full-suite run touches no network (the only network-shaped log lines in the
run are from *other* fetcher modules' unit tests exercising their own
error-handling against a deliberately fake `http://x` host — unrelated to
this bead).

**Regenerate every number the README states:**
```
uv run python -m einstein.gap_benchmark
```
→ printed grid matches the README's table exactly:
- threshold=0.20, margin=0.00: FDR=0.37, flag_rate=1.00 (README: "37% (7/19)", "100% (19/19)")
- threshold=0.02, margin=0.00: FDR=0.16, flag_rate=1.00 (README: "floor false discovery rate of 16% (3/19)")
- Every row in the 30-row grid (thresholds 0.00–0.50 × margins {0.00, 0.02}) printed `trustworthy=no`.
- Final line: `No operating point in this grid clears FDR<=0.1 and flag_rate>=0.9.`

**Spot-check of the specific "3 papers score 0.0" claim:**
```
uv run python -c "
from einstein.gap_benchmark import load_fixture, score_pairs, _fit_shared_embedder
positive, negative = load_fixture()
embedder = _fit_shared_embedder(positive, negative, None)
scores = score_pairs(positive, embedder)
for p, s in zip(positive, scores):
    if s == 0.0:
        print(p.paper.id, s)
"
```
→ `2102.12092v2 0.0`, `2010.11929v2 0.0`, `1701.07875v3 0.0` — exactly the
three IDs (DALL-E, ViT, WGAN) the README names.

**Fixture is real, not fabricated:** inspected
`tests/fixtures/gap_benchmark.json` directly — the BERT entry's `raw` field
carries the actual arXiv abstract text, real author list (Devlin, Chang, Lee,
Toutanova), `entry_id`, `primary_category: cs.CL`, matching the live paper.
19/19 positive and 19/19 negative pairs share the same 19-paper set (checked
via `LoadFixtureTest.test_positive_pairs_reuse_the_same_papers_as_negative_pairs`,
passing).

## Acceptance criteria — checked one by one

1. **"Both arms are checked-in fixtures with recorded API responses. No
   network in the test suite."** — Met. `tests/fixtures/gap_benchmark.json`
   is checked in (untracked in git status pending commit, but present and
   readable); confirmed no network calls occur during
   `unittest discover tests`.
2. **"One command regenerates every number, and that command is named in the
   README next to each figure."** — Met.
   `uv run python -m einstein.gap_benchmark` is named directly under the
   README's "Gap-detection evaluation harness" heading and reproduced every
   figure stated there, verified above.
3. **"The README states the measured false-discovery rate and whether an
   operating point exists where the detector is trustworthy. If none exists,
   that is the finding and it gets published as the finding."** — Met. README
   states FDR=37% at the shipped default threshold, a 16% floor FDR from 3
   papers whose abstracts share zero vocabulary with their own repo's
   description, and explicitly: "No operating point in the swept grid ...
   clears the bar this module defines as trustworthy." It does not tune the
   threshold to hide this.
4. **"An 'abstain' outcome is a first-class Gap classification, not an error
   case."** — Met, in the scope this bead builds: `gap_benchmark.classify()`
   returns `"abstain"` as one of three ordinary `Outcome` values (not an
   exception, not a sentinel error), with dedicated tests
   (`ClassifyTest.test_inside_band_is_abstain`,
   `EvaluateTest.test_abstention_band_removes_scores_from_both_confident_rates`).
   Note: `einstein/gaps.py`'s shipped `detect_gaps`/`Match.below_threshold`
   is still a hard two-valued boundary (no abstain band) — this bead's
   `margin=0.0` sweep row is the direct measurement of that shipped detector,
   and `margin>0.0` rows show what an abstaining variant would report
   instead. Wiring abstain into `detect_gaps` itself was not asked for by
   this bead's acceptance text and was not done; if `einstein-15` (ideator,
   blocked on this bead) wants that, it's a new bead, not implied by this one.

## What I decided not to do, and why

- Did not build a patent-axis positive/negative arm. `einstein/uspto_fetcher.py`
  requires `USPTO_API_KEY`, unset in this environment, and refuses to
  fabricate patent data when no key is present (its own module docstring).
  The README already states this as an explicit, named coverage gap rather
  than silently omitting the patent axis or faking patent fixtures.
- Did not test a dense embedder (`SentenceTransformerEmbedder`) — it's not a
  pinned dependency, and the bead's environment instructions say keep
  dependencies minimal. README states this is unmeasured, not assumed to
  help or not.
- Did not touch `mathgraph/evaluate.py`, `bench_pfr.py`, or
  `certabstain/conformal.py` — none of the three exist in this checkout (the
  bead references "your own repos" but this container only has `einstein/`).
  README already states this rather than inventing a conformal-guarantee
  threshold procedure that cites nonexistent code.
- Did not modify `einstein/gaps.py` to add an abstain band to the shipped
  detector — out of this bead's acceptance text (see point 4 above); filing
  that as a follow-up is a decision for whoever picks up `einstein-15`, not
  this bead.
- Did not commit or push. Per repo git policy, tree is left ready to commit.

## What I could not verify

- Whether `1701.07875`'s repo id in the fixture (`martinarjovsky/WassersteinGAN`)
  and the other 18 GitHub repo identifiers are still live/canonical today —
  I did not re-hit the live GitHub API to reconfirm (that would violate "no
  network" for routine verification, and the harvest script is explicitly a
  hand-run, occasional-use tool, not something to re-run casually against a
  rate-limited API). I verified the fixture's *content* is real fetched data
  by inspection, not that the repos still exist at time of this session.
- Whether the specific 19 "unrelated repos" chosen for the negative arm are
  representative of the full space of "genuinely unconnected" pairs, or
  whether a larger/different sample would shift the 100% flag rate. The
  bead asks for this specific measurement, which is what's reported; a
  larger-N negative arm is future work, not something I could do without
  more live API calls in this session.

## Final test-run line

```
$ uv run python -m unittest discover tests
Ran 220 tests in 0.310s

OK
```

## bd status

`bd close einstein-0.1` was run after all of the above passed. Notes with
the same verification summary were attached via `bd update einstein-0.1
--notes=...` before closing.

## git status (unchanged by this session — no files were edited)

Per repo git policy, no `git add`/`commit`/`push` was run. Everything listed
below was already present before this session started (git status at session
start, reproduced here for the record — this session added nothing to it):

```
AM .beads/issues.jsonl
 M .beads/interactions.jsonl
 M .gitignore
A  .python-version, README.md, einstein/__init__.py, einstein/arxiv_fetcher.py,
   einstein/arxiv_source.py, einstein/embedding.py, einstein/github_fetcher.py,
   einstein/openalex_fetcher.py, einstein/schema.py, main.py, pyproject.toml,
   sandbox-prompt.md, tests/test_arxiv_fetcher.py, tests/test_embedding.py,
   tests/test_github_fetcher.py, tests/test_openalex_fetcher.py,
   tests/test_schema.py, uv.lock
?? .claude/settings.local.json, einstein/cli.py, einstein/gap_benchmark.py,
   einstein/gaps.py, einstein/graph.py, einstein/patent_claims.py,
   einstein/store.py, einstein/uspto_fetcher.py, einstein/velocity.py,
   scripts/, tests/fixtures/, tests/test_cli.py, tests/test_gap_benchmark.py,
   tests/test_gaps.py, tests/test_graph.py, tests/test_patent_claims.py,
   tests/test_store.py, tests/test_uspto_fetcher.py, tests/test_velocity.py
```

Suggested next commands for a human, unchanged from repo convention (not run
by me):
```
git add -A
git commit -m "..."
bd dolt push   # only if a Dolt remote is configured; this environment has none
git push
```
