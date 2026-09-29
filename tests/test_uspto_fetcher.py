import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from einstein.store import Store
from einstein.uspto_fetcher import USPTOFetchError, fetch_patents

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Field names confirmed live against api.uspto.gov/api/v1/patent/applications/search
# (einstein-tix) -- see tests/fixtures/uspto_patent_search.json for the trimmed real
# response these mirror.
GRANTED_DOC = {
    "grantDocumentMetaData": {
        "fileLocationURI": (
            "https://api.uspto.gov/api/v1/datasets/products/files/"
            "PTGRXML-SPLT/2023/ipg230516/00000000_11234567.xml"
        ),
    },
    "applicationMetaData": {
        "inventionTitle": "System and Method for Quantum Error Correction",
        "patentNumber": "11234567",
        "grantDate": "2023-05-16",
        "filingDate": "2021-01-01",
    },
    "applicationNumberText": "17100000",
}

PENDING_DOC = {
    "applicationMetaData": {
        "inventionTitle": "Bare Widget",
        "filingDate": "2026-01-01",
    },
    "applicationNumberText": "19700001",
}

# Trimmed real response for application 19589991 (IQM Finland OY, fetched
# live against api.uspto.gov this session, einstein-zfp) -- confirms this
# is a genuine shape, not a hand-guess: applicationStatusCode 19
# ("Application Undergoing Preexam Processing") documents carry no
# filingDate key at all, only effectiveFilingDate. Mirrors the second
# entry in tests/fixtures/uspto_patent_search.json.
PENDING_NO_FILING_DATE_DOC = {
    "applicationMetaData": {
        "applicationStatusCode": 19,
        "applicationStatusDescriptionText": "Application Undergoing Preexam Processing",
        "effectiveFilingDate": "2026-05-22",
        "inventionTitle": (
            "METHOD FOR CONSTRUCTING A QUANTUM ERROR CORRECTION CODE, A QUANTUM "
            "PROCESSING DEVICE FOR IMPLEMENTING A QUANTUM ERROR CORRECTION CODE, "
            "A QUANTUM ERROR CORRECTION CODE AND A METHOD FOR IMPLEMENTING "
            "QUANTUM INFORMATION USING THE QUANTUM ERROR CORRECTION CODE"
        ),
        "firstApplicantName": "IQM FINLAND OY",
    },
    "applicationNumberText": "19589991",
}


NO_MATCH_404_BODY = {
    "code": "404",
    "message": "Not Found",
    "detailedMessage": "No matching records found, refine your search criteria and try again",
    "requestIdentifier": "7e28cd2c-0872-4fdb-92d3-ab2689658902",
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
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": [GRANTED_DOC]}))
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(USPTOFetchError) as ctx:
                fetch_patents("quantum computing", http=http)
        self.assertTrue(ctx.exception.missing_key)
        self.assertEqual(http.calls, [])

    def test_empty_string_key_is_treated_as_missing(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": [GRANTED_DOC]}))
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
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
        with patch.dict("os.environ", {}, clear=True):
            fetch_patents("x", api_key="explicit-key", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "explicit-key")

    def test_env_var_used_when_argument_omitted(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "env-key"}, clear=True):
            fetch_patents("x", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "env-key")

    def test_explicit_argument_overrides_env_var(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "env-key"}, clear=True):
            fetch_patents("x", api_key="explicit-key", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "explicit-key")


class FetchPatentsSuccessTest(unittest.TestCase):
    def test_maps_granted_doc_to_record(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": [GRANTED_DOC]}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            records = fetch_patents("quantum error correction", http=http)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.type, "patent")
        self.assertEqual(record.id, "11234567")
        self.assertEqual(record.title, "System and Method for Quantum Error Correction")
        # No abstract field exists in this API -- summary is title-only.
        self.assertEqual(record.summary, "System and Method for Quantum Error Correction")
        self.assertEqual(record.url, "https://patents.google.com/patent/US11234567/en")
        self.assertEqual(record.ts, "2023-05-16")
        self.assertEqual(record.raw, GRANTED_DOC)

    def test_pending_application_falls_back_to_application_number_and_patentcenter_url(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": [PENDING_DOC]}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            record = fetch_patents("x", http=http)[0]
        self.assertEqual(record.id, "19700001")
        self.assertEqual(record.title, "Bare Widget")
        self.assertEqual(record.summary, "Bare Widget")
        self.assertEqual(record.url, "https://patentcenter.uspto.gov/applications/19700001")
        # No grantDate yet -- falls back to filingDate.
        self.assertEqual(record.ts, "2026-01-01")

    def test_pending_application_with_no_filing_date_falls_back_to_effective_filing_date(self):
        # einstein-zfp: applicationStatusCode 19 documents (live-confirmed,
        # not hand-guessed -- see PENDING_NO_FILING_DATE_DOC) have no
        # filingDate key at all. Before this fix, ts silently fell all the
        # way through to the hardcoded "1970-01-01" placeholder.
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": [PENDING_NO_FILING_DATE_DOC]}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            record = fetch_patents("x", http=http)[0]
        self.assertEqual(record.id, "19589991")
        self.assertEqual(record.ts, "2026-05-22")
        self.assertNotEqual(record.ts, "1970-01-01")

    def test_zero_results_returns_empty_list_without_raising(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            records = fetch_patents("asdkfjasldkfjalskdjf_no_match", http=http)
        self.assertEqual(records, [])

    def test_max_results_truncates(self):
        docs = [
            {
                "applicationMetaData": dict(GRANTED_DOC["applicationMetaData"], patentNumber=str(10000000 + i)),
                "applicationNumberText": GRANTED_DOC["applicationNumberText"],
            }
            for i in range(5)
        ]
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": docs}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            records = fetch_patents("x", max_results=2, http=http)
        self.assertEqual(len(records), 2)

    def test_query_and_headers_sent_to_http_client(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "secret"}, clear=True):
            fetch_patents("neural nets", http=http)
        self.assertEqual(len(http.calls), 1)
        url, kwargs = http.calls[0]
        self.assertEqual(url, "https://api.uspto.gov/api/v1/patent/applications/search")
        self.assertEqual(kwargs["params"]["q"], "neural nets")
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "secret")

    def test_sort_by_score_always_requested(self):
        # einstein-b3b: this API defaults to sorting by filing date
        # (recency), not relevance -- confirmed live that a multi-word `q`
        # is an OR-of-terms match with no ranking unless `sort=_score desc`
        # is sent explicitly. Every request must carry it.
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            fetch_patents("neural nets", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["params"]["sort"], "_score desc")


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
            ok_http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
            self.assertEqual(fetch_patents("x", http=ok_http), [])

            bad_http = FakeHttp(FakeResponse(503, {}, reason="Service Unavailable"))
            with self.assertRaises(USPTOFetchError):
                fetch_patents("x", http=bad_http)

    def test_doc_missing_patent_and_application_number_raises(self):
        bad_doc = {"applicationMetaData": {"inventionTitle": "No Number Patent"}}
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": [bad_doc]}))
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
        return {"patentFileWrapperDataBag": raw["patentFileWrapperDataBag"]}

    def test_second_identical_fetch_reads_from_store_not_network(self):
        http = FakeHttp(FakeResponse(200, self._fixture_payload()))
        first = fetch_patents("quantum error correction", http=http, store=self.store)
        second = fetch_patents("quantum error correction", http=http, store=self.store)

        self.assertEqual(len(http.calls), 1, "second fetch must not re-hit the fake http client")
        self.assertEqual([r.id for r in first], [r.id for r in second])
        self.assertEqual(second[0].id, "12735810")

    def test_zero_result_search_is_cached_as_a_negative_not_dropped(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
        fetch_patents("asdkfjasldkfjalskdjf_no_match", http=http, store=self.store)

        lookup = self.store.lookup_query(
            source="uspto", query="asdkfjasldkfjalskdjf_no_match", params={"max_results": 10}
        )
        self.assertEqual(lookup.status, "negative")

    def test_second_zero_result_fetch_does_not_hit_the_network(self):
        http = FakeHttp(FakeResponse(200, {"patentFileWrapperDataBag": []}))
        fetch_patents("no match", http=http, store=self.store)
        fetch_patents("no match", http=http, store=self.store)
        self.assertEqual(len(http.calls), 1)

    def test_live_shaped_no_match_404_is_a_cached_negative_not_an_error(self):
        # Body copied from a live q=zzzznonexistentqueryterm12345 call (einstein-n7n).
        http = FakeHttp(FakeResponse(404, NO_MATCH_404_BODY, reason="Not Found"))
        self.assertEqual(fetch_patents("zzzz", http=http, store=self.store), [])
        lookup = self.store.lookup_query(source="uspto", query="zzzz", params={"max_results": 10})
        self.assertEqual(lookup.status, "negative")

    def test_other_404_still_raises(self):
        http = FakeHttp(FakeResponse(404, {"code": "404", "message": "Not Found"}, reason="Not Found"))
        with self.assertRaises(USPTOFetchError) as ctx:
            fetch_patents("x", http=http, store=self.store)
        self.assertEqual(ctx.exception.status_code, 404)

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
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].id, "12735810")


if __name__ == "__main__":
    unittest.main()
