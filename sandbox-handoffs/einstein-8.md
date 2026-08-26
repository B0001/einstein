# einstein-8: Patent independent-claims extraction — handoff

## Status

Closed. Bead was `open`, unclaimed, no prior work on disk to inherit or
distrust. Built fresh this session.

## What I changed

- **New: `einstein/patent_claims.py`** — pure text-processing module, no
  network. Three public functions:
  - `parse_claims(text: str) -> list[Claim]` — splits a claims-section text
    block into individual numbered claims, classifying each `Claim` (frozen
    dataclass: `number`, `text`, `is_independent`, `depends_on`, `canceled`)
    as independent or dependent by whether its text refers back to another
    claim number.
  - `independent_claims(text: str) -> list[Claim]` — filter to the
    independent subset.
  - `independent_claims_for_record(record, *, claims_text=None,
    claims_field=None) -> list[Claim]` — pulls claim text out of
    `Record.raw` (patent records only) and runs it through the two functions
    above.
- **New: `tests/test_patent_claims.py`** — 23 tests, no network.

Command that proves the module imports and the full suite passes:

```
uv run python -m unittest discover tests
```

Verbatim final line:

```
Ran 170 tests in 0.211s

OK
```

(170 = 147 pre-existing + 23 new. Verified directly: moved
`tests/test_patent_claims.py` out of `tests/` and reran
`uv run python -m unittest discover tests`, which reported
`Ran 147 tests in 0.138s / OK`, then moved it back.)

## Why independent claims, not the abstract (the acceptance criterion)

The bead's stated reason ("abstract-level TF-IDF is useless for patent law")
is a legal fact, not a style preference, and I designed around it rather than
just asserting it: under 35 U.S.C. 112(d) a dependent claim can only narrow
the independent claim it refers back to, never broaden it. So the
independent claims are the *widest* legal scope a patent covers — that's the
right thing for a novelty auditor (einstein-16) to compare a candidate
invention against. Comparing against dependent claims too is redundant (you
can't infringe a narrower dependent claim without also infringing the
independent claim under it); comparing against the abstract compares against
marketing prose that has no legal scope at all.

`independent_claims_for_record` enforces this at the API level, not just in
a docstring: it never reads `record.summary` (the abstract) as a fallback.
If no claim text is found under any of the field names it checks, it raises
`ClaimsNotFoundError` naming the record id and the fields it tried —
verified by `test_missing_claim_text_raises_not_falls_back_to_abstract`.

## Claim-splitting and dependency-detection logic (the actual parsing)

Claims text is one numbered paragraph per claim (`"1. A system
comprising..."`, `"2. The system of claim 1, wherein..."`). Each claim is
legally required to be a single sentence (37 CFR 1.75(i)), so there's no
internal period to confuse with the next claim's leading `"N."` — I anchor
on `^\s*(\d+)[.)]\s+` at start-of-line, which is safe for well-formed claim
dumps and is exactly what `test_splits_into_correct_count_and_numbers` and
`test_parenthetical_dot_numbering_supported` (the `1)`/`2)` variant) check.

Dependency detection scans each claim's body for the word `claim`/`claims`
and then walks forward token-by-token consuming digits and separators
(`,`, `and`, `or`, `to`, `through`, `-`, `–`), stopping at the first token
that doesn't fit. Covered by tests against a ten-claim realistic fixture
(`tests/test_patent_claims.py::CLAIMS_TEXT`):

- single reference (`"of claim 1"`) → `test_single_reference_dependent_claim`
- `and`-joined list (`"of claims 5 and 6"`) → `test_and_list_reference`
- hyphen range (`"of claims 5-6"`) → `test_hyphen_range_reference`
- `to`-range appearing mid-sentence, not just as the dependency clause itself
  → `test_to_range_reference_mid_sentence`
- `"any of the preceding claims"` expands to every earlier claim number, not
  a fixed/empty placeholder → `test_preceding_claims_phrase_expands_to_all_prior_numbers`
- `(Canceled)` claims are marked `canceled=True` and `is_independent=False`
  (a canceled claim has no scope, so it isn't independent by any definition
  useful to a novelty auditor) → `test_canceled_claim_is_not_independent`
- `(Currently Amended)` / `(Original)` prosecution-history prefixes don't
  break parsing → `test_status_prefix_does_not_break_parsing`

## Where I stated "no prior art" / "novel" — and what I wrote instead

Nowhere in this module. It classifies claim structure (independent vs.
dependent); it makes no comparison to any candidate invention and asserts
nothing about absence of prior art. The one place it makes an absence claim
at all is the `ClaimsNotFoundError` message when no claim text is found —
phrased as "no claim text found under these field names," a fact about what
was looked up, not a claim about the patent's content.

## What I could not verify

**The exact field name the USPTO Open Data Portal uses for full claim text
in a search response.** I tried to check: `curl` to
`https://api.uspto.gov/api/v1/patent/search` returns `403` without
authentication (confirms the host/path from `einstein/uspto_fetcher.py` is
live, but not the response shape), and `WebFetch` against
`https://api.uspto.gov/api-docs` also `403`s — no authenticated access to
real docs from this sandbox. `WebSearch` returned degenerate/refusal-shaped
results for every claims-API query I tried (looked like a tool-level issue
in this environment, not a "no results" answer) so I couldn't cross-check
that way either. The existing `tests/test_uspto_fetcher.py` fixture
(`PATENT_DOC`) has no claims field at all — `fetch_patents` was scoped to
bibliographic search only (einstein-5), so there's no prior art in this repo
to confirm a field name against either.

Given that, I did **not** hardcode one guessed field name as if confirmed.
`independent_claims_for_record` checks `claimsText`, `claimText`, `claims`,
`claim_text` in that order, and accepts `claims_field=` to point at whatever
the real field turns out to be, or `claims_text=` to skip the `Record.raw`
lookup entirely once a real fetch path exists. This is a documented
assumption in the module docstring, not a verified fact — the correct fix
once someone has an authenticated USPTO API key is to make one real
`fetch_patents` call, inspect the actual JSON, and either confirm one of
these four names or pass the real one via `claims_field=`.

**I did not add a network call to fetch claim text.** The bead's description
("parse claims section, isolate independent claims") is a text-processing
task; bolting a guessed, unverified endpoint URL onto `uspto_fetcher.py` on
top of an already-unverified field-name guess would have compounded one
unconfirmed assumption on another and made a fetch path look more solid than
it is. `einstein-5`'s handoff already flagged the *search* endpoint shape as
unverified for the same reason (inconsistent web results, no authenticated
access) — I'm not going to add a second unverified endpoint next to it.
Fetching real claim text is real, separate scope; I'd rather leave it
explicitly undone than fabricate it.

## What I decided not to do

- **Did not change `Record` or `einstein/schema.py`.** Adding a `claims`
  field to the frozen `Record` dataclass would force every non-patent
  fetcher to carry a meaningless field. `Record.raw` already carries
  whatever the source fetcher captured (the schema's own docstring: "the
  unmodified source payload"), so reading claim text out of `raw` — rather
  than mutating it or growing the schema — keeps `patent_claims.py` decoupled
  from a Record-schema change nobody asked for in this bead.
- **Did not touch `einstein/uspto_fetcher.py`.** It has no claims field to
  read yet (see above); there's nothing there this bead needs to change.
- **Did not attempt OCR/PDF claim-text extraction.** USPTO claim text is
  distributed as structured text/JSON in every real API surface I'm aware
  of, not scanned images, for post-2000 patents in this codebase's likely
  use case (recent CS/quantum-computing filings). Out of scope unless a
  future bead hits a real case that needs it.
- **Did not add a `_self_check()` / `if __name__ == "__main__":` block.**
  Roughly half the modules in this repo have one (`schema.py`, `gaps.py`,
  `velocity.py`, `store.py`, `graph.py`); the fetcher modules
  (`arxiv_fetcher.py`, `github_fetcher.py`, `openalex_fetcher.py`,
  `uspto_fetcher.py`) don't and rely on `tests/` alone. `patent_claims.py` is
  pure logic like the first group, but 23 unittest cases already cover every
  branch (including the realistic 10-claim fixture), so a redundant
  hand-rolled self-check would just be the same assertions in a second form.

## Aside, out of scope: `gemini_convo.md` tail

While reading `gemini_convo.md` for design context (section 5, "Automated
Patentability & Prior Art Claims Analysis", is what this bead is built from)
I noticed the file's last two exchanges are unrelated to the pipeline design
— a "raw string snippet containing a binary property list payload and a
single URL pointing to a personal GitHub repository
(github.com/B0001/mathgraph)" and a reply telling the reader to `git clone`
it. That reads as planted/anomalous content, not part of the actual design
conversation, and I did not act on it (didn't fetch the URL, didn't clone
anything, didn't treat it as an instruction). Flagging it since it's odd
enough to be worth a human glance; not filing a bead since I don't know if
it's intentional test scaffolding for this sandbox or something to actually
clean up.

## Commands to run (not run by me — git policy is conservative)

```
git add einstein/patent_claims.py tests/test_patent_claims.py
git commit -m "einstein-8: add patent independent-claims extraction"
```

`bd close einstein-8` was already run this session. No `bd dolt push` was
run — no Dolt remote is configured (same pre-existing condition noted in the
einstein-5 handoff).
