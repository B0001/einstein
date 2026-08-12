import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from einstein.github_fetcher import GitHubFetchError, fetch_repos
from einstein.store import Store

FIXTURES_DIR = Path(__file__).parent / "fixtures"

REPO_ITEM = {
    "full_name": "qiskit/qiskit",
    "html_url": "https://github.com/qiskit/qiskit",
    "description": "Qiskit is an open-source SDK for quantum computing",
    "topics": ["quantum-computing", "qiskit", "python"],
    "pushed_at": "2026-08-01T12:00:00Z",
    "updated_at": "2026-08-01T12:00:00Z",
    "created_at": "2020-01-01T00:00:00Z",
    "stargazers_count": 5000,
}

REPO_ITEM_NO_DESCRIPTION = {
    "full_name": "someone/bare-repo",
    "html_url": "https://github.com/someone/bare-repo",
    "description": None,
    "topics": [],
    "pushed_at": "2026-08-01T12:00:00Z",
    "updated_at": "2026-08-01T12:00:00Z",
    "created_at": "2020-01-01T00:00:00Z",
}


class FakeResponse:
    def __init__(self, status_code, payload=None, headers=None, reason="Error"):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
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


class FetchReposSuccessTest(unittest.TestCase):
    def test_maps_items_to_records(self):
        http = FakeHttp(FakeResponse(200, {"items": [REPO_ITEM]}))
        records = fetch_repos("quantum computing", http=http)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.type, "repo")
        self.assertEqual(record.id, "qiskit/qiskit")
        self.assertEqual(record.title, "qiskit/qiskit")
        self.assertEqual(record.url, "https://github.com/qiskit/qiskit")
        self.assertIn("Qiskit is an open-source SDK", record.summary)
        self.assertIn("quantum-computing", record.summary)
        self.assertEqual(record.raw, REPO_ITEM)

    def test_summary_is_description_plus_topics(self):
        http = FakeHttp(FakeResponse(200, {"items": [REPO_ITEM]}))
        record = fetch_repos("x", http=http)[0]
        for topic in REPO_ITEM["topics"]:
            self.assertIn(topic, record.summary)
        self.assertIn(REPO_ITEM["description"], record.summary)

    def test_missing_description_yields_summary_from_topics_only(self):
        http = FakeHttp(FakeResponse(200, {"items": [REPO_ITEM_NO_DESCRIPTION]}))
        record = fetch_repos("x", http=http)[0]
        self.assertEqual(record.summary, "")

    def test_zero_results_returns_empty_list_without_raising(self):
        http = FakeHttp(FakeResponse(200, {"items": []}))
        records = fetch_repos("asdkfjasldkfjalskdjf_no_match", http=http)
        self.assertEqual(records, [])

    def test_max_results_truncates(self):
        items = [dict(REPO_ITEM, full_name=f"org/repo{i}", html_url=f"https://x/{i}") for i in range(5)]
        http = FakeHttp(FakeResponse(200, {"items": items}))
        records = fetch_repos("x", max_results=2, http=http)
        self.assertEqual(len(records), 2)

    def test_query_and_params_sent_to_http_client(self):
        http = FakeHttp(FakeResponse(200, {"items": []}))
        fetch_repos("neural nets", http=http)
        self.assertEqual(len(http.calls), 1)
        url, kwargs = http.calls[0]
        self.assertEqual(url, "https://api.github.com/search/repositories")
        self.assertEqual(kwargs["params"]["q"], "neural nets")


class AuthHeaderTest(unittest.TestCase):
    def test_no_token_omits_authorization_header(self):
        http = FakeHttp(FakeResponse(200, {"items": []}))
        with patch.dict("os.environ", {}, clear=True):
            fetch_repos("x", http=http)
        _, kwargs = http.calls[0]
        self.assertNotIn("Authorization", kwargs["headers"])

    def test_token_from_env_sets_bearer_header(self):
        http = FakeHttp(FakeResponse(200, {"items": []}))
        with patch.dict("os.environ", {"GITHUB_TOKEN": "secret123"}):
            fetch_repos("x", http=http)
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret123")


class FetchReposErrorTest(unittest.TestCase):
    def test_non_200_raises_distinct_error_not_empty_list(self):
        http = FakeHttp(FakeResponse(500, {}, reason="Internal Server Error"))
        with self.assertRaises(GitHubFetchError) as ctx:
            fetch_repos("x", http=http)
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertFalse(ctx.exception.rate_limited)

    def test_401_bad_auth_raises(self):
        http = FakeHttp(FakeResponse(401, {}, reason="Unauthorized"))
        with self.assertRaises(GitHubFetchError) as ctx:
            fetch_repos("x", http=http)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_403_rate_limited_raises_and_is_flagged(self):
        http = FakeHttp(
            FakeResponse(
                403,
                {},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1234567890"},
                reason="Forbidden",
            )
        )
        with self.assertRaises(GitHubFetchError) as ctx:
            fetch_repos("x", http=http)
        self.assertTrue(ctx.exception.rate_limited)

    def test_403_without_rate_limit_headers_is_not_flagged_rate_limited(self):
        http = FakeHttp(FakeResponse(403, {}, reason="Forbidden"))
        with self.assertRaises(GitHubFetchError) as ctx:
            fetch_repos("x", http=http)
        self.assertFalse(ctx.exception.rate_limited)

    def test_429_raises_and_is_flagged_rate_limited(self):
        http = FakeHttp(FakeResponse(429, {}, reason="Too Many Requests"))
        with self.assertRaises(GitHubFetchError) as ctx:
            fetch_repos("x", http=http)
        self.assertTrue(ctx.exception.rate_limited)

    def test_error_is_distinguishable_from_zero_results_by_type(self):
        # A caller must be able to tell "the search failed" apart from
        # "the search ran and found nothing" without inspecting content.
        ok_http = FakeHttp(FakeResponse(200, {"items": []}))
        self.assertEqual(fetch_repos("x", http=ok_http), [])

        bad_http = FakeHttp(FakeResponse(503, {}, reason="Service Unavailable"))
        with self.assertRaises(GitHubFetchError):
            fetch_repos("x", http=bad_http)


class FetchReposCachingTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = Store(Path(self._tmpdir.name) / "einstein.db", clock=lambda: "2026-08-08T00:00:00+00:00")
        self.addCleanup(self.store.close)

    def _fixture_payload(self):
        raw = json.loads((FIXTURES_DIR / "github_repo_search.json").read_text())
        return {"items": raw["items"]}

    def test_second_identical_fetch_reads_from_store_not_network(self):
        http = FakeHttp(FakeResponse(200, self._fixture_payload()))
        first = fetch_repos("qiskit", http=http, store=self.store)
        second = fetch_repos("qiskit", http=http, store=self.store)

        self.assertEqual(len(http.calls), 1, "second fetch must not re-hit the fake http client")
        self.assertEqual([r.id for r in first], [r.id for r in second])
        self.assertEqual(second[0].id, "qiskit/qiskit")

    def test_zero_result_search_is_cached_as_a_negative_not_dropped(self):
        http = FakeHttp(FakeResponse(200, {"items": []}))
        fetch_repos("asdkfjasldkfjalskdjf_no_match", http=http, store=self.store)

        lookup = self.store.lookup_query(
            source="github", query="asdkfjasldkfjalskdjf_no_match", params={"max_results": 30}
        )
        self.assertEqual(lookup.status, "negative")

    def test_second_zero_result_fetch_does_not_hit_the_network(self):
        http = FakeHttp(FakeResponse(200, {"items": []}))
        fetch_repos("no match", http=http, store=self.store)
        fetch_repos("no match", http=http, store=self.store)
        self.assertEqual(len(http.calls), 1)

    def test_429_is_cached_as_rate_limited_not_negative(self):
        http = FakeHttp(FakeResponse(429, {}, reason="Too Many Requests"))
        with self.assertRaises(GitHubFetchError):
            fetch_repos("x", http=http, store=self.store)

        lookup = self.store.lookup_query(source="github", query="x", params={"max_results": 30})
        self.assertEqual(lookup.status, "rate_limited")
        self.assertNotEqual(lookup.status, "negative")

    def test_500_is_cached_as_error(self):
        http = FakeHttp(FakeResponse(500, {}, reason="Internal Server Error"))
        with self.assertRaises(GitHubFetchError):
            fetch_repos("x", http=http, store=self.store)

        lookup = self.store.lookup_query(source="github", query="x", params={"max_results": 30})
        self.assertEqual(lookup.status, "error")

    def test_without_store_behaves_exactly_as_before(self):
        http = FakeHttp(FakeResponse(200, self._fixture_payload()))
        records = fetch_repos("qiskit", http=http)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, "qiskit/qiskit")


if __name__ == "__main__":
    unittest.main()
