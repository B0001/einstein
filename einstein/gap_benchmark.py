"""Evaluation harness for `einstein.gaps.detect_gaps`'s core claim: "no repo
matches this paper in this index at this threshold" -- measured against a
genuine negative arm, not just fixture data that happens to sort into three
buckets (that's all `tests/test_gaps.py` proves).

The corpus (`tests/fixtures/gap_benchmark.json`, produced once by
`scripts/harvest_gap_benchmark.py` against live arXiv/GitHub APIs, checked in
so this module and its tests touch no network) has two arms:

- ``positive_pairs``: real papers paired with their own known, canonical
  reference implementation (BERT <-> google-research/bert, CLIP <->
  openai/CLIP, ...). Ground-truth NON-gaps. Every pair the detector's
  similarity score puts below threshold is a false discovery: a paper with
  real, findable code that the detector would tell an ideator "nobody has
  built this."
- ``negative_pairs``: the same papers, each paired with a real repo from a
  domain with no ML/CS-research vocabulary overlap (cooking, knitting,
  woodworking, ...) -- genuinely unconnected by construction, not merely
  "not the right repo." Ground-truth gaps: the detector should flag every one.

Both arms share one fixed pairing (`papers[i] <-> repo[i]`), not a full
paper-vs-corpus search -- that isolates the question this benchmark asks
("does the *similarity score* for a specific known/unrelated pair fall on
the right side of the threshold?") from `detect_gaps`'s separate
best-of-corpus argmax step, which `tests/test_gaps.py` already covers.

Three-valued classification (`classify`) adds an ambiguous middle band controlled
by `margin`: a score within `margin` of `threshold` is `"abstain"`, not forced
into a match/gap call it isn't confident enough to make. `margin=0` recovers
the exact two-valued boundary `einstein.gaps.Match.below_threshold` uses, so
sweeping `margin=0` is a direct measurement of the shipped detector; `margin>0`
shows what an abstaining variant would report instead of a wrong answer.

Run `uv run python -m einstein.gap_benchmark` to regenerate every number this
module can produce (see the module's `main()` for the exact threshold/margin
grid) -- that command is what the README's benchmark section names next to
each figure it states.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from sklearn.metrics.pairwise import cosine_similarity

from einstein.embedding import Embedder, TfidfEmbedder
from einstein.schema import Record

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gap_benchmark.json"

Outcome = Literal["match", "gap", "abstain"]
OUTCOMES: tuple[str, ...] = get_args(Outcome)

# The bar a threshold must clear to be reported as "trustworthy" in main()'s
# summary: rare false discoveries against known implementations, and the
# negative arm mostly correctly flagged. Both numbers are arbitrary judgment
# calls, stated here so the README's claim about them is checkable against
# code, not a private intuition.
TRUSTWORTHY_MAX_FDR = 0.10
TRUSTWORTHY_MIN_FLAG_RATE = 0.90


def classify(similarity: float, threshold: float, margin: float = 0.0) -> Outcome:
    """Three-valued read of one paper<->repo similarity score.

    `"gap"` below `threshold - margin`, `"match"` above `threshold + margin`,
    `"abstain"` in between. `margin=0` collapses the band to a point, so every
    score is a confident match or gap -- the same boundary
    `einstein.gaps.Match.below_threshold` draws.
    """
    assert margin >= 0.0, margin
    if similarity < threshold - margin:
        return "gap"
    if similarity >= threshold + margin:
        return "match"
    return "abstain"


@dataclass(frozen=True, slots=True)
class BenchmarkPair:
    """One (paper, repo) pair with its ground-truth relationship.

    `label="linked"` (positive arm): `repo` is `paper`'s own known
    implementation -- a match here is correct, a gap here is a false
    discovery. `label="unrelated"` (negative arm): `repo` and `paper` are
    genuinely unconnected -- a gap here is correct, a match here means the
    score is picking up incidental lexical overlap, not opportunity.
    """

    paper: Record
    repo: Record
    label: Literal["linked", "unrelated"]


def _record_from_dict(d: dict) -> Record:
    return Record(
        type=d["type"], id=d["id"], title=d["title"], summary=d["summary"],
        url=d["url"], ts=d["ts"], raw=d["raw"],
    )


def load_fixture(path: Path = FIXTURE_PATH) -> tuple[list[BenchmarkPair], list[BenchmarkPair]]:
    """Load the checked-in benchmark corpus. Reads a local file only -- no
    network. Returns `(positive_pairs, negative_pairs)`."""
    data = json.loads(path.read_text())
    positive = [
        BenchmarkPair(paper=_record_from_dict(p["paper"]), repo=_record_from_dict(p["repo"]), label="linked")
        for p in data["positive_pairs"]
    ]
    negative = [
        BenchmarkPair(paper=_record_from_dict(p["paper"]), repo=_record_from_dict(p["repo"]), label="unrelated")
        for p in data["negative_pairs"]
    ]
    assert positive, "positive_pairs fixture is empty"
    assert negative, "negative_pairs fixture is empty"
    return positive, negative


def _text(record: Record) -> str:
    return f"{record.title} {record.summary}".strip()


def _fit_shared_embedder(
    positive: list[BenchmarkPair], negative: list[BenchmarkPair], embedder: Embedder | None
) -> Embedder:
    """Fit one embedder over every paper/repo text in both arms -- the same
    "one shared vector space per run" contract `einstein.embedding.embed_groups`
    establishes for `detect_gaps`, so this benchmark's scores are produced the
    same way the real detector's would be, not a more favorable private space.
    """
    if embedder is None:
        embedder = TfidfEmbedder()
    all_texts = [
        _text(pair.paper) for pair in (*positive, *negative)
    ] + [
        _text(pair.repo) for pair in (*positive, *negative)
    ]
    embedder.fit(all_texts)
    return embedder


def score_pairs(pairs: list[BenchmarkPair], embedder: Embedder) -> list[float]:
    """Cosine similarity of `paper` vs. `repo` for each pair, `embedder`
    already fit. This is a specific-pair score, not a best-of-corpus argmax
    -- see module docstring for why that's the right level for this
    benchmark."""
    if not pairs:
        return []
    paper_vecs = embedder.transform([_text(pair.paper) for pair in pairs])
    repo_vecs = embedder.transform([_text(pair.repo) for pair in pairs])
    sims = cosine_similarity(paper_vecs, repo_vecs)
    return [float(sims[i, i]) for i in range(len(pairs))]


@dataclass(frozen=True, slots=True)
class ThresholdReport:
    """One row of the sweep: everything measured at one (threshold, margin)."""

    threshold: float
    margin: float
    false_discovery_rate: float
    flag_rate: float
    positive_abstention_rate: float
    negative_abstention_rate: float
    n_positive: int
    n_negative: int

    @property
    def trustworthy(self) -> bool:
        """Whether this operating point clears the bar this module defines
        as trustworthy (`TRUSTWORTHY_MAX_FDR` / `TRUSTWORTHY_MIN_FLAG_RATE`).
        Not a claim that the bar itself is the right one -- that's a
        judgment call stated in code so it's checkable, not asserted from
        nowhere."""
        return (
            self.false_discovery_rate <= TRUSTWORTHY_MAX_FDR
            and self.flag_rate >= TRUSTWORTHY_MIN_FLAG_RATE
        )


def evaluate(
    positive_scores: list[float], negative_scores: list[float], *, threshold: float, margin: float = 0.0
) -> ThresholdReport:
    pos_outcomes = [classify(s, threshold, margin) for s in positive_scores]
    neg_outcomes = [classify(s, threshold, margin) for s in negative_scores]
    n_pos, n_neg = len(pos_outcomes), len(neg_outcomes)
    assert n_pos and n_neg, "evaluate requires a non-empty positive and negative arm"
    return ThresholdReport(
        threshold=threshold,
        margin=margin,
        false_discovery_rate=pos_outcomes.count("gap") / n_pos,
        flag_rate=neg_outcomes.count("gap") / n_neg,
        positive_abstention_rate=pos_outcomes.count("abstain") / n_pos,
        negative_abstention_rate=neg_outcomes.count("abstain") / n_neg,
        n_positive=n_pos,
        n_negative=n_neg,
    )


def sweep(
    positive_pairs: list[BenchmarkPair],
    negative_pairs: list[BenchmarkPair],
    *,
    thresholds: list[float],
    margins: list[float] = (0.0,),
    embedder: Embedder | None = None,
) -> list[ThresholdReport]:
    """Score both arms once, then classify at every (threshold, margin) in
    the grid. One embedder fit for the whole sweep -- the scores don't change
    per threshold, only how they're read."""
    embedder = _fit_shared_embedder(positive_pairs, negative_pairs, embedder)
    positive_scores = score_pairs(positive_pairs, embedder)
    negative_scores = score_pairs(negative_pairs, embedder)
    return [
        evaluate(positive_scores, negative_scores, threshold=t, margin=m)
        for m in margins
        for t in thresholds
    ]


def _print_report(reports: list[ThresholdReport]) -> None:
    header = f"{'margin':>7} {'threshold':>9} {'FDR':>6} {'flag_rate':>9} {'abstain(+)':>10} {'abstain(-)':>10} {'trustworthy':>11}"
    print(header)
    print("-" * len(header))
    for r in reports:
        print(
            f"{r.margin:7.2f} {r.threshold:9.2f} {r.false_discovery_rate:6.2f} "
            f"{r.flag_rate:9.2f} {r.positive_abstention_rate:10.2f} {r.negative_abstention_rate:10.2f} "
            f"{'yes' if r.trustworthy else 'no':>11}"
        )


def main() -> None:
    positive, negative = load_fixture()
    # Finer resolution below 0.05 -- that's where this corpus's scores are
    # dense enough for a 1-2% threshold move to matter (see README).
    thresholds = [round(0.01 * i, 2) for i in range(6)] + [round(0.05 * i, 2) for i in range(2, 11)]
    reports = sweep(positive, negative, thresholds=thresholds, margins=[0.0, 0.02])

    print(f"positive arm (known paper<->repo links): n={len(positive)}")
    print(f"negative arm (paper<->unrelated repo):    n={len(negative)}")
    print()
    _print_report(reports)

    trustworthy = [r for r in reports if r.trustworthy]
    print()
    if trustworthy:
        best = min(trustworthy, key=lambda r: r.false_discovery_rate)
        print(
            f"Trustworthy operating point exists: margin={best.margin}, threshold={best.threshold} "
            f"(FDR={best.false_discovery_rate:.2f}, flag_rate={best.flag_rate:.2f})"
        )
    else:
        print(
            f"No operating point in this grid clears FDR<={TRUSTWORTHY_MAX_FDR} "
            f"and flag_rate>={TRUSTWORTHY_MIN_FLAG_RATE}."
        )


if __name__ == "__main__":
    main()
