"""Isolate a patent's independent claims from its claim text.

Patent claim text is what the novelty auditor (einstein-16) needs for
infringement reasoning -- not the abstract. Abstracts are marketing prose;
claims are the actual legal scope of what is protected, and under 35 U.S.C.
112(d) a dependent claim can only narrow (never broaden) the claim it refers
back to. So the independent claims are the widest-scope statements of what a
patent covers: checking a candidate invention against them is the right
comparison. Checking against every dependent claim too is redundant (you
cannot infringe a dependent claim without also infringing the independent
claim it narrows), and checking against the abstract is checking the wrong
text entirely -- exactly the "TF-IDF on abstracts is useless for patent law"
mistake this module exists to avoid.

This module does one thing: given the claims-section text of a single patent
(a block of numbered claims, each legally required to be a single sentence
per 37 CFR 1.75(i)), split it into individual claims and classify each as
independent or dependent by whether its text refers back to another claim
number ("The system of claim 1, wherein...", "... any of claims 1-3 ...",
"... any of the preceding claims ...").

What this module deliberately does NOT do: fetch claim text over the
network. `independent_claims_for_record` reads it out of `Record.raw` under
one of a few plausible field names -- the exact field the USPTO Open Data
Portal search API uses for full claim text was not confirmed against live
docs (api.uspto.gov returned 403 without authenticated access from this
sandbox, and the existing `einstein/uspto_fetcher.py` search response fixture
in tests/test_uspto_fetcher.py has no claims field at all). If the real field
name turns out to differ, pass `claims_field=` or `claims_text=` explicitly;
this function fails loudly rather than silently falling back to the abstract
when no claim text is found under any of them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from einstein.schema import Record

# Claims dumps are one numbered paragraph per claim: "1. A system
# comprising..." / "2. The system of claim 1, wherein...". Each claim is
# legally a single sentence (37 CFR 1.75(i)), so there is no internal period
# to confuse with the next claim's leading "N." -- anchoring on
# start-of-line digits followed by '.' or ')' is safe for well-formed claim
# text.
_CLAIM_START = re.compile(r"(?m)^[ \t]*(\d+)[.)][ \t]+")

_STATUS_PREFIX = re.compile(
    r"^\((?:currently amended|original|previously presented|new|withdrawn)\)\s*",
    re.IGNORECASE,
)
_CANCELED = re.compile(r"^\(\s*cancell?ed\s*\)\.?$", re.IGNORECASE)

_CLAIM_KEYWORD = re.compile(r"\bclaims?\b", re.IGNORECASE)
_REF_TOKEN = re.compile(r"\s*(\d+|,|and|or|to|through|-|–)\s*", re.IGNORECASE)
_PRECEDING_REF = re.compile(
    r"(?:any\s+(?:one\s+)?of\s+the\s+)?(?:preceding|previous)\s+claims?",
    re.IGNORECASE,
)

_DEFAULT_CLAIMS_FIELDS = ("claimsText", "claimText", "claims", "claim_text")


class ClaimsNotFoundError(ValueError):
    """Raised when text (or a Record) that should have numbered claims doesn't."""


@dataclass(frozen=True, slots=True)
class Claim:
    """One numbered claim, classified independent/dependent.

    `depends_on` is the sorted tuple of claim numbers this claim's text
    refers back to -- empty for an independent claim. "any of the preceding
    claims" expands to every claim number below this one; it does not mean
    "unknown dependency."
    """

    number: int
    text: str
    is_independent: bool
    depends_on: tuple[int, ...]
    canceled: bool = False


def _consume_ref_run(body: str, pos: int) -> list[int]:
    """Read claim numbers/ranges starting at `pos` (just after "claim(s)").

    Stops at the first token that isn't a digit or a separator (',', 'and',
    'or', 'to', 'through', '-'), e.g. "claims 1, 2 and 3 wherein" yields
    [1, 2, 3] and stops before "wherein".
    """
    numbers: list[int] = []
    pending_range_start: int | None = None
    expect_range_end = False
    i = pos
    n = len(body)
    while i < n:
        match = _REF_TOKEN.match(body, i)
        if not match:
            break
        token = match.group(1)
        if token.isdigit():
            value = int(token)
            if expect_range_end and pending_range_start is not None:
                lo, hi = pending_range_start, value
                numbers.extend(range(min(lo, hi), max(lo, hi) + 1))
                pending_range_start = None
                expect_range_end = False
            else:
                numbers.append(value)
                pending_range_start = value
        elif token.lower() in ("to", "through", "-", "–"):
            expect_range_end = True
        else:  # "and" / "or" / ","
            expect_range_end = False
        i = match.end()
    return numbers


def _find_references(body: str, claim_number: int) -> tuple[int, ...]:
    refs: set[int] = set()
    if _PRECEDING_REF.search(body):
        refs.update(range(1, claim_number))
    for keyword in _CLAIM_KEYWORD.finditer(body):
        refs.update(n for n in _consume_ref_run(body, keyword.end()) if n != claim_number)
    return tuple(sorted(refs))


def parse_claims(text: str) -> list[Claim]:
    """Split claim-section text into individual `Claim`s.

    Returns `[]` for empty/whitespace-only text -- a patent can genuinely
    have no claims text available, and that is a fact about the input, not
    an error. Raises `ClaimsNotFoundError` for non-empty text with no
    recognizable numbered-claim markers, since that means the text is not a
    claims section at all (e.g. an abstract was passed by mistake) and
    silently returning `[]` would look identical to "no claims" in that case.
    """
    if not text or not text.strip():
        return []

    starts = list(_CLAIM_START.finditer(text))
    if not starts:
        raise ClaimsNotFoundError(
            "no numbered claims found (expected lines starting '1. ', '2. ', "
            f"...) in text: {text[:80]!r}"
        )

    claims: list[Claim] = []
    for i, start in enumerate(starts):
        number = int(start.group(1))
        body_start = start.end()
        body_end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        body = text[body_start:body_end].strip()

        stripped = _STATUS_PREFIX.sub("", body).strip()
        if _CANCELED.match(stripped):
            claims.append(
                Claim(number=number, text=body, is_independent=False, depends_on=(), canceled=True)
            )
            continue

        refs = _find_references(stripped, number)
        claims.append(
            Claim(number=number, text=body, is_independent=not refs, depends_on=refs)
        )
    return claims


def independent_claims(text: str) -> list[Claim]:
    """The subset of `parse_claims(text)` with no back-reference to another claim."""
    return [claim for claim in parse_claims(text) if claim.is_independent]


def independent_claims_for_record(
    record: Record,
    *,
    claims_text: str | None = None,
    claims_field: str | None = None,
) -> list[Claim]:
    """Independent claims for a patent `Record`, read from `record.raw`.

    Looks up `claims_field` (or, if not given, each of `claimsText`,
    `claimText`, `claims`, `claim_text` in turn) in `record.raw`. Pass
    `claims_text=` to supply claim text directly and skip the `raw` lookup
    entirely -- e.g. when a caller already fetched it separately.

    Raises `ClaimsNotFoundError` if no claim text is found anywhere. Does
    NOT fall back to `record.summary` (the abstract) -- an abstract is not
    claim text, and silently substituting it would reproduce exactly the
    "abstract-level TF-IDF is useless for patent law" problem this module
    exists to fix.
    """
    if record.type != "patent":
        raise ValueError(
            f"independent_claims_for_record requires a patent Record, got type={record.type!r}"
        )

    text = claims_text
    if text is None:
        fields = (claims_field,) if claims_field else _DEFAULT_CLAIMS_FIELDS
        for field in fields:
            value = record.raw.get(field)
            if isinstance(value, str) and value.strip():
                text = value
                break

    if text is None:
        tried = (claims_field,) if claims_field else _DEFAULT_CLAIMS_FIELDS
        raise ClaimsNotFoundError(
            f"Record {record.id!r} has no claim text under any of {tried!r} in "
            "raw; pass claims_text= explicitly if you have it from elsewhere. "
            "Not falling back to record.summary (the abstract) -- that would "
            "silently reintroduce the abstract-vs-claims mistake this module "
            "exists to avoid."
        )

    return independent_claims(text)
