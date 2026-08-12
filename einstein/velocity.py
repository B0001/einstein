"""Gap dedup against the seen-gaps table, plus temporal velocity scoring.

Two independent temporal signals, both derived entirely from what `Store`
(einstein-2) and `detect_gaps` (einstein-11) already persist -- no new
fetching, no new API surface:

- **Dormancy**: how long a gap's `gap_key` has persisted in the `gaps`
  table, in days, from `first_seen` to `now`. gemini_convo.md's own example:
  "a paper with 0 repos for 2 years is a dormant gap worth targeting."
- **Activity**: whether the nearest repo match for a `true_invention_gap` /
  `open_source_disruption_target` has grown *more* similar, run over run --
  i.e. whether something in the indexed repo corpus is converging toward
  this paper, even though it has not yet crossed `repo_threshold`. This is
  a proxy for gemini_convo.md's literal "5 commits yesterday" example, not
  the thing itself: `github_fetcher.py` calls the repository-*search*
  endpoint, not the commits API, so no per-repo commit timeline exists
  anywhere in this pipeline to score directly. A real commit-velocity
  signal is a new fetcher and a new bead, not an inference this module can
  make from data it does not have -- see the module docstring's own
  standard: don't invent precision you didn't measure.

Dedup ("prevents regenerating last week's ideas") falls out of the same
diff: a gap is `is_new` exactly when its `gap_key` was absent from the
store *before this run's detections are upserted*. Age and similarity delta
are only meaningful for gaps that are not new -- there is nothing to diff
a first sighting against, so both are `None`, never coerced to 0.

None of this reclassifies a `Gap`'s `kind` or claims novelty. `trend` is a
second, independent score layered on top for a caller (the ideator) to
prioritize with. "dormant" does not mean "more real" -- it means "unopposed
longer, by everything this index has seen so far in this store". A gap
that is `is_new` has no trend evidence yet; that is "unknown", not "not
dormant".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, get_args

from einstein.gaps import Gap, Match
from einstein.store import Store

DORMANT_AGE_DAYS = 730.0  # ~2 years -- gemini_convo.md's own dormancy example
RISING_SIMILARITY_EPSILON = 1e-9  # any measurable increase counts as rising

VelocityTrend = Literal["new", "rising", "dormant", "stable"]
VELOCITY_TRENDS: tuple[str, ...] = get_args(VelocityTrend)


@dataclass(frozen=True, slots=True)
class ScoredGap:
    """A `Gap` plus its temporal score against the seen-gaps table.

    `age_days` / `similarity_delta` are `None` exactly when there is
    nothing to diff against yet: a brand-new gap, or a prior payload with
    no repo `Match` recorded (e.g. `unformalized_code`, which never carries
    one -- its evidence is a paper match, not a repo match).
    """

    gap: Gap
    is_new: bool
    age_days: float | None
    similarity_delta: float | None
    trend: VelocityTrend

    def __post_init__(self) -> None:
        assert self.trend in VELOCITY_TRENDS, self.trend
        assert self.is_new == (self.trend == "new"), (self.is_new, self.trend)
        assert self.is_new == (self.age_days is None), "age_days is only known for non-new gaps"


def snapshot_gaps(store: Store) -> dict[str, dict]:
    """The seen-gaps table's state *before* this run's upserts.

    Call this first, pass the result to `score_gaps` as `previous`, and
    only then upsert the newly detected gaps into `store`. Upserting first
    would make every gap look like it was always there, at `age_days == 0`
    against itself.
    """
    return {row["gap_key"]: row for row in store.all_gaps()}


def score_gaps(
    previous: dict[str, dict],
    gaps: list[Gap],
    *,
    now: str,
    dormant_age_days: float = DORMANT_AGE_DAYS,
) -> list[ScoredGap]:
    """Score `gaps` against `previous` (see `snapshot_gaps`).

    `now` is an ISO 8601 timestamp, injected rather than read from the
    clock so this stays deterministic and network/clock-free for tests --
    the same pattern `Store`'s own `clock` parameter establishes.

    A gap is `"rising"` when its nearest repo similarity grew since the
    prior payload -- that check runs before the dormancy check, because
    rising activity is evidence someone is already closing the gap
    regardless of how long it has sat open; `"dormant"` only applies to
    gaps with no such evidence. Otherwise a non-new gap past
    `dormant_age_days` is `"dormant"`, and anything younger is `"stable"`.
    """
    now_dt = _parse_ts(now)
    scored: list[ScoredGap] = []
    for gap in gaps:
        prior = previous.get(gap.gap_key)
        if prior is None:
            scored.append(
                ScoredGap(gap=gap, is_new=True, age_days=None, similarity_delta=None, trend="new")
            )
            continue

        age_days = (now_dt - _parse_ts(prior["first_seen"])).total_seconds() / 86400.0
        similarity_delta = _repo_similarity_delta(gap, prior["payload"])

        trend: VelocityTrend
        if similarity_delta is not None and similarity_delta > RISING_SIMILARITY_EPSILON:
            trend = "rising"
        elif age_days >= dormant_age_days:
            trend = "dormant"
        else:
            trend = "stable"

        scored.append(
            ScoredGap(gap=gap, is_new=False, age_days=age_days, similarity_delta=similarity_delta, trend=trend)
        )
    return scored


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _repo_match(gap: Gap) -> Match | None:
    return next((m for m in gap.matches if m.against_type == "repo" and m.best_id is not None), None)


def _repo_similarity_delta(gap: Gap, prior_payload: dict) -> float | None:
    current = _repo_match(gap)
    if current is None:
        return None
    prior_similarity = next(
        (
            m["best_similarity"]
            for m in prior_payload.get("matches", [])
            if m.get("against_type") == "repo" and m.get("best_id") is not None
        ),
        None,
    )
    if prior_similarity is None:
        return None
    return current.best_similarity - prior_similarity


def _self_check() -> None:
    import tempfile
    from pathlib import Path

    from einstein.gaps import Match

    def make_gap(kind, subject_id, repo_similarity) -> Gap:
        repo_match = Match(against_type="repo", best_id="someone/x", best_similarity=repo_similarity, threshold=0.2)
        patent_match = Match(against_type="patent", best_id=None, best_similarity=0.0, threshold=0.2)
        return Gap(
            kind=kind,
            subject_type="paper",
            subject_id=subject_id,
            subject_title="A Paper",
            matches=(repo_match, patent_match),
        )

    ticks = iter(
        [
            "2026-08-08T00:00:00+00:00",  # store construction (unused clock call slot safety)
        ]
    )
    with tempfile.TemporaryDirectory() as tmp:
        with Store(Path(tmp) / "einstein.db", clock=lambda: next(ticks, "2026-08-08T00:00:00+00:00")) as store:
            gap_v1 = make_gap("true_invention_gap", "p1", 0.05)

            # Run 1: never seen before -> new, no age/delta.
            previous = snapshot_gaps(store)
            scored = score_gaps(previous, [gap_v1], now="2026-08-08T00:00:00+00:00")
            assert scored[0].is_new and scored[0].trend == "new" and scored[0].age_days is None, scored[0]
            store.upsert_gap(gap_v1.gap_key, gap_v1.kind, gap_v1.to_payload())

            # Run 2, ~2.1 years later, same similarity -> dormant.
            gap_v2 = make_gap("true_invention_gap", "p1", 0.05)
            previous = snapshot_gaps(store)
            scored = score_gaps(previous, [gap_v2], now="2028-09-15T00:00:00+00:00")
            assert not scored[0].is_new, scored[0]
            assert scored[0].trend == "dormant", scored[0]
            assert scored[0].similarity_delta == 0.0, scored[0]
            assert scored[0].age_days > DORMANT_AGE_DAYS, scored[0]
            store.upsert_gap(gap_v2.gap_key, gap_v2.kind, gap_v2.to_payload())

            # Run 3, similarity climbed -> rising, even though still "old".
            gap_v3 = make_gap("true_invention_gap", "p1", 0.15)
            previous = snapshot_gaps(store)
            scored = score_gaps(previous, [gap_v3], now="2028-09-16T00:00:00+00:00")
            assert scored[0].trend == "rising", scored[0]
            assert abs(scored[0].similarity_delta - 0.10) < 1e-9, scored[0]

    print("einstein.velocity self-check: OK")


if __name__ == "__main__":
    _self_check()
