import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from einstein.store import Store
from einstein.uspto_fetcher import USPTOFetchError, fetch_patents

FIXTURES_DIR = Path(__file__).parent / "fixtures"

PATENT_DOC = {
    "patentNumber": "11234567",
    "patentTitle": "System and Method for Quantum Error Correction",
    "abstractText": "A system for correcting errors in a quantum processor.",
    "grantDate": "2023-05-16",
}

PATENT_DOC_NO_ABSTRACT = {
    "patentNumber": "11999999",
    "patentTitle": "Bare Widget",
    "abstractText": "",
    "grantDate": "2022-01-01",
}


class FakeResponse:
    def __init__(self, status_code, payload=None, reason="Error"):
        self.status_code = status_code
        self._payload = payload or {}
        self.reason = reason

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


class MissingApiKeyTest(unittest.TestCase):
    def test_no_key_anywhere_raises_before_any_request(self):
        http = FakeHttp(FakeResponse(200, {"patentData": [PATENT_DOC]}))
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(USPTOFetchError) as ctx:
                fetch_patents("quantum computing", http=http)
        self.assertTrue(ctx.exception.missing_key)
        self.assertEqual(http.calls, [])

    def test_empty_string_key_is_treated_as_missing(self):
        http = FakeHttp(FakeResponse(200, {"patentData": [PATENT_DOC]}))
        with patch.dict("os.environ", {"USPTO_API_KEY": ""}, clear=True):
            with self.assertRaises(USPTOFetchError) as ctx:
                fetch_patents("x", http=http)
        self.assertTrue(ctx.exception.missing_key)

    def test_error_message_mentions_env_var(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(USPTOFetchError) as ctx:
                fetch_patents("x", http=FakeHttp(FakeResponse(200, {})))
        self.assertIn("USPTO_API_KEY", str(ctx.exception))

    def test_never_returns_mock_patent_data(self):
        # The convo draft fell back to a hardcoded "MOCK-PATENT-01" record.
        # That must be gone: a missing key is a hard failure, not a stub.
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(USPTOFetchError):
                fetch_patents("x", http=FakeHttp(FakeResponse(200, {})))


class ApiKeyResolutionTest(unittest.TestCase):
    def test_explicit_api_key_argument_used(self):
        http = FakeHttp(FakeResponse(200, {"patentData": []}))
        with patch.dict("os.environ", {}, clear=True):
            fetch_patents("x", api_key="explicit-key", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "explicit-key")

    def test_env_var_used_when_argument_omitted(self):
        http = FakeHttp(FakeResponse(200, {"patentData": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "env-key"}, clear=True):
            fetch_patents("x", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "env-key")

    def test_explicit_argument_overrides_env_var(self):
        http = FakeHttp(FakeResponse(200, {"patentData": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "env-key"}, clear=True):
            fetch_patents("x", api_key="explicit-key", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "explicit-key")


class FetchPatentsSuccessTest(unittest.TestCase):
    def test_maps_docs_to_records(self):
        http = FakeHttp(FakeResponse(200, {"patentData": [PATENT_DOC]}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            records = fetch_patents("quantum error correction", http=http)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.type, "patent")
        self.assertEqual(record.id, "11234567")
        self.assertEqual(record.title, "System and Method for Quantum Error Correction")
        self.assertIn("correcting errors", record.summary)
        self.assertEqual(record.url, "https://patents.google.com/patent/US11234567/en")
        self.assertEqual(record.ts, "2023-05-16")
        self.assertEqual(record.raw, PATENT_DOC)

    def test_missing_abstract_summary_is_title_only(self):
        http = FakeHttp(FakeResponse(200, {"patentData": [PATENT_DOC_NO_ABSTRACT]}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            record = fetch_patents("x", http=http)[0]
        self.assertEqual(record.summary, "Bare Widget")

    def test_zero_results_returns_empty_list_without_raising(self):
        http = FakeHttp(FakeResponse(200, {"patentData": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            records = fetch_patents("asdkfjasldkfjalskdjf_no_match", http=http)
        self.assertEqual(records, [])

    def test_max_results_truncates(self):
        docs = [dict(PATENT_DOC, patentNumber=str(10000000 + i)) for i in range(5)]
        http = FakeHttp(FakeResponse(200, {"patentData": docs}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            records = fetch_patents("x", max_results=2, http=http)
        self.assertEqual(len(records), 2)

    def test_query_and_headers_sent_to_http_client(self):
        http = FakeHttp(FakeResponse(200, {"patentData": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "secret"}, clear=True):
            fetch_patents("neural nets", http=http)
        self.assertEqual(len(http.calls), 1)
        url, kwargs = http.calls[0]
        self.assertEqual(url, "https://api.uspto.gov/api/v1/patent/search")
        self.assertEqual(kwargs["params"]["q"], "neural nets")
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "secret")


class FetchPatentsErrorTest(unittest.TestCase):
    def test_non_200_raises_distinct_error_not_empty_list(self):
        http = FakeHttp(FakeResponse(500, {}, reason="Internal Server Error"))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            with self.assertRaises(USPTOFetchError) as ctx:
                fetch_patents("x", http=http)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertFalse(ctx.exception.missing_key)

    def test_401_bad_key_raises_and_is_not_flagged_missing_key(self):
        # A configured-but-rejected key is a different failure than an
        # absent one -- the caller told us a key, USPTO said no to it.
        http = FakeHttp(FakeResponse(401, {}, reason="Unauthorized"))
        with patch.dict("os.environ", {"USPTO_API_KEY": "bad-key"}, clear=True):
            with self.assertRaises(USPTOFetchError) as ctx:
                fetch_patents("x", http=http)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertFalse(ctx.exception.missing_key)

    def test_error_is_distinguishable_from_zero_results_by_type(self):
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            ok_http = FakeHttp(FakeResponse(200, {"patentData": []}))
            self.assertEqual(fetch_patents("x", http=ok_http), [])

            bad_http = FakeHttp(FakeResponse(503, {}, reason="Service Unavailable"))
            with self.assertRaises(USPTOFetchError):
                fetch_patents("x", http=bad_http)

    def test_doc_missing_patent_number_raises(self):
        bad_doc = {"patentTitle": "No Number Patent", "abstractText": ""}
        http = FakeHttp(FakeResponse(200, {"patentData": [bad_doc]}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            with self.assertRaises(USPTOFetchError):
                fetch_patents("x", http=http)


class FetchPatentsCachingTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = Store(Path(self._tmpdir.name) / "einstein.db", clock=lambda: "2026-08-08T00:00:00+00:00")
        self.addCleanup(self.store.close)
        self._env = patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True)
        self._env.start()
        self.addCleanup(self._env.stop)

    def _fixture_payload(self):
        raw = json.loads((FIXTURES_DIR / "uspto_patent_search.json").read_text())
        return {"patentData": raw["patentData"]}

    def test_second_identical_fetch_reads_from_store_not_network(self):
        http = FakeHttp(FakeResponse(200, self._fixture_payload()))
        first = fetch_patents("quantum error correction", http=http, store=self.store)
        second = fetch_patents("quantum error correction", http=http, store=self.store)

        self.assertEqual(len(http.calls), 1, "second fetch must not re-hit the fake http client")
        self.assertEqual([r.id for r in first], [r.id for r in second])
        self.assertEqual(second[0].id, "11234567")

    def test_zero_result_search_is_cached_as_a_negative_not_dropped(self):
        http = FakeHttp(FakeResponse(200, {"patentData": []}))
        fetch_patents("asdkfjasldkfjalskdjf_no_match", http=http, store=self.store)

        lookup = self.store.lookup_query(
            source="uspto", query="asdkfjasldkfjalskdjf_no_match", params={"max_results": 10}
        )
        self.assertEqual(lookup.status, "negative")

    def test_second_zero_result_fetch_does_not_hit_the_network(self):
        http = FakeHttp(FakeResponse(200, {"patentData": []}))
        fetch_patents("no match", http=http, store=self.store)
        fetch_patents("no match", http=http, store=self.store)
        self.assertEqual(len(http.calls), 1)

    def test_429_is_cached_as_rate_limited_not_negative(self):
        http = FakeHttp(FakeResponse(429, {}, reason="Too Many Requests"))
        with self.assertRaises(USPTOFetchError):
            fetch_patents("x", http=http, store=self.store)

        lookup = self.store.lookup_query(source="uspto", query="x", params={"max_results": 10})
        self.assertEqual(lookup.status, "rate_limited")
        self.assertNotEqual(lookup.status, "negative")

    def test_500_is_cached_as_error(self):
        http = FakeHttp(FakeResponse(500, {}, reason="Internal Server Error"))
        with self.assertRaises(USPTOFetchError):
            fetch_patents("x", http=http, store=self.store)

        lookup = self.store.lookup_query(source="uspto", query="x", params={"max_results": 10})
        self.assertEqual(lookup.status, "error")

    def test_missing_key_never_touches_store(self):
        http = FakeHttp(FakeResponse(200, self._fixture_payload()))
        self._env.stop()
        try:
            with patch.dict("os.environ", {}, clear=True):
                with self.assertRaises(USPTOFetchError):
                    fetch_patents("x", http=http, store=self.store)
        finally:
            self._env.start()
        self.assertEqual(http.calls, [])
        lookup = self.store.lookup_query(source="uspto", query="x", params={"max_results": 10})
        self.assertEqual(lookup.status, "absent")

    def test_without_store_behaves_exactly_as_before(self):
        http = FakeHttp(FakeResponse(200, self._fixture_payload()))
        records = fetch_patents("quantum error correction", http=http)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, "11234567")


if __name__ == "__main__":
    unittest.main()
