"""Fetch and parse USPTO Patent Grant full-text XML (the "red book" format).

`einstein.uspto_fetcher`'s search API (the USPTO Open Data Portal's Patent
File Wrapper) has no abstract or claims-text field in its JSON response --
confirmed live, einstein-tix. What that search response *does* carry, once a
patent has actually granted, is `grantDocumentMetaData.fileLocationURI`: a
link (through one more 302 redirect to a presigned, time-limited download
URL) to that patent's entry in USPTO's long-standing Patent Grant Full-Text
bulk data product -- an XML document (DTD family `us-patent-grant-v4.x`, in
production since the early 2000s) with `<abstract>` and `<claims>` as real
text, not a PDF. The earlier assumption in `einstein/patent_claims.py` that
claim text might only exist behind a PDF download was wrong; it was never
checked against this endpoint.

This module fetches that XML and flattens `<claims>` into the plain
"N. text" one-claim-per-line convention `einstein.patent_claims.parse_claims`
expects. No translation is needed to produce it: the grant XML already
embeds each claim's number as a leading `<b>N</b>.` inside the claim's own
text, so stripping tags and collapsing whitespace yields "N. claim text"
directly. The result can be written straight into `Record.raw["claimsText"]`
-- the field `patent_claims.independent_claims_for_record` already looks for
by default -- with no further format translation on that side.

Deliberately not fetched or parsed here: `<description>` (the specification
body). Nothing downstream reads it, and it can run to hundreds of kilobytes
per patent for no current consumer.

This is a separate, opt-in enrichment step, not folded into
`uspto_fetcher.fetch_patents` -- fetching it costs one extra network
round-trip (two, counting the redirect) per patent against the same
rate-limited/quota'd key, for data most callers of a bare search don't need.
A still-pending application has no grant document at all, so this only ever
applies to patents that have actually granted.
"""

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from typing import Any, Protocol

import requests

from einstein.schema import Record

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30


class GrantTextError(Exception):
    """Raised when a patent's grant full-text XML cannot be fetched or parsed."""


class _HttpClient(Protocol):
    """Just enough of `requests`' surface to fetch and to fake in tests."""

    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


@dataclass(frozen=True, slots=True)
class GrantText:
    """Abstract and flattened claims text parsed from one grant XML document.

    Either field may be `""` -- a design patent, for instance, genuinely has
    no `<abstract>` element at all; that is a fact about the patent, not a
    parse failure.
    """

    abstract: str
    claims_text: str


def _resolve_api_key(api_key: str | None) -> str:
    key = api_key if api_key is not None else os.environ.get("USPTO_API_KEY")
    if not key:
        raise GrantTextError(
            "Grant full-text fetch requires an API key: set USPTO_API_KEY or "
            "pass api_key=... ."
        )
    return key


def _flatten(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return " ".join("".join(element.itertext()).split())


def _flatten_claims(root: ET.Element) -> str:
    claims_el = root.find(".//claims")
    if claims_el is None:
        return ""
    parts = [_flatten(claim) for claim in claims_el.findall("claim")]
    return "\n\n".join(part for part in parts if part)


def parse_grant_xml(xml_bytes: bytes) -> GrantText:
    """Parse one patent's grant full-text XML into abstract + claims text.

    Raises `GrantTextError` if the document doesn't parse as XML at all (a
    genuinely malformed or truncated fetch) -- a well-formed document with no
    `<abstract>` or `<claims>` element is not an error, see `GrantText`.
    """
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as exc:
        raise GrantTextError(f"could not parse grant XML: {exc}") from exc
    return GrantText(abstract=_flatten(root.find(".//abstract")), claims_text=_flatten_claims(root))


def fetch_grant_text(
    record: Record,
    *,
    api_key: str | None = None,
    http: _HttpClient | None = None,
) -> GrantText:
    """Fetch and parse the grant full-text XML for one patent `Record`.

    Requires `record.raw["grantDocumentMetaData"]["fileLocationURI"]` --
    present only once `record` has actually granted (from
    `uspto_fetcher.fetch_patents`'s response shape). Raises `GrantTextError`
    if that key is absent, the API key is missing, or the fetch/parse fails.

    `http` defaults to the `requests` module; pass a fake with a `.get`
    method to test without the network. The USPTO endpoint issues a 302 to a
    presigned download URL on a different host (`data.uspto.gov`) --
    `requests`' default `allow_redirects=True` follows it in one call, so no
    special redirect handling is needed here.
    """
    if record.type != "patent":
        raise ValueError(f"fetch_grant_text requires a patent Record, got type={record.type!r}")

    grant_meta = record.raw.get("grantDocumentMetaData")
    uri = grant_meta.get("fileLocationURI") if isinstance(grant_meta, dict) else None
    if not uri:
        raise GrantTextError(
            f"Record {record.id!r} has no grantDocumentMetaData.fileLocationURI in "
            "raw -- either it has not granted yet, or this Record predates "
            "that field."
        )

    key = _resolve_api_key(api_key)
    client: _HttpClient = http if http is not None else requests
    response = client.get(uri, headers={"X-API-KEY": key}, timeout=DEFAULT_TIMEOUT)
    if response.status_code != 200:
        reason = getattr(response, "reason", "")
        message = f"grant text fetch failed: {response.status_code} {reason} for {record.id!r}"
        logger.error(message)
        raise GrantTextError(message)

    return parse_grant_xml(response.content)


def record_with_grant_text(record: Record, grant_text: GrantText) -> Record:
    """A copy of `record` with claims text and a real abstract merged in.

    Rebuilds `summary` as "title + abstract" (matching the convention
    `uspto_fetcher._to_record` uses when abstract text is available), and
    adds `claimsText` to `raw` for `patent_claims.independent_claims_for_record`
    to find. If `grant_text.abstract` is empty (e.g. a design patent), the
    original title-only `summary` is kept rather than blanked.
    """
    raw = dict(record.raw)
    if grant_text.claims_text:
        raw["claimsText"] = grant_text.claims_text
    summary = " ".join(part for part in (record.title, grant_text.abstract) if part).strip()
    return replace(record, raw=raw, summary=summary or record.summary)
