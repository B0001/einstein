"""Tests for einstein.arxiv_source: LaTeX e-print extraction, no network.

Acceptance criterion for einstein-7 ("for one known paper, extracted equation
count > 0") is `EquationExtractionTest.test_known_paper_yields_equations`
below: a fixture tarball built from a real published equation (the scaled
dot-product attention formula, arXiv:1706.03762 "Attention Is All You
Need"), fed through `extract_source` via a fake HTTP client.
"""

import gzip
import io
import tarfile
import unittest

from einstein.arxiv_source import (
    ArxivSourceError,
    NoTexSourceError,
    extract_source,
    fetch_eprint_bytes,
)

# A real equation from arXiv:1706.03762 ("Attention Is All You Need"), plus a
# Limitations-style heading and a Future Work heading -- structurally
# faithful to how arXiv LaTeX sources are actually laid out.
ATTENTION_TEX = r"""
\documentclass{article}
\begin{document}

\section{Model}

The attention function is computed as:

\begin{equation}
\mathrm{Attention}(Q, K, V) = \mathrm{softmax}\left(\frac{QK^T}{\sqrt{d_k}}\right)V
\end{equation}

We also define the Hamiltonian-inspired energy term:
\[
\hat{H} = -\sum_i J_{ij} \sigma_i \sigma_j
\]

\section{Limitations}

This model does not handle sequences longer than the context window, and
training cost scales quadratically with sequence length.

\subsection{Future Work}

Sparse attention variants are left for future work.

\end{document}
"""

NO_MATH_TEX = r"""
\documentclass{article}
\begin{document}
\section{Introduction}
Just prose, no display math anywhere in this file.
\end{document}
"""


def _tar_gz_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in files.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class FakeHttp:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FetchEprintBytesTest(unittest.TestCase):
    def test_returns_raw_content_on_200(self):
        payload = _tar_gz_bytes({"paper.tex": ATTENTION_TEX})
        http = FakeHttp(FakeResponse(200, payload))

        result = fetch_eprint_bytes("1706.03762", http=http)

        self.assertEqual(result, payload)
        self.assertEqual(http.calls[0][0], "https://export.arxiv.org/e-print/1706.03762")

    def test_non_200_raises_arxiv_source_error(self):
        http = FakeHttp(FakeResponse(500, b""))

        with self.assertRaises(ArxivSourceError) as ctx:
            fetch_eprint_bytes("1706.03762", http=http)
        self.assertEqual(ctx.exception.arxiv_id, "1706.03762")


class EquationExtractionTest(unittest.TestCase):
    def test_known_paper_yields_equations(self):
        """einstein-7 acceptance: for one known paper, extracted equation count > 0."""
        payload = _tar_gz_bytes({"paper.tex": ATTENTION_TEX})
        http = FakeHttp(FakeResponse(200, payload))

        extraction = extract_source("1706.03762", http=http)

        self.assertGreater(len(extraction.equations), 0)
        self.assertEqual(extraction.arxiv_id, "1706.03762")
        self.assertEqual(extraction.tex_filenames, ("paper.tex",))

    def test_equations_are_tagged_by_heuristic_keyword(self):
        payload = _tar_gz_bytes({"paper.tex": ATTENTION_TEX})
        http = FakeHttp(FakeResponse(200, payload))

        extraction = extract_source("1706.03762", http=http)

        tags = {tag for eq in extraction.equations for tag in eq.tags}
        self.assertIn("hamiltonian", tags)

    def test_limitations_and_future_work_sections_isolated(self):
        payload = _tar_gz_bytes({"paper.tex": ATTENTION_TEX})
        http = FakeHttp(FakeResponse(200, payload))

        extraction = extract_source("1706.03762", http=http)

        self.assertEqual(len(extraction.limitations), 1)
        self.assertIn("context window", extraction.limitations[0].text)
        self.assertEqual(len(extraction.future_work), 1)
        self.assertIn("Sparse attention", extraction.future_work[0].text)

    def test_no_math_source_yields_zero_equations_not_an_error(self):
        payload = _tar_gz_bytes({"paper.tex": NO_MATH_TEX})
        http = FakeHttp(FakeResponse(200, payload))

        extraction = extract_source("0000.00000", http=http)

        self.assertEqual(extraction.equations, ())

    def test_multi_file_source_concatenates_in_file_order(self):
        payload = _tar_gz_bytes({"a_intro.tex": NO_MATH_TEX, "b_model.tex": ATTENTION_TEX})
        http = FakeHttp(FakeResponse(200, payload))

        extraction = extract_source("1706.03762", http=http)

        self.assertEqual(extraction.tex_filenames, ("a_intro.tex", "b_model.tex"))
        self.assertGreater(len(extraction.equations), 0)


class NoTexSourceTest(unittest.TestCase):
    def test_pdf_only_submission_raises_no_tex_source_error(self):
        pdf_bytes = gzip.compress(b"%PDF-1.4 fake pdf content")
        http = FakeHttp(FakeResponse(200, pdf_bytes))

        with self.assertRaises(NoTexSourceError) as ctx:
            extract_source("old.0001", http=http)
        self.assertEqual(ctx.exception.arxiv_id, "old.0001")
        self.assertIsInstance(ctx.exception, ArxivSourceError)

    def test_tarball_with_no_tex_members_raises_no_tex_source_error(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"not tex"
            info = tarfile.TarInfo(name="readme.txt")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        http = FakeHttp(FakeResponse(200, buf.getvalue()))

        with self.assertRaises(NoTexSourceError):
            extract_source("1706.03762", http=http)

    def test_bare_gzip_single_tex_file_fallback(self):
        payload = gzip.compress(ATTENTION_TEX.encode("utf-8"))
        http = FakeHttp(FakeResponse(200, payload))

        extraction = extract_source("1706.03762", http=http)

        self.assertGreater(len(extraction.equations), 0)
        self.assertEqual(extraction.tex_filenames, ("1706.03762.tex",))

    def test_non_gzip_non_tar_payload_raises_arxiv_source_error(self):
        http = FakeHttp(FakeResponse(200, b"not a gzip or tar payload at all"))

        with self.assertRaises(ArxivSourceError) as ctx:
            extract_source("1706.03762", http=http)
        self.assertNotIsInstance(ctx.exception, NoTexSourceError)


if __name__ == "__main__":
    unittest.main()
