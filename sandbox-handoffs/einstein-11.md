# einstein-11: Three-way gap detection matrix

Status: **closed**, evidence below.

## What I changed

New file `einstein/gaps.py`. Nothing else in the pipeline was touched (the
`einstein/store.py`, `uspto_fetcher.py` and their tests already in the
working tree at session start were prior work, not mine — I read them for
the `Gap` shape contract `store.py` had already committed to and left them
alone).

`detect_gaps(papers, repos, patents, *, repo_threshold=0.2, patent_threshold=0.2, embedder=None) -> list[Gap]`:

- Fits one embedder over the union of paper/repo/patent text via the
  existing `einstein.embedding.embed_groups` (default TF-IDF, swappable —
  same contract einstein-10 established), then computes
  `paper_vs_repo = cosine_similarity(paper_vecs, repo_vecs)` and
  `paper_vs_patent = cosine_similarity(paper_vecs, patent_vecs)`.
- For each paper: `repo_match` = best (highest-similarity) repo,
  `patent_match` = best patent. If `repo_match.best_similarity >=
  repo_threshold` the paper is not a gap (already implemented somewhere in
  this index) — skipped regardless of patent status. Otherwise:
  - `patent_match.best_similarity < patent_threshold` → `kind =
    "true_invention_gap"` (no repo, no patent)
  - else → `kind = "open_source_disruption_target"` (no repo, but a patent
    exists — someone filed, nobody shipped open code)
- For each repo: read the *same* `paper_vs_repo` matrix by column — the
  best-matching paper for that repo. Below `repo_threshold` → `kind =
  "unformalized_code"`.
- Missing corpora are not silently treated as "found nothing suspicious":
  an empty `repos` list makes every low-patent paper `true_invention_gap`
  by construction (no repo *can* be found), and the module docstring says
  explicitly that this is indistinguishable, from inside this function,
  from "nobody built it" vs. "we didn't fetch any repos this run." A
  caller presenting that output must carry that caveat forward — this
  module cannot detect its own blind spots, that's what einstein-0.1 (still
  open, blocked on this bead, not touched by me) is for.

`Gap` (frozen dataclass): `kind` (one of `GAP_KINDS` =
`true_invention_gap` / `open_source_disruption_target` /
`unformalized_code`), `subject_type`, `subject_id`, `subject_title`,
`matches: tuple[Match, ...]` — the evidence, not a bare score. `Match`
carries `against_type`, `best_id` (`None` only when that corpus was
empty), `best_similarity`, and the `threshold` it was judged against, so a
consumer can see exactly what was compared to what, not just a label.
`Gap.gap_key` (`f"{kind}:{subject_type}:{subject_id}"`) and `to_payload()`
(JSON-serializable dict) exist specifically to satisfy the
`Store.upsert_gap(gap_key, kind, payload)` contract `einstein/store.py`
had already reserved for this bead — verified by
`tests/test_gaps.py::DetectGapsStoreIntegrationTest`, which round-trips
detected gaps through a real (tempdir) `Store` and checks a second
`detect_gaps` + upsert pass doesn't duplicate rows.

Every record passed to `papers=`/`repos=`/`patents=` is asserted to
actually have `record.type` matching the parameter it was passed under —
a caller swapping `repos` and `papers` gets an `AssertionError`, not a
matrix that's silently transposed wrong.

No language anywhere in this module or its tests says "novel" or "no
prior art" — see the module docstring's second paragraph, which states
outright that a `Gap` here means "no match found in this index, this run,
at this threshold" and nothing more. That framing question is the one
place in this task I was tempted to write something stronger ("nobody has
built this") and didn't.

## Test evidence

```
uv run python -m unittest discover tests
```
```
Ran 108 tests in 0.077s

OK
```

New file `tests/test_gaps.py`, 24 of those 108 tests. Covers:
- `Match`/`Gap` construction and validation (bad `kind`, bad
  `against_type`, empty `matches` tuple all raise `AssertionError`).
- `gap_key` stability/uniqueness, `to_payload()` JSON-serializability.
- The bead's literal acceptance criterion —
  `test_yields_all_three_classes_on_fixture_data`: a 3-paper / 2-repo /
  1-patent fixture built so one paper is `true_invention_gap`, one is
  `open_source_disruption_target`, one has a repo and is *not* a gap; one
  repo is `unformalized_code`, one has a paper and is not.
- Empty-corpus edge cases (empty repos, empty papers, everything empty —
  the last one required a short-circuit in `detect_gaps` because
  `TfidfVectorizer.fit([])` on truly nothing raises `ValueError: empty
  vocabulary`, not a graceful empty result).
- Wrong-`Record.type` assertion.
- Threshold boundaries (`repo_threshold=0.0` → no paper is a gap;
  `repo_threshold=1.0` → every paper is, since TF-IDF cosine similarity
  between distinct documents never reaches exactly 1.0).
- Store round-trip and upsert-idempotency (above).

Also ran the module's own `_self_check()` (same pattern as
`schema.py`/`store.py`/`embedding.py` in this repo) directly:
```
uv run python -m einstein.gaps
```
```
einstein.gaps self-check: OK
```

## Numbers

The only "numbers" this bead produces are similarity scores over a fixture
corpus I wrote myself for the test — not a claim about any real
paper/repo/patent. Regenerate with:
```
uv run python -m einstein.gaps
```
Every score in that fixture is fabricated text designed to land clearly on
one side of `threshold=0.3`; it is a unit test, not a measurement, and I
did not present it as one. The bead this leads into —
**einstein-0.1** — is the one that will produce a real, checked
false-discovery number against a genuine negative arm. I did not touch
einstein-0.1 or run it against live data; nothing in this session claims a
real gap was found anywhere.

## What I decided not to do

- **Did not pick a "correct" default threshold.** `DEFAULT_REPO_THRESHOLD
  = DEFAULT_PATENT_THRESHOLD = 0.2`, copied from the gemini_convo.md
  prototype's ballpark, exists only so the function is callable without
  arguments — it is not a validated operating point. einstein-0.1's job is
  to sweep this and publish a curve; I left it as an obvious, overridable
  constant rather than pretending 0.2 means something.
- **Did not add an "abstain" class.** einstein-0.1's description asks for
  abstention as "a first-class Gap classification" — that's explicitly
  that bead's acceptance criterion, not this one's ("fixture data yields
  the expected 3 classes" — three, not four). Adding it here would have
  been scope creep into a bead that's designed to require a labeled
  benchmark I don't have. Left `GapKind` at exactly three values.
- **Did not touch `einstein-12`** (method-domain cross-pollination
  detection), which I found already `in_progress`, owned by a different
  session (`assignee: sandbox`, started 2026-08-10). It's a distinct rule
  (embedding-close + zero-citation-edge) from this bead's repo/patent
  cosine-threshold matrix and depends on einstein-6 (citation edges), not
  einstein-11. I did not touch its files or state.
- **Did not wire this into a CLI or the store automatically.** einstein-14
  (LangGraph skeleton, blocked on this bead, still open) is where
  `detect_gaps` output gets consumed by a real pipeline run; that's a
  separate bead and I left it alone.
- **Did not add a new dependency.** `cosine_similarity` comes from
  `scikit-learn`, already pinned in `pyproject.toml`; no `pyproject.toml`
  change was needed.

## What I could not verify

- **Real-world discriminative power.** This bead's acceptance bar is
  "fixture data yields the expected 3 classes," which I've shown. Whether
  `detect_gaps` actually separates true gaps from index-thinness noise on
  real arXiv/GitHub/USPTO data is unverified by design — that's
  einstein-0.1, not this bead, and I did not run this against any live
  fetch.
- **Behavior under the dense `SentenceTransformerEmbedder` backend.**
  `detect_gaps` accepts any `Embedder`, and `embed_groups` is already
  tested against a non-TF-IDF backend in `tests/test_embedding.py`, but I
  did not write a `test_gaps.py` case exercising a real dense embedder
  (it isn't installed — `sentence-transformers` pulls in torch and model
  weights, explicitly excluded from the test suite by the module's own
  docstring). Reasoned to be fine by interface (`Match`/`Gap` construction
  only touches the `(n, d)` array shape, not values), not measured.

## Commands to hand off

```bash
git status
git add einstein/gaps.py tests/test_gaps.py
git commit -m "..."   # not run — git policy is hands-off this session
```

I did not `git add`, `git commit`, `git push`, or run `bd dolt push` —
per this repo's git policy, that's for a human to run.
