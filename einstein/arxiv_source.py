"""arXiv e-print (LaTeX source) fetch -> extracted equations + limitations/future-work.

An abstract tells you a paper exists; it does not tell you the mechanics --
the actual Hamiltonian, unitary, or cost function a method is built on, or
what the authors themselves flagged as a limitation or left for future work.
None of that is in the metadata `arxiv_fetcher.fetch_papers` (einstein-3)
returns. This module pulls the `/e-print/` tarball for a given arXiv ID,
extracts whatever `.tex` files it contains, and isolates two things from the
raw LaTeX text: display-math blocks (`equation`/`align`/`gather`/...
environments and `\\[ \\]` blocks), and the content of any section or
subsection whose heading mentions "limitation" or "future work".

This is text extraction, not typesetting. The equations returned are the raw
LaTeX source of each block (macros and all), not rendered math, and the
heading match is a keyword regex, not a semantic classifier -- a paper that
buries its caveats in a paragraph with no heading, or spells its limitations
section "Caveats", will not be found by this. That is a real coverage gap in
what this module can see, not a bug in the regex; a caller building a claim
on top of "no limitations section found" needs to carry that caveat forward,
same as every other absence this repo detects.

Not every arXiv submission has TeX source: very old papers, or ones the
author uploaded as PDF-only, don't. That is a fact about the source, not a
bug here -- `extract_source` raises `NoTexSourceError` (a subclass of
`ArxivSourceError`) rather than returning an empty result, so "this paper has
no source" is never silently indistinguishable from "this paper's source has
zero equations".
"""

from __future__ import annotations

import gzip
import io
import logging
import re
import tarfile
from dataclasses import dataclass
from typing import Any, Protocol

import requests

logger = logging.getLogger(__name__)

EPRINT_URL_TEMPLATE = "https://export.arxiv.org/e-print/{arxiv_id}"
DEFAULT_TIMEOUT = 60

_MATH_ENV_NAMES = (
    "equation*",
    "equation",
    "align*",
    "align",
    "alignat*",
    "alignat",
    "gather*",
    "gather",
    "multline*",
    "multline",
    "eqnarray*",
    "eqnarray",
    "displaymath",
)
_MATH_ENV_PATTERN = re.compile(
    r"\\begin\{(" + "|".join(re.escape(name) for name in _MATH_ENV_NAMES) + r")\}"
    r"(.*?)\\end\{\1\}",
    re.DOTALL,
)
_BRACKET_MATH_PATTERN = re.compile(r"\\\[(.*?)\\\]", re.DOTALL)
_DOLLAR_MATH_PATTERN = re.compile(r"(?<!\$)\$\$(.*?)\$\$(?!\$)", re.DOTALL)

# Matches a \section/\subsection/\subsubsection heading and everything up to
# the next heading of any of those three levels (or \end{document}). Not
# nesting-aware -- a \subsection under the matched heading is swallowed into
# its text, which is what we want ("the Limitations section" means the whole
# section, subsections included).
_HEADING_PATTERN = re.compile(
    r"\\(?:sub){0,2}section\*?\{([^}]*)\}(.*?)"
    r"(?=\\(?:sub){0,2}section\*?\{|\\end\{document\}|\Z)",
    re.DOTALL,
)
_LIMITATIONS_RE = re.compile(r"limitation", re.IGNORECASE)
_FUTURE_WORK_RE = re.compile(r"future\s+(work|direction)", re.IGNORECASE)

# A heuristic tag on the raw LaTeX of an equation, not a parse of its
# meaning -- "contains a symbol commonly used for X", nothing stronger.
_EQUATION_TAG_PATTERNS = {
    "hamiltonian": re.compile(r"\\hat\{?H\}?|\\mathcal\{H\}|\bH\s*=|\bhamiltonian\b", re.I),
    "unitary": re.compile(r"\\hat\{?U\}?|\\mathcal\{U\}|\bU\s*=|\bunitary\b", re.I),
    "cost_function": re.compile(
        r"\\mathcal\{L\}|\\mathcal\{C\}|\bcost\b|\bloss\b|\bobjective\b", re.I
    ),
}


class ArxivSourceError(Exception):
    """Raised when fetching or extracting an e-print source fails outright."""

    def __init__(
        self, message: str, *, arxiv_id: str, cause: BaseException | None = None
    ) -> None:
        self.arxiv_id = arxiv_id
        if cause is not None:
            self.__cause__ = cause
        super().__init__(message)


class NoTexSourceError(ArxivSourceError):
    """The e-print exists but carries no `.tex` source (PDF-only submission).

    Distinct from a paper whose source was parsed but happened to contain no
    display-math equations -- that is `SourceExtraction.equations == ()`,
    a measured zero. This is "we could not look", not "we looked and found
    none", same distinction `ArxivFetchError` draws for a failed search vs.
    a genuine zero-results one.
    """


class _HttpClient(Protocol):
    """Just enough of `requests`' surface to fetch and to fake in tests."""

    def get(self, url: str, **kwargs: Any) -> requests.Response: ...


@dataclass(frozen=True, slots=True)
class Equation:
    """One display-math block, in its raw LaTeX form.

    `tags` is a heuristic keyword match against `latex` (see
    `_EQUATION_TAG_PATTERNS`) -- e.g. `("hamiltonian",)` -- and may be empty;
    an empty tuple means no keyword matched, not "this equation is
    uninteresting".
    """

    latex: str
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Section:
    """One heading and the raw LaTeX text under it, up to the next heading."""

    heading: str
    text: str


@dataclass(frozen=True, slots=True)
class SourceExtraction:
    """Everything pulled from one paper's LaTeX source.

    `tex_filenames` is the `.tex` members found in the e-print (for
    provenance -- multi-file sources are common). `equations`,
    `limitations`, and `future_work` are concatenated across all of them in
    file order.
    """

    arxiv_id: str
    tex_filenames: tuple[str, ...]
    equations: tuple[Equation, ...]
    limitations: tuple[Section, ...]
    future_work: tuple[Section, ...]


def fetch_eprint_bytes(arxiv_id: str, *, http: _HttpClient | None = None) -> bytes:
    """Download the raw `/e-print/` payload for `arxiv_id`.

    Raises `ArxivSourceError` for any non-200 response. `http` defaults to
    the `requests` module; pass a fake with a `.get` method to test without
    the network.
    """
    client: _HttpClient = http if http is not None else requests
    url = EPRINT_URL_TEMPLATE.format(arxiv_id=arxiv_id)
    response = client.get(url, timeout=DEFAULT_TIMEOUT)

    if response.status_code != 200:
        message = f"e-print fetch failed: {response.status_code} for arxiv_id={arxiv_id!r}"
        logger.error(message)
        raise ArxivSourceError(message, arxiv_id=arxiv_id)

    return response.content


def _extract_tex_files(raw: bytes, *, arxiv_id: str) -> dict[str, str]:
    """Best-effort unwrap of an e-print payload into `{filename: tex_text}`.

    arXiv e-prints show up in three shapes: a gzip-compressed tar of
    multiple files (the common case for any paper with figures/bib/multiple
    .tex files), a bare gzip-compressed single `.tex` file (simple
    single-file submissions), or a gzip-wrapped PDF (submissions with no TeX
    source at all). We try tar first, fall back to a single gzip'd file, and
    raise `NoTexSourceError` for a PDF or for a tarball with no `.tex`
    members.
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            files: dict[str, str] = {}
            for member in tar.getmembers():
                if not member.isfile() or not member.name.endswith(".tex"):
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                files[member.name] = extracted.read().decode("utf-8", errors="replace")
        if not files:
            raise NoTexSourceError(
                f"e-print tarball for {arxiv_id!r} contains no .tex files", arxiv_id=arxiv_id
            )
        return files
    except tarfile.ReadError:
        pass  # not a tarball -- fall through to single-file handling

    try:
        decompressed = gzip.decompress(raw)
    except OSError as exc:
        raise ArxivSourceError(
            f"e-print payload for {arxiv_id!r} is not a gzip-compressed file",
            arxiv_id=arxiv_id,
            cause=exc,
        ) from exc

    if decompressed.lstrip().startswith(b"%PDF"):
        raise NoTexSourceError(
            f"e-print for {arxiv_id!r} is PDF-only, no LaTeX source available", arxiv_id=arxiv_id
        )
    return {f"{arxiv_id}.tex": decompressed.decode("utf-8", errors="replace")}


def _tag_equation(latex: str) -> tuple[str, ...]:
    return tuple(tag for tag, pattern in _EQUATION_TAG_PATTERNS.items() if pattern.search(latex))


def _find_equations(tex: str) -> list[Equation]:
    equations = []
    for match in _MATH_ENV_PATTERN.finditer(tex):
        body = match.group(2).strip()
        if body:
            equations.append(Equation(latex=body, tags=_tag_equation(body)))
    for pattern in (_BRACKET_MATH_PATTERN, _DOLLAR_MATH_PATTERN):
        for match in pattern.finditer(tex):
            body = match.group(1).strip()
            if body:
                equations.append(Equation(latex=body, tags=_tag_equation(body)))
    return equations


def _find_sections(tex: str, keyword_pattern: re.Pattern[str]) -> list[Section]:
    sections = []
    for match in _HEADING_PATTERN.finditer(tex):
        heading = match.group(1).strip()
        if keyword_pattern.search(heading):
            sections.append(Section(heading=heading, text=match.group(2).strip()))
    return sections


def extract_source(arxiv_id: str, *, http: _HttpClient | None = None) -> SourceExtraction:
    """Fetch and parse the LaTeX source for `arxiv_id`.

    Raises `NoTexSourceError` if the e-print has no `.tex` source (PDF-only),
    and `ArxivSourceError` for any other fetch/decompress failure. Equations
    and limitations/future-work sections found across all `.tex` files in
    the source are concatenated in file order -- callers do not need to know
    a paper's source was multi-file.
    """
    raw = fetch_eprint_bytes(arxiv_id, http=http)
    tex_files = _extract_tex_files(raw, arxiv_id=arxiv_id)

    equations: list[Equation] = []
    limitations: list[Section] = []
    future_work: list[Section] = []
    for tex in tex_files.values():
        equations.extend(_find_equations(tex))
        limitations.extend(_find_sections(tex, _LIMITATIONS_RE))
        future_work.extend(_find_sections(tex, _FUTURE_WORK_RE))

    if not equations:
        logger.info("no equations found in e-print source for arxiv_id=%r", arxiv_id)

    return SourceExtraction(
        arxiv_id=arxiv_id,
        tex_filenames=tuple(sorted(tex_files)),
        equations=tuple(equations),
        limitations=tuple(limitations),
        future_work=tuple(future_work),
    )
