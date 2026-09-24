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

### Gap persistence + velocity (`einstein-0.3`)

```bash
uv run einstein "stochastic gradient descent" --db einstein.db
```

Every invocation opens a `Store` at `--db` (default `einstein.db`, relative to
cwd, gitignored) and dedupes that run's gaps against every prior run's via
`einstein/velocity.py`. Each gap in the report carries a `velocity` field —
`is_new` / `trend` (`new` / `rising` / `dormant` / `stable`) / `age_days` /
`similarity_delta` — instead of every run looking like the first. Point
`--db` at the same file across scheduled runs to get real dedup/velocity;
a fresh path per run (or `:memory:`-equivalent behavior) makes every run
"new" again.

### Cross-pollination (`--cross-pollinate`, `einstein-12`/`einstein-0.3`)

```bash
uv run einstein "tensor network contraction" --cross-pollinate "protein folding"
```

`einstein/cross_pollination.py` is a paper x paper rule ("has method M been
applied to domain B, per the citation graph this run fetched") — structurally
different from the paper x repo/patent rule the rest of this CLI runs, and it
needs a second corpus this CLI otherwise has no concept of. It is **off by
default**: it re-fetches both `domain` and `--cross-pollinate`'s argument as
fresh OpenAlex paper corpora (not the arXiv-fetched `papers` from the main
run — cross-pollination needs both sides in OpenAlex's id space, see the
module docstring) and fetches citation edges one call per unique DOI across
both corpora, which is real, additional API load. Papers OpenAlex has no DOI
for are counted (`papers_without_doi`) and skipped, never silently dropped.
Candidates are persisted and velocity-scored through the same `--db` Store
as the main gaps, under `kind: "cross_pollination"`. `--cross-pollination-threshold`
sets the cosine-similarity floor for a candidate (default matches
`einstein.cross_pollination.DEFAULT_SIMILARITY_THRESHOLD`).

### Constraint & friction mining (`--mine-constraints`, `einstein-9`/`einstein-0.7`)

```bash
uv run einstein "stochastic gradient descent" --mine-constraints someone/transformer-lib
```

`einstein/constraint_mining.py` scrapes Limitations/Future Work sections out
of this run's papers (a live per-paper e-print fetch via
`einstein.arxiv_source.extract_source`) and `bug`/`help wanted` issues off
each named repo (`einstein.github_fetcher.fetch_issues`), then single-link
clusters the combined pain points by cosine similarity. It is **off by
default**: it is real, additional API load (one e-print fetch per paper in
the run, one issues fetch per `--mine-constraints` repo) on top of the main
query. Repeat the flag for more than one repo
(`--mine-constraints a/b --mine-constraints c/d`); every repo named must
already be part of this run's fetched corpus (i.e. surfaced by the main
`domain` search), or the run fails with no partial report, same "no partial
report on a live-fetch failure" contract the three main sources have. A
paper with no LaTeX source on arXiv (`NoTexSourceError`) is counted
(`papers_without_tex_source`) and skipped, not treated as a failure. Only
clusters spanning >1 distinct source ("widespread") are rendered — friction
reported in more than one paper/repo is a stronger signal than friction
reported once, never a severity or unsolved-elsewhere claim. Widespread
clusters are persisted and velocity-scored through the same `--db` Store as
the main gaps, under `kind: "constraint_cluster"`.
`--constraint-similarity-threshold` sets the cosine-similarity floor for
clustering (default matches
`einstein.constraint_mining.DEFAULT_SIMILARITY_THRESHOLD`).

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
- **Adjacent arm** (`adjacent_pairs`, n=19, `einstein-0.4`): the same 19
  papers, each paired with a real, well-known ML/CS repo from a *different
  subfield* — RL, classical ML, MLOps, graph algorithms, distributed
  training, dialogue systems — that is neither that paper's nor any other
  paper's implementation. Ground-truth **gaps**, same as the negative arm,
  but constructed so shared ML/CS jargon, not shared topic-by-domain, is the
  only thing that could make the detector fail to flag them. This is the arm
  that separates "the score measures opportunity" from "the score measures
  topic distance" — the cooking arm alone can't, because both hypotheses
  predict its flag_rate=1.00.

All three arms were fetched once from live arXiv/GitHub APIs by
`scripts/harvest_gap_benchmark.py` and are checked in at
`tests/fixtures/gap_benchmark.json`; `tests/test_gap_benchmark.py` and
`einstein/gap_benchmark.py` read that file and touch no network.

**Regenerate every number in this section with:**

```bash
uv run python -m einstein.gap_benchmark
```

### Measured result

At the pipeline's shipped default (`DEFAULT_REPO_THRESHOLD = 0.2` in
`einstein/gaps.py`, margin=0), on this 19-pair-per-arm corpus (n=19 per arm
=> **1 sample = 5.3 percentage points**, so any rate below is only resolved
to the nearest 5.3-pt step):

| | value |
|---|---|
| False-discovery rate (positive arm, threshold=0.20) | **37%** (7/19 known implementations wrongly called a gap; 1 sample = 5.3 pts) |
| Flag rate (negative arm, threshold=0.20) | 100% (19/19 unrelated pairs correctly flagged; 1 sample = 5.3 pts) |
| Flag rate (adjacent arm, threshold=0.20) | 100% (19/19 adjacent-domain pairs correctly flagged; 1 sample = 5.3 pts) |
| Abstention rate (all three arms, threshold=0.20) | 0% (margin=0 forces every score to a match/gap call, never `"abstain"` — see `classify` in `einstein/gap_benchmark.py`) |

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

**Adjacent arm, and which hypothesis it supports.** At the shipped default
(threshold=0.20) the adjacent arm has caught up to the cooking arm: both
`flag_rate` and `adjacent_flag_rate` read **100% (19/19; 1 sample = 5.3
pts)**, so at this specific operating point the two arms do not diverge —
confirmed by
`RealCorpusMeasurementTest.test_adjacent_arm_reaches_saturation_at_shipped_default_threshold`
in `tests/test_gap_benchmark.py`. But that agreement is a saturation effect,
not evidence for either hypothesis: by threshold 0.05 both arms are already
pinned at 1.00, so nothing above that threshold — including the shipped
0.20 — can tell "opportunity" and "topic distance" apart. The divergence
`einstein-0.4` was filed to look for shows up **below** the shipped default,
in the 0.01–0.04 band, where the cooking arm has already saturated but the
adjacent arm has not:

| threshold | flag_rate (cooking) | adjacent_flag_rate | resolution |
|---|---|---|---|
| 0.01 | 79% | 32% (6/19) | 1 sample = 5.3 pts |
| 0.02 | 100% | 37% (7/19) | 1 sample = 5.3 pts |
| 0.03 | 100% | 63% (12/19) | 1 sample = 5.3 pts |
| 0.04 | 100% | 84% (16/19) | 1 sample = 5.3 pts |
| 0.05 | 100% | 100% (19/19) | 1 sample = 5.3 pts |

At threshold=0.02 specifically — the point `tests/test_gap_benchmark.py`'s
`test_floor_false_discovery_rate_at_threshold_0_02` asserts directly — the
cooking arm's flag_rate is already 1.00 while the adjacent arm sits at
7/19 = 0.368, a real collapse, not noise at this corpus's 5.3-pt
resolution. Per `einstein-0.4`'s own framing ("if flag_rate collapses here
while staying 1.00 on the cooking arm, the score is topic distance, not
opportunity"), **this result supports TOPIC DISTANCE**: shared ML/CS jargon
between a paper and an unrelated adjacent-subfield repo measurably inflates
similarity relative to a truly foreign domain like cooking, at every
threshold where the two arms can be told apart at all. The shipped default
does not contradict that finding — it just operates past the point (0.05)
where the effect is visible, the same way raising the threshold cannot fix
the positive arm's floor FDR above.

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
