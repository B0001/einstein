"""One-time, hand-run script: harvest real API data for the einstein-0.1 gap-
detection benchmark and write it to tests/fixtures/gap_benchmark.json.

This is NOT part of the test suite (see tests/test_gap_benchmark.py, which
reads the checked-in fixture this script produces and touches no network).
Re-run this by hand only if the benchmark corpus needs to change; it hits
live arXiv and GitHub APIs and is subject to their rate limits.

POSITIVE ARM: hand-curated (arxiv_id, github "owner/repo") pairs where the
repo is the paper's own well-known reference implementation -- not searched
for or inferred, because an inferred link is exactly the kind of ambiguous
ground truth this benchmark cannot afford. Every pair here is a widely-cited,
independently-verifiable paper<->official-code link (e.g. BERT, ResNet,
CLIP). Real title/abstract text for the paper, real description/topics text
for the repo, fetched from the live APIs -- not fabricated.

NEGATIVE ARM: the same paper set, paired against real repos from domains
that share no topical vocabulary with ML/CS research (gardening, recipes,
knitting, dotfiles, ...). Pairing is `papers[i] <-> unrelated_repos[i]`, a
fixed rotation with no curation of which paper lands on which repo --
genuinely-unrelated-by-construction, not cherry-picked to be easy or hard.

Usage: uv run python scripts/harvest_gap_benchmark.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import arxiv
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from einstein.arxiv_fetcher import _to_record as arxiv_to_record
from einstein.github_fetcher import _to_record as github_to_record

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "gap_benchmark.json"

# (arxiv_id, official/canonical repo "owner/name") -- hand-verified, not searched.
POSITIVE_PAIRS = [
    ("1810.04805", "google-research/bert"),
    ("1512.03385", "KaimingHe/deep-residual-networks"),
    ("1506.02640", "pjreddie/darknet"),
    ("1506.01497", "rbgirshick/py-faster-rcnn"),
    ("1406.2661", "goodfeli/adversarial"),
    ("2006.11239", "hojonathanho/diffusion"),
    ("2304.02643", "facebookresearch/segment-anything"),
    ("2302.13971", "facebookresearch/llama"),
    ("2103.00020", "openai/CLIP"),
    ("2112.10752", "CompVis/latent-diffusion"),
    ("1603.02754", "dmlc/xgboost"),
    ("2212.04356", "openai/whisper"),
    ("2102.12092", "openai/DALL-E"),
    ("2010.11929", "google-research/vision_transformer"),
    ("1910.10683", "google-research/text-to-text-transfer-transformer"),
    ("1806.07366", "rtqichen/torchdiffeq"),
    ("1710.10903", "PetarV-/GAT"),
    ("1707.06347", "openai/baselines"),
    ("1701.07875", "martinarjovsky/WassersteinGAN"),
]

# Real repos from domains with no ML/CS-research vocabulary overlap -- for
# the negative arm. Found via GitHub topic search (cooking, gardening,
# knitting, ...) and verified to exist by a direct GET before being used --
# see scripts/harvest_gap_benchmark.py commit history for the search session.
UNRELATED_REPOS = [
    "Anduin2017/HowToCook",
    "mathiasbynens/dotfiles",
    "maybe-finance/maybe",
    "artisan-roaster-scope/artisan",
    "karlomikus/bar-assistant",
    "rampatra/wedding-website",
    "brycejohnston/awesome-agriculture",
    "alexstaplesdesign/keiths-aquascaping-website",
    "jamesmahler/knitting_parser",
    "SabakiHQ/Sabaki",
    "kriti-rai/trailista",
    "neonfuzz/svg_quilter",
    "BrianTolman/kiwi-bench",
    "engineermayur-07/Travel-Planning",
    "jovandeginste/workout-tracker",
    "kantord/LibreLingo",
    "jcallaghan/The-Cookbook",
    "hendricius/the-bread-code",
    "juftin/camply",
]


def _sleep_politely() -> None:
    time.sleep(3.0)


def fetch_positive_arm() -> list[dict]:
    client = arxiv.Client()
    pairs = []
    for arxiv_id, repo_full_name in POSITIVE_PAIRS:
        search = arxiv.Search(id_list=[arxiv_id])
        results = list(client.results(search))
        assert results, f"arXiv id {arxiv_id!r} returned no results -- dead/wrong id"
        paper_record = arxiv_to_record(results[0])
        _sleep_politely()

        resp = requests.get(
            f"https://api.github.com/repos/{repo_full_name}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=30,
        )
        assert resp.status_code == 200, f"GitHub repo {repo_full_name!r} fetch failed: {resp.status_code} {resp.text[:200]}"
        repo_record = github_to_record(resp.json())

        pairs.append(
            {
                "paper": _record_to_dict(paper_record),
                "repo": _record_to_dict(repo_record),
            }
        )
        print(f"positive: {arxiv_id} <-> {repo_full_name}  OK")
    return pairs


def fetch_negative_arm(papers: list[dict]) -> list[dict]:
    pairs = []
    repo_records = []
    for full_name in UNRELATED_REPOS:
        resp = requests.get(
            f"https://api.github.com/repos/{full_name}",
            headers={"Accept": "application/vnd.github+json"},
            timeout=30,
        )
        if resp.status_code != 200:
            print(f"negative: {full_name}  SKIP ({resp.status_code})")
            continue
        repo_records.append(_record_to_dict(github_to_record(resp.json())))
        print(f"negative repo: {full_name}  OK")

    assert len(repo_records) >= len(papers), (
        f"need at least {len(papers)} unrelated repos, only fetched {len(repo_records)} -- "
        "add more candidates to UNRELATED_REPOS"
    )

    for i, paper in enumerate(papers):
        pairs.append({"paper": paper["paper"], "repo": repo_records[i % len(repo_records)]})
    return pairs


def _record_to_dict(record) -> dict:
    return {
        "type": record.type,
        "id": record.id,
        "title": record.title,
        "summary": record.summary,
        "url": record.url,
        "ts": record.ts,
        "raw": record.raw,
    }


def main() -> None:
    print("Fetching positive arm (paper <-> its own known repo)...")
    positive = fetch_positive_arm()

    print("\nFetching negative arm (paper <-> unrelated repo)...")
    negative = fetch_negative_arm(positive)

    fixture = {
        "description": (
            "einstein-0.1 gap-detection benchmark. positive_pairs: real "
            "paper<->repo pairs that are known-linked (repo is the paper's "
            "own reference implementation) -- ground-truth NON-gaps. "
            "negative_pairs: the same papers paired against real repos from "
            "unrelated domains -- ground-truth gaps. Regenerate with "
            "scripts/harvest_gap_benchmark.py."
        ),
        "positive_pairs": positive,
        "negative_pairs": negative,
    }

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n")
    print(f"\nWrote {len(positive)} positive pairs, {len(negative)} negative pairs to {FIXTURE_PATH}")


if __name__ == "__main__":
    main()
