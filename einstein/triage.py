"""Discovery triage (einstein-827): the three checks that separate a
discovery-grade experiment from a careful reproduction, run before anything
is spent.

1. **Stakes.** The question states what someone does if the answer is yes,
   what they do if it is no, and who acts. If both branches say the same
   thing, the answer changes nothing and the question is dropped
   (`no_stakes`). This checks the question is *posed* as decision-relevant;
   whether the decision matters is still a human call.
2. **Edge.** Every capability the candidate claims to have must cite a file
   that exists under `root` (`no_edge` otherwise). A capability you cannot
   point at is a hope, not an edge.
3. **Literature.** Each survivor is re-queried with several phrasings (one
   phrasing is brittle) and the union is ranked against the question.

The output is an evidence pack for a reader, never a novelty verdict. The
TF-IDF + theta gate in `novelty_auditor` effectively always passes a
one-sentence question (short text rarely reaches cosine 0.85 against an
abstract), so a mechanical "pass" is not evidence of novelty; the ranked
nearest papers are. A failed search is `search_failed` and a search that
returns nothing at all is `search_empty`, never `read`: missing evidence must
not look like an empty literature. (OpenAlex's `title_and_abstract.search`
filter, which `openalex_fetcher.search_papers` uses, ANDs every term, so a
long query returns zero hits; rephrase shorter rather than read that as open.)

`harvest` supplies item 1's raw material: the open questions the repos
already wrote down -- unchecked backlog items and bullets under "cannot
claim" / "out of scope" / "open question" headings.

    python -m einstein.triage candidates.json --root ~/Downloads [--out report.md]
    python -m einstein.triage --harvest ~/Downloads/chem/specs ...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from einstein.embedding import TfidfEmbedder
from einstein.novelty_auditor import SearchFn
from einstein.schema import Record

Status = Literal["read", "no_stakes", "no_edge", "search_failed", "search_empty"]

_SEED_HEADING = re.compile(r"cannot claim|out of scope|open question|not (yet )?done|future work", re.I)


@dataclass(frozen=True)
class Candidate:
    id: str
    question: str
    if_yes: str
    if_no: str
    who_acts: str
    edge: tuple[tuple[str, str], ...]  # (path relative to root, what it gives us)
    queries: tuple[str, ...]

    @classmethod
    def from_dict(cls, d: dict) -> "Candidate":
        return cls(
            id=d["id"], question=d["question"], if_yes=d.get("if_yes", ""),
            if_no=d.get("if_no", ""), who_acts=d.get("who_acts", ""),
            edge=tuple((e["path"], e.get("why", "")) for e in d.get("edge", [])),
            queries=tuple(d.get("queries") or [d["question"]]),
        )


@dataclass
class Result:
    candidate: Candidate
    status: Status
    reasons: list[str] = field(default_factory=list)
    nearest: list[tuple[float, Record]] = field(default_factory=list)


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def triage(candidates: list[Candidate], *, search: SearchFn, root: Path, top_k: int = 8) -> list[Result]:
    results = []
    for c in candidates:
        if not (c.if_yes.strip() and c.if_no.strip() and c.who_acts.strip()):
            results.append(Result(c, "no_stakes", ["if_yes, if_no and who_acts are all required"]))
            continue
        if _norm(c.if_yes) == _norm(c.if_no):
            results.append(Result(c, "no_stakes", ["both answers lead to the same action"]))
            continue
        missing = [p for p, _ in c.edge if not (root / p).exists()]
        if not c.edge or missing:
            results.append(Result(c, "no_edge", [f"missing: {p}" for p in missing] or ["no edge cited"]))
            continue
        records: dict[str, Record] = {}
        try:
            for q in c.queries:
                for r in search(q):
                    records.setdefault(r.id, r)
        except Exception as e:  # fail closed: no evidence is not an empty literature
            results.append(Result(c, "search_failed", [f"{type(e).__name__}: {e}"]))
            continue
        if not records:
            results.append(Result(c, "search_empty", [f"0 papers from {len(c.queries)} queries: rephrase shorter"]))
            continue
        results.append(Result(c, "read", [f"{len(records)} unique papers from {len(c.queries)} queries"],
                              _rank(c.question, list(records.values()))[:top_k]))
    return results


def _rank(question: str, records: list[Record]) -> list[tuple[float, Record]]:
    if not records:
        return []
    texts = [question] + [f"{r.title}. {r.summary}" for r in records]
    m = TfidfEmbedder().fit_transform(texts)
    m = m / np.maximum(np.linalg.norm(m, axis=1, keepdims=True), 1e-12)
    sims = m[1:] @ m[0]
    # ties broken by id so the pack is deterministic
    return sorted(zip(sims.tolist(), records), key=lambda t: (-t[0], t[1].id))


def harvest(paths: list[Path]) -> list[tuple[str, str]]:
    """(source "file:line", text) for every open question the docs already name."""
    seeds = []
    for f in sorted(p for d in paths for p in ([d] if d.is_file() else d.rglob("*.md"))):
        in_section = False
        for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            s = line.strip()
            if s.startswith("#") or (s.startswith("**") and s.endswith("**")):
                in_section = bool(_SEED_HEADING.search(s))
                continue
            if s.startswith("- [ ]"):
                seeds.append((f"{f}:{i}", s[5:].strip()))
            elif in_section and s.startswith(("- ", "* ")) or (s.startswith("- ") and _SEED_HEADING.search(s)):
                seeds.append((f"{f}:{i}", s[2:].strip()))
    return seeds


def render(results: list[Result]) -> str:
    out = ["# Discovery triage", "",
           "Evidence packs, not verdicts. `read` means: stakes and edge check out; now read the "
           "nearest papers and decide whether they already answer the question.", ""]
    for r in results:
        c = r.candidate
        out += [f"## {c.id}: `{r.status}`", "", f"**Q:** {c.question}", "",
                f"- If yes: {c.if_yes}", f"- If no: {c.if_no}", f"- Who acts: {c.who_acts}"]
        out += [f"- Edge: `{p}`: {why}" for p, why in c.edge]
        out += [f"- {x}" for x in r.reasons]
        if r.nearest:
            out += ["", "| sim | year | paper |", "|---|---|---|"]
            out += [f"| {s:.2f} | {rec.ts[:4]} | [{rec.title}]({rec.url}) |" for s, rec in r.nearest]
        out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m einstein.triage", description=__doc__.split("\n\n")[0])
    ap.add_argument("candidates", nargs="?", type=Path)
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="edge paths resolve against this")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--top-k", type=int, default=8)
    ap.add_argument("--harvest", nargs="+", type=Path, help="print seed questions from these docs and exit")
    args = ap.parse_args(argv)
    if args.harvest:
        for src, text in harvest(args.harvest):
            print(f"{src}\t{text}")
        return 0
    if args.candidates is None:
        ap.error("candidates file required unless --harvest")
    from einstein.openalex_fetcher import search_papers

    cands = [Candidate.from_dict(d) for d in json.loads(args.candidates.read_text())]
    md = render(triage(cands, search=search_papers, root=args.root.expanduser(), top_k=args.top_k))
    (args.out.write_text(md) if args.out else sys.stdout.write(md + "\n"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
