import unittest
from unittest.mock import patch

from einstein.openalex_fetcher import (
    Edge,
    OpenAlexFetchError,
    fetch_citation_edges,
    fetch_work_by_doi,
)

# Trimmed but structurally faithful shape of a real OpenAlex work response
# (https://api.openalex.org/works/doi:10.7717/peerj.4375).
WORK_ITEM = {
    "id": "https://openalex.org/W2741809807",
    "doi": "https://doi.org/10.7717/peerj.4375",
    "title": "The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles",
    "display_name": "The state of OA: a large-scale analysis of the prevalence and impact of Open Access articles",
    "publication_date": "2018-02-13",
    "abstract_inverted_index": {
        "Despite": [0],
        "growing": [1],
        "interest": [2],
        "in": [3],
        "Open": [4],
        "Access": [5],
    },
    "referenced_works": [
        "https://openalex.org/W2100121061",
        "https://openalex.org/W2144252560",
        "https://openalex.org/W1995315455",
    ],
    "cited_by_count": 400,
}

WORK_ITEM_NO_ABSTRACT_NO_REFS = {
    "id": "https://openalex.org/W9999999999",
    "doi": "https://doi.org/10.0000/bare",
    "title": "A Bare Work",
    "display_name": "A Bare Work",
    "publication_date": "2020-01-01",
    "abstract_inverted_index": None,
    "referenced_works": [],
    "cited_by_count": 0,
}


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


class FakeHttp:
    """Stands in for `requests`: records the call, returns a canned response."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class FetchWorkByDoiTest(unittest.TestCase):
    def test_maps_work_to_record(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM))
        record = fetch_work_by_doi("10.7717/peerj.4375", http=http)

        self.assertEqual(record.type, "paper")
        self.assertEqual(record.id, "W2741809807")
        self.assertEqual(record.title, WORK_ITEM["display_name"])
        self.assertEqual(record.url, "https://openalex.org/W2741809807")
        self.assertEqual(record.ts, "2018-02-13")
        self.assertEqual(record.raw, WORK_ITEM)

    def test_abstract_reconstructed_from_inverted_index(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM))
        record = fetch_work_by_doi("10.7717/peerj.4375", http=http)
        self.assertEqual(record.summary, "Despite growing interest in Open Access")

    def test_missing_abstract_yields_empty_summary(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM_NO_ABSTRACT_NO_REFS))
        record = fetch_work_by_doi("10.0000/bare", http=http)
        self.assertEqual(record.summary, "")

    def test_doi_url_form_is_accepted(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM))
        fetch_work_by_doi("https://doi.org/10.7717/peerj.4375", http=http)
        url, _ = http.calls[0]
        self.assertEqual(url, "https://api.openalex.org/works/doi:10.7717/peerj.4375")

    def test_bare_doi_builds_expected_url(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM))
        fetch_work_by_doi("10.7717/peerj.4375", http=http)
        url, _ = http.calls[0]
        self.assertEqual(url, "https://api.openalex.org/works/doi:10.7717/peerj.4375")

    def test_404_raises_and_is_flagged_not_found(self):
        http = FakeHttp(FakeResponse(404, {}))
        with self.assertRaises(OpenAlexFetchError) as ctx:
            fetch_work_by_doi("10.0000/missing", http=http)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertTrue(ctx.exception.not_found)

    def test_500_raises_and_is_not_flagged_not_found(self):
        http = FakeHttp(FakeResponse(500, {}))
        with self.assertRaises(OpenAlexFetchError) as ctx:
            fetch_work_by_doi("10.0000/x", http=http)
        self.assertFalse(ctx.exception.not_found)

    def test_no_mailto_omits_mailto_param(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM))
        with patch.dict("os.environ", {}, clear=True):
            fetch_work_by_doi("10.7717/peerj.4375", http=http)
        _, kwargs = http.calls[0]
        self.assertNotIn("mailto", kwargs["params"])

    def test_mailto_env_var_included_as_param(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM))
        with patch.dict("os.environ", {"OPENALEX_MAILTO": "me@example.com"}):
            fetch_work_by_doi("10.7717/peerj.4375", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["params"]["mailto"], "me@example.com")

    def test_mailto_argument_overrides_env_var(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM))
        with patch.dict("os.environ", {"OPENALEX_MAILTO": "env@example.com"}):
            fetch_work_by_doi("10.7717/peerj.4375", mailto="arg@example.com", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["params"]["mailto"], "arg@example.com")


class FetchCitationEdgesTest(unittest.TestCase):
    def test_seed_doi_returns_outbound_citation_edges(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM))
        edges = fetch_citation_edges("10.7717/peerj.4375", http=http)

        self.assertGreater(len(edges), 0)
        self.assertEqual(len(edges), 3)
        for edge in edges:
            self.assertIsInstance(edge, Edge)
            self.assertEqual(edge.from_id, "W2741809807")
        self.assertEqual(
            {edge.to_id for edge in edges},
            {"W2100121061", "W2144252560", "W1995315455"},
        )

    def test_work_with_no_referenced_works_returns_empty_list(self):
        http = FakeHttp(FakeResponse(200, WORK_ITEM_NO_ABSTRACT_NO_REFS))
        edges = fetch_citation_edges("10.0000/bare", http=http)
        self.assertEqual(edges, [])

    def test_lookup_failure_raises_not_returns_empty_list(self):
        # A failed lookup must be distinguishable from "found, cites nothing".
        http = FakeHttp(FakeResponse(404, {}))
        with self.assertRaises(OpenAlexFetchError):
            fetch_citation_edges("10.0000/missing", http=http)


if __name__ == "__main__":
    unittest.main()
