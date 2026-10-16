"""Calibration for `novelty_auditor.DEFAULT_THETA` (einstein-wvc).

The old default, theta=0.85, came from gemini_convo.md and was never
measured. Under TF-IDF it rejects nothing: an idea built word for word from a
paper's own title and abstract scores about 0.4-0.75 against that paper, so a
"pass" at 0.85 was close to automatic.

This harness uses the 19 real arXiv papers in `tests/fixtures/gap_benchmark.json`
(the same corpus as `einstein.gap_benchmark`, local file only) and runs every
measurement through the auditor's own paper-side scoring (`_idea_text`,
`_best_paper_match`), so it checks what ships rather than a copy of it.

Every pool is `POOL_SIZE` papers, matching the auditor's production pool
(`openalex_fetcher.search_papers` max_results=10). TF-IDF is refit on each
pool, so pool size moves the scores. Pools are drawn with `SEEDS` fixed seeds
and the rates are averaged.

- **overlap arm** (should be caught): ideas built from a paper's own text (see
  `VARIANTS`), audited against the source plus 9 other papers. A miss means
  "pass" on an idea that restates a paper in the pool.
- **novel arm** (should pass): the same ideas audited against 10 other papers,
  with the source left out. A flag means a false force_pivot.
- **neighbor arm** (should pass, hard): the same, but the pool always includes
  the paper's hand-picked same-subfield neighbor (`NEIGHBORS`), for example YOLO
  with Faster R-CNN. Production pools are search results on the idea's own
  topic, so this rate is the more realistic false force_pivot rate.

Rule: `DEFAULT_THETA` is the largest value on `GRID` whose mean catch rate on
the overlap arm is >= `TARGET_CATCH_RATE` (a stated 10% miss rate). The
false-flag rates at that value are reported next to it, never hidden.

Limits, stated rather than tuned away:
- The catch rate assumes the matching paper was retrieved. If search never
  returns it, no theta can catch it.
- The overlap ideas are verbatim excerpts. Real LLM ideas paraphrase, which
  lowers similarity, so the miss rate on real prior art is *at least* the
  one measured here.
- n=19 papers x 5 variants, one corpus. This is a sanity floor, not a
  certificate. The pick moves between neighboring grid values under
  resampling. `main()` prints per-variant rows so the spread is visible.
- Paper side only. Patent claims use the same theta, and a patent hit at
  theta is a REJECT. There is no paper<->patent arm (einstein-yy0), so the
  patent-side theta, and the reject rate it implies, are uncalibrated.

    uv run python -m einstein_agents.novelty_calibration
"""

from __future__ import annotations

import random
import re
from collections.abc import Callable
from dataclasses import dataclass

from einstein.gap_benchmark import load_fixture
from einstein.schema import Record
from einstein_agents.ideator import Idea
from einstein_agents.novelty_auditor import _best_paper_match, _idea_text

TARGET_CATCH_RATE = 0.90
GRID = tuple(round(0.05 * i, 2) for i in range(2, 18))  # 0.10 .. 0.85
POOL_SIZE = 10  # = openalex_fetcher.search_papers default max_results
SEEDS = tuple(range(20))

# Hand-picked same-subfield pairs in the fixture (symmetric).
_NEIGHBOR_PAIRS = (
    ("1506.02640v5", "1506.01497v3"),  # YOLO / Faster R-CNN
    ("2006.11239v2", "2112.10752v2"),  # DDPM / Latent Diffusion
    ("1406.2661v1", "1701.07875v3"),  # GAN / Wasserstein GAN
    ("1810.04805v2", "1910.10683v4"),  # BERT / T5
    ("2010.11929v2", "1512.03385v1"),  # ViT / ResNet
)
NEIGHBORS = {a: b for a, b in _NEIGHBOR_PAIRS} | {b: a for a, b in _NEIGHBOR_PAIRS}


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def _mid(text: str) -> str:
    sents = _sentences(text)
    return sents[len(sents) // 2]


# name -> (method, problem) built from one paper; mirrors Idea's two fields.
VARIANTS: dict[str, Callable[[Record], tuple[str, str]]] = {
    "title+first_sentence": lambda p: (p.title, _sentences(p.summary)[0]),
    "title": lambda p: (p.title, ""),
    "first_sentence": lambda p: (_sentences(p.summary)[0], ""),
    "middle_sentence": lambda p: (_mid(p.summary), ""),
    "last_sentence": lambda p: (_sentences(p.summary)[-1], ""),
}


@dataclass(frozen=True, slots=True)
class Scores:
    """Best paper-side similarity per (paper, variant, seed), per arm. Each
    entry is `(variant, score)` so rates can be split by variant."""

    overlap: list[tuple[str, float]]
    novel: list[tuple[str, float]]
    neighbor: list[tuple[str, float]]


@dataclass(frozen=True, slots=True)
class ThetaRow:
    theta: float
    catch_rate: float  # overlap arm: fraction flagged (best >= theta), source in the pool
    false_flag_rate: float  # novel arm: fraction flagged, source left out
    neighbor_false_flag_rate: float  # neighbor arm: same, pool includes a same-subfield paper


def _idea(paper: Record, method: str, problem: str) -> Idea:
    return Idea(
        gap_key=f"calibration:{paper.id}", subject_id=paper.id, subject_title=paper.title,
        method=method, problem=problem, context_equations=(), raw_response="",
    )


def _papers() -> list[Record]:
    positive, _, _ = load_fixture()
    return [pair.paper for pair in positive]


def _best(text: str, pool: list[Record]) -> float:
    return _best_paper_match(text, pool, 0.0, None).best_similarity


def score_arms(papers: list[Record] | None = None) -> Scores:
    papers = papers if papers is not None else _papers()
    by_id = {p.id: p for p in papers}
    scores = Scores([], [], [])
    for seed in SEEDS:
        rng = random.Random(seed)
        for paper in papers:
            others = [p for p in papers if p.id != paper.id]
            rng.shuffle(others)
            neighbor = by_id.get(NEIGHBORS.get(paper.id, ""))
            for name, build in VARIANTS.items():
                text = _idea_text(_idea(paper, *build(paper)))
                scores.overlap.append((name, _best(text, [paper, *others[: POOL_SIZE - 1]])))
                scores.novel.append((name, _best(text, others[:POOL_SIZE])))
                if neighbor is not None:
                    rest = [p for p in others if p is not neighbor][: POOL_SIZE - 1]
                    scores.neighbor.append((name, _best(text, [neighbor, *rest])))
    return scores


def _rate(arm: list[tuple[str, float]], theta: float, variant: str | None = None) -> float:
    vals = [s for v, s in arm if variant is None or v == variant]
    return sum(s >= theta for s in vals) / len(vals)


def sweep(scores: Scores, grid: tuple[float, ...] = GRID, variant: str | None = None) -> list[ThetaRow]:
    return [
        ThetaRow(
            theta=t,
            catch_rate=_rate(scores.overlap, t, variant),
            false_flag_rate=_rate(scores.novel, t, variant),
            neighbor_false_flag_rate=_rate(scores.neighbor, t, variant),
        )
        for t in grid
    ]


def pick_theta(rows: list[ThetaRow], target: float = TARGET_CATCH_RATE) -> ThetaRow:
    eligible = [r for r in rows if r.catch_rate >= target]
    assert eligible, f"no theta on the grid reaches catch rate {target}"
    return max(eligible, key=lambda r: r.theta)


def main() -> None:
    scores = score_arms()
    rows = sweep(scores)
    chosen = pick_theta(rows)
    print(f"{'theta':>6} {'catch':>6} {'false_flag':>10} {'neighbor_ff':>11}")
    for r in rows:
        mark = "  <- chosen" if r is chosen else ""
        print(f"{r.theta:>6.2f} {r.catch_rate:>6.3f} {r.false_flag_rate:>10.3f} {r.neighbor_false_flag_rate:>11.3f}{mark}")
    print(f"\nper variant at theta={chosen.theta:.2f}:")
    for name in VARIANTS:
        (r,) = sweep(scores, (chosen.theta,), name)
        print(f"  {name:22s} catch {r.catch_rate:.3f}  false_flag {r.false_flag_rate:.3f}  neighbor_ff {r.neighbor_false_flag_rate:.3f}")
    print(
        f"\nlargest theta with mean catch >= {TARGET_CATCH_RATE:.2f}: {chosen.theta:.2f} "
        f"(pool={POOL_SIZE}, seeds={len(SEEDS)}, n={len(_papers())} papers x {len(VARIANTS)} variants)"
    )


if __name__ == "__main__":
    main()
