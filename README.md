# einstein

A gap-detection pipeline: ingest arXiv / GitHub / USPTO / OpenAlex, normalize
every source into one `Record` schema (`einstein/schema.py`), embed and
compare (`einstein/embedding.py`), and detect structural gaps between what
has been published and what has been built or filed on (`einstein/gaps.py`).

`bd show einstein-0` is the epic; its children are the pipeline stages.

## Setup

Python 3.12, [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python -m unittest discover tests
```

`uv run` writes its virtualenv to `/tmp/venv` in this container
(`UV_PROJECT_ENVIRONMENT`) — the mounted host `.venv` is not used here.

## CLI (`einstein-21`)

```bash
uv run einstein "stochastic gradient descent"
uv run einstein "stochastic gradient descent" --json --output report.json
uv run einstein "stochastic gradient descent" --skip-patents  # no USPTO_API_KEY configured
```

`--repo-threshold` / `--patent-threshold` map directly onto `einstein/gaps.py`'s
thresholds. `--skip-papers` / `--skip-repos` / `--skip-patents` leave a corpus
empty *on purpose*; the report says `skipped (--skip-x)`, never a bare zero,
because "the user chose not to search" and "the search failed" are different
facts and this CLI keeps them visibly separate (see `einstein/cli.py`'s module
docstring). A live fetch failure (rate limit, missing `USPTO_API_KEY`,
unreachable API) exits nonzero with **no report at all** — a report built on
a partially-failed ingest is worse than no report.

### Scheduled runs

Two ways to run this on a schedule, per the bead's own note — cron/Actions
before a DAG engine, since this is a linear pipeline:

- `.github/workflows/nightly.yml` — a scheduled GitHub Actions job (03:17 UTC
  daily, also runnable by hand via `workflow_dispatch`) that runs the CLI for
  one domain and uploads the JSON report as a build artifact. Uses the job's
  own `GITHUB_TOKEN` for the GitHub fetcher's higher rate limit; passes
  `--skip-patents` automatically if the `USPTO_API_KEY` repo secret is not
  set.
- `scripts/nightly_run.sh` — the same wrapper for a self-hosted `cron`,
  writing timestamped JSON reports under `./reports/`.

Neither pushes results anywhere beyond the artifact/file — wiring a
destination (issue, dashboard, notification) is separate, unbuilt scope.

Tests never touch the network: every fetcher test runs against a recorded or
faked response. Live API calls (arXiv, GitHub, OpenAlex, USPTO) are separate,
hand-run scripts under `scripts/`, not part of `unittest discover`.

## Gap-detection evaluation harness (`einstein-0.1`)

`detect_gaps` (`einstein/gaps.py`) classifies a paper as a gap when its best
cosine-similarity match against a repo/patent corpus falls below a threshold.
That is a claim about absence: "no match found in *this* index, at *this*
threshold" — not "nobody has built this." The only way to know whether that
claim is trustworthy is to measure it against pairs whose ground truth is
already known, including pairs the detector is expected to correctly *not*
flag.

`einstein/gap_benchmark.py` does that measurement over a checked-in corpus of
real (not fabricated) arXiv papers and GitHub repos:

- **Positive arm** (`positive_pairs`, n=19): real papers paired with their
  own known, canonical reference implementation — e.g. BERT
  (`1810.04805`) &harr; `google-research/bert`, CLIP (`2103.00020`) &harr;
  `openai/CLIP`. Ground-truth **non-gaps**. Every pair the detector's score
  puts below threshold is a false discovery: a paper with real, findable
  code that the pipeline would report to an ideator as unbuilt.
- **Negative arm** (`negative_pairs`, n=19): the same 19 papers, each paired
  with a real GitHub repo from a domain with no ML/CS-research vocabulary
  overlap — cooking, knitting, woodworking, coffee roasting, quilting, and
  so on. Genuinely unconnected by construction, not merely "not the right
  repo." Ground-truth **gaps** — the detector should flag every one.

Both arms were fetched once from live arXiv/GitHub APIs by
`scripts/harvest_gap_benchmark.py` and are checked in at
`tests/fixtures/gap_benchmark.json`; `tests/test_gap_benchmark.py` and
`einstein/gap_benchmark.py` read that file and touch no network.

**Regenerate every number in this section with:**

```bash
uv run python -m einstein.gap_benchmark
```

### Measured result

At the pipeline's shipped default (`DEFAULT_REPO_THRESHOLD = 0.2` in
`einstein/gaps.py`), on this 19-pair corpus:

| | value |
|---|---|
| False-discovery rate (positive arm, threshold=0.20) | **37%** (7/19 known implementations wrongly called a gap) |
| Flag rate (negative arm, threshold=0.20) | 100% (19/19 unrelated pairs correctly flagged) |

The negative arm alone looks clean — every genuinely unrelated pair scores
below 0.02 cosine similarity, comfortably separated from threshold. The
positive arm is where the claim breaks: **3 of the 19 known paper&harr;repo
links score exactly 0.0 similarity** (`2102.12092`/DALL-E,
`2010.11929`/ViT, `1701.07875`/WGAN vs. their own repos) — zero shared
vocabulary between the paper's abstract and the repo's description/topics,
because a real reference-implementation README often just doesn't restate
the abstract in words a TF-IDF vectorizer can see. That sets a **floor false
discovery rate of 16% (3/19) at every threshold above 0**, no matter where
the line is drawn — raising the threshold only trades more false discoveries
for no additional correctly-flagged negatives (the negative arm is already
saturated at 100% by threshold 0.02).

**No operating point in the swept grid (threshold 0.00–0.50, margin 0.00 or
0.02) clears the bar this module defines as trustworthy** (`FDR ≤ 10%` and
`flag_rate ≥ 90%`, see `TRUSTWORTHY_MAX_FDR` / `TRUSTWORTHY_MIN_FLAG_RATE` in
`einstein/gap_benchmark.py`) — confirmed by
`RealCorpusMeasurementTest.test_no_trustworthy_operating_point_in_swept_grid`
in `tests/test_gap_benchmark.py`. This is a negative result on this corpus,
reported as the finding: **TF-IDF cosine similarity between paper
abstract-text and repo description-text, alone, is not a trustworthy signal
for "this paper has no implementation."** It separates genuinely unrelated
pairs well, but a nontrivial fraction of *real, known* implementations are
lexically distant enough from their own paper to be indistinguishable from a
true gap. Any consumer of a `true_invention_gap` from this pipeline (the
ideator node, `einstein-15`, blocked on this bead) must carry that ~16%+
floor false-discovery rate forward, not present a `Gap` as "no prior
implementation exists."

This tracks the outcome the bead anticipated from prior art in a related
repo (`mathgraph/evaluate.py` + `bench_pfr.py`, not present in this
checkout): a negative arm with no trustworthy operating point, ~15% ballpark
precision ceiling.

### What this measurement does not cover

- **Patent axis unmeasured.** `detect_gaps` also checks a patent corpus
  (`patent_threshold`) to distinguish `true_invention_gap` from
  `open_source_disruption_target`, but only the repo axis decides whether a
  paper is a gap *at all* — a paper with a close repo match is never a gap
  regardless of patent status. This benchmark's positive/negative arms are
  built entirely on the repo axis for that reason. A patent-citing-arXiv
  positive arm was not built: `einstein/uspto_fetcher.py` requires
  `USPTO_API_KEY`, which is not configured in this environment, and this
  repo's own fetcher deliberately refuses to fabricate patent data when no
  key is present (see its module docstring) — so this benchmark does the
  same and states the gap in coverage rather than inventing patent fixtures.
- **Repo-corpus size.** Each arm scores a paper against exactly one paired
  repo (see `einstein/gap_benchmark.py` module docstring for why), not
  `detect_gaps`'s real best-of-corpus argmax over every fetched repo. A live
  pipeline run's repo corpus is larger, which can only change the *specific*
  best match a paper finds, not the underlying vocabulary-overlap problem
  this measurement exposes.
- **Embedder.** Measured only against the default `TfidfEmbedder`. A dense
  embedder (`SentenceTransformerEmbedder`) is not pinned as a project
  dependency and untested here — it might close some of this gap (dense
  embeddings are less sensitive to exact word choice) or might not; that is
  unmeasured, not assumed either way.
