import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from einstein.openalex_fetcher import (
    Edge,
    OpenAlexFetchError,
    fetch_citation_edges,
    fetch_work_by_doi,
    search_papers,
)
from einstein.store import Store

FIXTURES_DIR = Path(__file__).parent / "fixtures"

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


class FetchWorkByDoiCachingTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = Store(Path(self._tmpdir.name) / "einstein.db", clock=lambda: "2026-08-08T00:00:00+00:00")
        self.addCleanup(self.store.close)

    def _fixture_work(self):
        data = json.loads((FIXTURES_DIR / "openalex_work.json").read_text())
        data.pop("_note", None)
        return data

    def test_second_identical_fetch_reads_from_store_not_network(self):
        http = FakeHttp(FakeResponse(200, self._fixture_work()))
        first = fetch_work_by_doi("10.7717/peerj.4375", http=http, store=self.store)
        second = fetch_work_by_doi("10.7717/peerj.4375", http=http, store=self.store)

        self.assertEqual(len(http.calls), 1, "second fetch must not re-hit the fake http client")
        self.assertEqual(first.id, second.id)
        self.assertEqual(second.id, "W2741809807")

    def test_doi_url_and_bare_doi_forms_share_one_cache_entry(self):
        http = FakeHttp(FakeResponse(200, self._fixture_work()))
        fetch_work_by_doi("10.7717/peerj.4375", http=http, store=self.store)
        fetch_work_by_doi("https://doi.org/10.7717/peerj.4375", http=http, store=self.store)
        self.assertEqual(len(http.calls), 1)

    def test_404_is_cached_as_a_negative_and_replayed_without_network(self):
        http = FakeHttp(FakeResponse(404, {}))
        with self.assertRaises(OpenAlexFetchError) as first:
            fetch_work_by_doi("10.0000/missing", http=http, store=self.store)
        self.assertTrue(first.exception.not_found)

        with self.assertRaises(OpenAlexFetchError) as second:
            fetch_work_by_doi("10.0000/missing", http=http, store=self.store)
        self.assertTrue(second.exception.not_found)
        self.assertEqual(len(http.calls), 1, "cached not-found must not re-hit the network")

    def test_500_is_cached_as_error_not_negative(self):
        http = FakeHttp(FakeResponse(500, {}))
        with self.assertRaises(OpenAlexFetchError):
            fetch_work_by_doi("10.0000/x", http=http, store=self.store)

        lookup = self.store.lookup_query(source="openalex", query="10.0000/x", params={})
        self.assertEqual(lookup.status, "error")

    def test_without_store_behaves_exactly_as_before(self):
        http = FakeHttp(FakeResponse(200, self._fixture_work()))
        record = fetch_work_by_doi("10.7717/peerj.4375", http=http)
        self.assertEqual(record.id, "W2741809807")


class FetchCitationEdgesCachingTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = Store(Path(self._tmpdir.name) / "einstein.db", clock=lambda: "2026-08-08T00:00:00+00:00")
        self.addCleanup(self.store.close)

    def test_store_is_passed_through_and_second_call_avoids_network(self):
        data = json.loads((FIXTURES_DIR / "openalex_work.json").read_text())
        data.pop("_note", None)
        http = FakeHttp(FakeResponse(200, data))

        first = fetch_citation_edges("10.7717/peerj.4375", http=http, store=self.store)
        second = fetch_citation_edges("10.7717/peerj.4375", http=http, store=self.store)

        self.assertEqual(len(http.calls), 1)
        self.assertEqual(first, second)


def _search_fixture():
    data = json.loads((FIXTURES_DIR / "openalex_work_search.json").read_text())
    data.pop("_note", None)
    return data


class SearchPapersTest(unittest.TestCase):
    def test_maps_results_to_records(self):
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        records = search_papers("quantum error correction surface code", http=http)

        self.assertEqual(len(records), 2)
        self.assertTrue(all(r.type == "paper" for r in records))
        self.assertEqual(records[0].id, "W4400123456")
        self.assertEqual(records[0].title, "Quantum error correction below the surface code threshold")
        self.assertEqual(records[0].summary, "Quantum error correction below threshold")

    def test_uses_relevance_ranked_search_not_all_terms_filter(self):
        # einstein-av1: the title_and_abstract.search filter requires every
        # term to match and returned [] for paragraph-length method text.
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        search_papers("quantum error correction surface code", http=http)
        url, kwargs = http.calls[0]
        self.assertEqual(url, "https://api.openalex.org/works")
        self.assertEqual(kwargs["params"]["search"], "quantum error correction surface code")
        self.assertNotIn("filter", kwargs["params"])

    def test_comma_is_dropped_not_quote_wrapped(self):
        # Quotes mean exact phrase in search=, which would bring back the
        # zero-recall failure; the comma only needed quoting under filter=.
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        search_papers("gradient descent, adaptive learning rate", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["params"]["search"], "gradient descent adaptive learning rate")

    def test_query_syntax_in_prose_is_neutralized(self):
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        search_papers('Use "sparse" attention (NOT dense) AND low-rank updates.', http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["params"]["search"], "use sparse attention not dense and low rank updates")

    def test_paragraph_query_is_sent_whole(self):
        paragraph = (
            "We contract the attention tensor as a matrix product state, truncating bond "
            "dimension adaptively; this replaces the quadratic softmax with a linear-time sweep."
        )
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        search_papers(paragraph, http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(
            kwargs["params"]["search"],
            "we contract the attention tensor as a matrix product state truncating bond dimension "
            "adaptively this replaces the quadratic softmax with a linear time sweep",
        )

    def test_query_with_no_words_returns_empty_without_network(self):
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        self.assertEqual(search_papers(" ,;!? ", http=http), [])
        self.assertEqual(http.calls, [])

    def test_max_results_passed_as_per_page(self):
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        search_papers("quantum error correction", max_results=3, http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["params"]["per-page"], 3)

    def test_zero_results_returns_empty_list_not_error(self):
        http = FakeHttp(FakeResponse(200, {"meta": {"count": 0}, "results": []}))
        records = search_papers("a query nothing matches", http=http)
        self.assertEqual(records, [])

    def test_missing_abstract_yields_empty_summary(self):
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        records = search_papers("quantum error correction surface code", http=http)
        self.assertEqual(records[1].summary, "")

    def test_non_200_raises_and_is_not_flagged_not_found(self):
        http = FakeHttp(FakeResponse(500, {}))
        with self.assertRaises(OpenAlexFetchError) as ctx:
            search_papers("quantum error correction", http=http)
        self.assertFalse(ctx.exception.not_found)

    def test_mailto_env_var_included_as_param(self):
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        with patch.dict("os.environ", {"OPENALEX_MAILTO": "me@example.com"}):
            search_papers("quantum error correction", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["params"]["mailto"], "me@example.com")


class SearchPapersCachingTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = Store(Path(self._tmpdir.name) / "einstein.db", clock=lambda: "2026-08-08T00:00:00+00:00")
        self.addCleanup(self.store.close)

    def test_second_identical_search_reads_from_store_not_network(self):
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        first = search_papers("quantum error correction surface code", http=http, store=self.store)
        second = search_papers("quantum error correction surface code", http=http, store=self.store)

        self.assertEqual(len(http.calls), 1, "second search must not re-hit the fake http client")
        self.assertEqual([r.id for r in first], [r.id for r in second])

    def test_zero_result_search_is_cached_as_negative_and_replayed(self):
        http = FakeHttp(FakeResponse(200, {"meta": {"count": 0}, "results": []}))
        first = search_papers("a query nothing matches", http=http, store=self.store)
        second = search_papers("a query nothing matches", http=http, store=self.store)

        self.assertEqual(first, [])
        self.assertEqual(second, [])
        self.assertEqual(len(http.calls), 1, "cached negative must not re-hit the network")

    def test_error_is_cached_as_error_not_negative(self):
        http = FakeHttp(FakeResponse(500, {}))
        with self.assertRaises(OpenAlexFetchError):
            search_papers("quantum error correction", http=http, store=self.store)

        lookup = self.store.lookup_query(
            source="openalex", query="quantum error correction", params={"max_results": 10, "param": "search"}
        )
        self.assertEqual(lookup.status, "error")

    def test_negative_cached_by_the_old_filter_search_is_not_replayed(self):
        # A pre-einstein-av1 zero-result row (keyed on max_results alone) must
        # not answer for the new search= query.
        self.store.upsert_query(
            source="openalex", query="quantum error correction surface code",
            params={"max_results": 10}, status="ok", record_type="paper", ids=[],
        )
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        records = search_papers("quantum error correction surface code", http=http, store=self.store)
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(len(records), 2)

    def test_different_max_results_are_different_cache_entries(self):
        http = FakeHttp(FakeResponse(200, _search_fixture()))
        search_papers("quantum error correction surface code", max_results=5, http=http, store=self.store)
        search_papers("quantum error correction surface code", max_results=10, http=http, store=self.store)
        self.assertEqual(len(http.calls), 2)


class SearchPapersMatchesSearchFnTest(unittest.TestCase):
    """einstein-0.5's acceptance criterion: importable, passable as-is to
    `novelty_auditor.audit_idea(search_papers=...)`, which calls
    `search_papers(idea.method)` -- one positional query string, no other
    arguments. This exercises exactly that call shape, with `requests`
    itself faked out so no network call happens."""

    def test_callable_with_no_arguments_but_the_query(self):
        with patch("einstein.openalex_fetcher.requests") as fake_requests:
            fake_requests.get.return_value = FakeResponse(200, _search_fixture())
            records = search_papers("quantum error correction surface code")

        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 2)


if __name__ == "__main__":
    unittest.main()
