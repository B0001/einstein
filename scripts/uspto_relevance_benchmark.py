"""Hand-run script for einstein-uts: measure retrieval recall of
`uspto_fetcher.fetch_patents`'s `sort=_score desc` fix (einstein-b3b) against
a broader sample of `novelty_auditor`-shaped queries than that bead's
handful of hand-picked examples -- the exact gap einstein-uts identifies as
unmeasured.

Method (documented so the numbers below can be judged, not just trusted):

1. For each TOPIC below, find one real, verifiably-on-topic granted/pending
   patent via a QUOTED exact-phrase query. Quoted `query_string` search is a
   *different* query mode from the unquoted OR-of-terms mode being tested
   (confirmed by einstein-b3b: quoting collapses a ~900k-hit unquoted count
   to a few hundred, all on-topic) -- it is not independent of the search
   engine under test, but it IS a different, well-understood matching mode,
   so "quoted search says this patent is about X" is a materially different
   claim than "the default OR mode ranked it first". This is the best
   ground-truth oracle available without hand-curating patent numbers from
   memory, which this repo's standard treats as guessing, not measurement
   (see uspto_fetcher.py's own refusal to guess at undocumented endpoints).
   This IS a real limitation -- an independently-sourced ground truth set
   (e.g. patents named in news coverage, cross-checked against USPTO by
   number) would be stronger. Not built here; flagged, not hidden.
2. Build a natural-language method paragraph paraphrasing that patent's own
   title (never copied verbatim -- copying would test phrase search, not the
   OR-of-terms path `novelty_auditor.audit_idea` actually exercises via
   `idea.method`).
3. Run that paragraph through the *real* `fetch_patents` (the exact function
   `novelty_auditor.audit_idea` defaults to) at max_results=10 -- what
   `audit_idea` actually requests, since `search_patents(idea.method)` is
   called with no override -- and again at max_results=25 for comparison.
4. Recall@k = fraction of topics whose ground-truth patent ID appears in the
   top-k results for its own paraphrase.

A second, unrelated set of DISTRACTOR paragraphs (no expected specific
conflict) is run through the same top-10 path so a human can eyeball whether
generic method text returns plausibly-relevant or obviously-arbitrary
patents -- this is not scored (there's no ground truth for "should not
match"), it is a qualitative precision spot-check, reported as such.

This is NOT part of the test suite -- it hits the live api.uspto.gov API,
requires a real USPTO_API_KEY, and reflects one day's index (results will
drift as the index changes). Sample size is small (design target ~10 topics)
-- report this as what it is, a broader-than-b3b spot check, not a
statistically powered benchmark. Runtime is dominated by live HTTP calls; a
short delay is added between requests to avoid hammering the API.

Usage: USPTO_API_KEY=... uv run python scripts/uspto_relevance_benchmark.py
"""

from __future__ import annotations

import os
import sys
import time
import urllib.parse
import urllib.request
import json

import requests

from einstein.uspto_fetcher import USPTO_SEARCH_URL, USPTOFetchError, fetch_patents

REQUEST_DELAY_SECONDS = 1.0

# Each topic: a quoted exact-phrase query used ONLY to establish ground
# truth (step 1 above), plus a paraphrase written from that result's own
# title once fetched live -- filled in at run time, not hardcoded, so this
# script never hardcodes a patent number/title from memory.
TOPICS = [
    "closed loop insulin delivery",
    "solid state lithium battery separator",
    "unmanned aerial vehicle swarm coordination",
    "CRISPR guide RNA delivery",
    "autonomous vehicle lane keeping",
    "wind turbine blade pitch control",
    "surgical robot end effector",
    "battery thermal runaway prevention",
    "natural language to SQL query translation",
    "drone package delivery landing",
    "message queue load balancing",
    "image compression neural network",
    "solar panel tracking system",
    "robotic vacuum cleaner navigation",
    "3D printing support structure",
    "voice activity detection",
    "GPS spoofing detection",
    "battery management system",
    "fingerprint sensor authentication",
    "wireless power transfer coil",
]

# Method paragraphs with no specific expected conflict -- a qualitative spot
# check only, not scored (see module docstring).
DISTRACTORS = [
    (
        "A method for scheduling household chores among multiple family "
        "members using a shared calendar and a point-based reward system "
        "redeemable for allowance."
    ),
    (
        "A method for composing short-form poetry by sampling syllable "
        "counts from a Markov chain trained on a corpus of haiku."
    ),
]


def _require_api_key() -> str:
    key = os.environ.get("USPTO_API_KEY")
    if not key:
        print("USPTO_API_KEY is not set -- get one at https://account.uspto.gov and re-run:")
        print("  USPTO_API_KEY=... uv run python scripts/uspto_relevance_benchmark.py")
        sys.exit(1)
    return key


def _quoted_ground_truth(topic: str, api_key: str) -> tuple[str, str] | None:
    """One quoted-phrase lookup -- returns (patent_id, title) of the first
    hit, or None if the phrase has zero hits in the live index today.
    """
    query = f'"{topic}"'
    params = {"q": query, "rows": "5", "sort": "_score desc"}
    url = USPTO_SEARCH_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "X-API-KEY": api_key})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    docs = payload.get("patentFileWrapperDataBag") or []
    if not docs:
        return None
    doc = docs[0]
    meta = doc.get("applicationMetaData", {}) or {}
    patent_id = meta.get("patentNumber") or doc.get("applicationNumberText")
    title = meta.get("inventionTitle", "")
    if not patent_id or not title:
        return None
    return str(patent_id), title


def _paraphrase(title: str) -> str:
    """Deterministic, mechanical paraphrase: describe it as a method
    performing the titled thing, in different surface words than the title
    itself, so this exercises the OR-of-terms path novelty_auditor's
    idea.method paragraphs exercise -- not a repeat of the exact phrase.
    """
    lowered = title.lower()
    return (
        f"A method and system for {lowered}, comprising steps that "
        f"implement the approach described by the title '{title}' using "
        f"conventional hardware and control software."
    )


def _run_recall_check(api_key: str) -> None:
    print(f"=== recall check: {len(TOPICS)} topics ===\n")
    hits_at_10 = 0
    hits_at_25 = 0
    scored = 0
    for topic in TOPICS:
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            truth = _quoted_ground_truth(topic, api_key)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  [{topic!r}] ground-truth lookup FAILED (network/timeout, not a code bug): {exc}")
            continue
        if truth is None:
            print(f"  [{topic!r}] no ground-truth patent found via quoted search today -- skipped")
            continue
        patent_id, title = truth
        paraphrase = _paraphrase(title)

        # `scored` only counts topics where BOTH fetches actually completed --
        # a transient network/timeout failure here must not silently count as
        # a "search missed it" and deflate recall (that would be measuring
        # this script's network reliability, not the search's).
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            top10 = fetch_patents(paraphrase, max_results=10, api_key=api_key)
        except (USPTOFetchError, requests.exceptions.RequestException) as exc:
            print(f"  [{topic!r}] top-10 fetch FAILED (network/timeout, not a code bug): {exc}")
            continue
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            top25 = fetch_patents(paraphrase, max_results=25, api_key=api_key)
        except (USPTOFetchError, requests.exceptions.RequestException) as exc:
            print(f"  [{topic!r}] top-25 fetch FAILED (network/timeout, not a code bug): {exc}")
            continue
        scored += 1

        ids_10 = {r.id for r in top10}
        ids_25 = {r.id for r in top25}
        hit10 = patent_id in ids_10
        hit25 = patent_id in ids_25
        hits_at_10 += int(hit10)
        hits_at_25 += int(hit25)

        print(f"  [{topic!r}]")
        print(f"    ground truth: {patent_id} {title!r}")
        print(f"    paraphrase sent: {paraphrase!r}")
        print(f"    hit@10={hit10} hit@25={hit25}")
        print(f"    top-10 titles returned:")
        for r in top10:
            print(f"      - {r.id} {r.title!r}")
        print()

    print("=== summary ===")
    if scored == 0:
        print("  no topics scored -- all quoted lookups returned zero hits today, re-run later")
        return
    print(f"  topics with a ground-truth patent: {scored}/{len(TOPICS)}")
    print(f"  recall@10: {hits_at_10}/{scored} = {hits_at_10 / scored:.0%}")
    print(f"  recall@25: {hits_at_25}/{scored} = {hits_at_25 / scored:.0%}")
    print(
        "  recall@10 is the number that matters for the live pipeline: "
        "novelty_auditor.audit_idea calls search_patents(idea.method) with "
        "no max_results override, i.e. fetch_patents's default of 10."
    )


def _run_distractor_spotcheck(api_key: str) -> None:
    print(f"\n=== distractor spot-check: {len(DISTRACTORS)} paragraphs (not scored) ===\n")
    for paragraph in DISTRACTORS:
        time.sleep(REQUEST_DELAY_SECONDS)
        try:
            top10 = fetch_patents(paragraph, max_results=10, api_key=api_key)
        except (USPTOFetchError, requests.exceptions.RequestException) as exc:
            print(f"  [{paragraph[:60]!r}...] FAILED: {exc}")
            continue
        print(f"  [{paragraph[:70]!r}...]")
        for r in top10:
            print(f"    - {r.id} {r.title!r}")
        print()


def main() -> None:
    api_key = _require_api_key()
    _run_recall_check(api_key)
    _run_distractor_spotcheck(api_key)


if __name__ == "__main__":
    sys.exit(main())
