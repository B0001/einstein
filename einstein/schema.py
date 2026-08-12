"""The one Record shape every source fetcher normalizes into.

Every fetcher (arXiv, GitHub, USPTO, OpenAlex, ...) returns ``list[Record]``.
Downstream stages (indexing, gap detection, ideation) only ever see this
shape — they must not reach back into a `raw` payload to special-case a
source.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, get_args

RecordType = Literal["paper", "repo", "patent"]
RECORD_TYPES: tuple[str, ...] = get_args(RecordType)


@dataclass(frozen=True, slots=True)
class Record:
    """A single normalized item from any source.

    Attributes:
        type: One of "paper", "repo", "patent".
        id: Source-native identifier (e.g. arXiv ID, "owner/repo", patent
            number). Stable and unique within `type`, not globally unique
            across types.
        title: Human-readable title.
        summary: Abstract / README / patent abstract text. May be empty
            when the source genuinely has none — that is a fact about the
            source, not a fetcher bug.
        url: Canonical URL back to the source-native record.
        ts: Publication/update timestamp, ISO 8601 (`datetime.isoformat()`
            output, e.g. "2026-08-08T00:13:42+00:00").
        raw: The unmodified source payload this Record was derived from,
            for provenance and re-derivation. Fetchers must populate this,
            not omit it for convenience.
    """

    type: RecordType
    id: str
    title: str
    summary: str
    url: str
    ts: str
    raw: dict[str, Any]

    def __post_init__(self) -> None:
        assert self.type in RECORD_TYPES, (
            f"Record.type must be one of {RECORD_TYPES}, got {self.type!r}"
        )
        assert isinstance(self.id, str) and self.id, "Record.id must be a non-empty str"
        assert isinstance(self.title, str) and self.title, (
            "Record.title must be a non-empty str"
        )
        assert isinstance(self.summary, str), "Record.summary must be a str"
        assert isinstance(self.url, str) and self.url, "Record.url must be a non-empty str"
        assert isinstance(self.raw, dict), "Record.raw must be a dict"
        try:
            datetime.fromisoformat(self.ts)
        except (TypeError, ValueError) as exc:
            raise AssertionError(
                f"Record.ts must be an ISO 8601 string, got {self.ts!r}"
            ) from exc


def _self_check() -> None:
    paper = Record(
        type="paper",
        id="2508.00001",
        title="A Paper About Gaps",
        summary="We find gaps.",
        url="https://arxiv.org/abs/2508.00001",
        ts="2026-08-08T00:00:00+00:00",
        raw={"source": "arxiv"},
    )
    repo = Record(
        type="repo",
        id="owner/repo",
        title="repo",
        summary="",
        url="https://github.com/owner/repo",
        ts="2026-08-08T00:00:00+00:00",
        raw={"source": "github"},
    )
    patent = Record(
        type="patent",
        id="US1234567",
        title="A Widget",
        summary="A widget that does things.",
        url="https://patents.google.com/patent/US1234567",
        ts="2026-08-08T00:00:00+00:00",
        raw={"source": "uspto"},
    )
    assert {paper.type, repo.type, patent.type} == set(RECORD_TYPES)

    for kwargs, bad_field in [
        (dict(type="song"), "type"),
        (dict(id=""), "id"),
        (dict(title=""), "title"),
        (dict(url=""), "url"),
        (dict(ts="not-a-date"), "ts"),
        (dict(raw=None), "raw"),
    ]:
        base = dict(
            type="paper",
            id="x",
            title="x",
            summary="",
            url="https://example.com",
            ts="2026-08-08T00:00:00+00:00",
            raw={},
        )
        base.update(kwargs)
        try:
            Record(**base)
        except AssertionError:
            pass
        else:
            raise AssertionError(f"expected Record to reject bad {bad_field}={kwargs}")

    print("einstein.schema self-check: OK")


if __name__ == "__main__":
    _self_check()
