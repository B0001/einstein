import datetime
import json
import tempfile
import unittest
from pathlib import Path

import arxiv

from einstein.arxiv_fetcher import ArxivFetchError, fetch_papers
from einstein.store import Store

PUBLISHED = datetime.datetime(2026, 8, 1, 12, 0, tzinfo=datetime.timezone.utc)
UPDATED = datetime.datetime(2026, 8, 2, 9, 30, tzinfo=datetime.timezone.utc)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def make_result(
    short_id="2508.00001v1",
    title="Quantum Error Correction via Foo",
    summary="We propose a method for quantum error correction.",
    authors=("Jane Doe", "John Roe"),
    categories=("quant-ph", "cs.LG"),
):
    return arxiv.Result(
        entry_id=f"http://arxiv.org/abs/{short_id}",
        updated=UPDATED,
        published=PUBLISHED,
        title=title,
        authors=[arxiv.Result.Author(name) for name in authors],
        summary=summary,
        comment="10 pages",
        journal_ref="",
        doi="",
        primary_category=categories[0],
        categories=list(categories),
        links=[
            arxiv.Result.Link(
                f"http://arxiv.org/pdf/{short_id}", title="pdf", content_type="application/pdf"
            )
        ],
    )


class FakeArxivClient:
    """Stands in for `arxiv.Client`: records the call, returns canned results."""

    def __init__(self, results=None, error=None):
        self._results = results or []
        self._error = error
        self.searches = []

    def results(self, search):
        self.searches.append(search)
        if self._error is not None:
            raise self._error
        return iter(self._results)


def _make_fixture_result():
    """Build an `arxiv.Result` from the recorded field set in
    `tests/fixtures/arxiv_paper.json`, for tests that want a result backed
    by a fixture file rather than `make_result`'s inline defaults."""
    data = json.loads((FIXTURES_DIR / "arxiv_paper.json").read_text())
    return make_result(
        short_id=data["short_id"],
        title=data["title"],
        summary=data["summary"],
        authors=tuple(data["authors"]),
        categories=tuple(data["categories"]),
    )


class FetchPapersSuccessTest(unittest.TestCase):
    def test_maps_results_to_records(self):
        client = FakeArxivClient([make_result()])
        records = fetch_papers("quantum error correction", client=client)

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.type, "paper")
        self.assertEqual(record.id, "2508.00001v1")
        self.assertEqual(record.title, "Quantum Error Correction via Foo")
        self.assertEqual(record.summary, "We propose a method for quantum error correction.")
        self.assertEqual(record.url, "http://arxiv.org/abs/2508.00001v1")
        self.assertEqual(record.ts, PUBLISHED.isoformat())
        self.assertEqual(record.raw["authors"], ["Jane Doe", "John Roe"])
        self.assertEqual(record.raw["categories"], ["quant-ph", "cs.LG"])
        self.assertEqual(record.raw["primary_category"], "quant-ph")

    def test_multiple_results_all_mapped(self):
        results = [make_result(short_id=f"2508.0000{i}v1") for i in range(3)]
        client = FakeArxivClient(results)
        records = fetch_papers("x", client=client)
        self.assertEqual(len(records), 3)
        self.assertEqual([r.id for r in records], [f"2508.0000{i}v1" for i in range(3)])

    def test_zero_results_returns_empty_list_without_raising(self):
        client = FakeArxivClient([])
        records = fetch_papers("asdkfjasldkfjalskdjf_no_match", client=client)
        self.assertEqual(records, [])

    def test_search_sorted_by_submitted_date(self):
        client = FakeArxivClient([])
        fetch_papers("neural nets", client=client)
        self.assertEqual(len(client.searches), 1)
        search = client.searches[0]
        self.assertEqual(search.sort_by, arxiv.SortCriterion.SubmittedDate)
        self.assertEqual(search.query, "neural nets")

    def test_max_results_passed_to_search(self):
        client = FakeArxivClient([])
        fetch_papers("x", max_results=5, client=client)
        self.assertEqual(client.searches[0].max_results, 5)


class FetchPapersErrorTest(unittest.TestCase):
    def test_http_error_raises_distinct_error_not_empty_list(self):
        client = FakeArxivClient(error=arxiv.HTTPError(url="http://x", retry=3, status=500))
        with self.assertRaises(ArxivFetchError) as ctx:
            fetch_papers("x", client=client)
        self.assertEqual(ctx.exception.query, "x")
        self.assertIsInstance(ctx.exception.__cause__, arxiv.HTTPError)

    def test_unexpected_empty_page_error_raises(self):
        client = FakeArxivClient(
            error=arxiv.UnexpectedEmptyPageError(url="http://x", retry=3, raw_feed=None)
        )
        with self.assertRaises(ArxivFetchError):
            fetch_papers("x", client=client)

    def test_error_is_distinguishable_from_zero_results_by_type(self):
        # A caller must be able to tell "the search failed" apart from
        # "the search ran and found nothing" without inspecting content.
        ok_client = FakeArxivClient([])
        self.assertEqual(fetch_papers("x", client=ok_client), [])

        bad_client = FakeArxivClient(error=arxiv.HTTPError(url="http://x", retry=3, status=503))
        with self.assertRaises(ArxivFetchError):
            fetch_papers("x", client=bad_client)


class FetchPapersCachingTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.store = Store(Path(self._tmpdir.name) / "einstein.db", clock=lambda: "2026-08-08T00:00:00+00:00")
        self.addCleanup(self.store.close)

    def test_second_identical_fetch_reads_from_store_not_network(self):
        client = FakeArxivClient([_make_fixture_result()])
        first = fetch_papers("quantum error correction", client=client, store=self.store)
        second = fetch_papers("quantum error correction", client=client, store=self.store)

        self.assertEqual(len(client.searches), 1, "second fetch must not re-hit the fake client")
        self.assertEqual([r.id for r in first], [r.id for r in second])
        self.assertEqual(second[0].title, "Quantum Error Correction via Foo")

    def test_zero_result_search_is_cached_as_a_negative_not_dropped(self):
        client = FakeArxivClient([])
        fetch_papers("asdkfjasldkfjalskdjf_no_match", client=client, store=self.store)

        lookup = self.store.lookup_query(
            source="arxiv", query="asdkfjasldkfjalskdjf_no_match", params={"max_results": 30}
        )
        self.assertEqual(lookup.status, "negative")

    def test_second_zero_result_fetch_does_not_hit_the_network(self):
        client = FakeArxivClient([])
        fetch_papers("no match", client=client, store=self.store)
        fetch_papers("no match", client=client, store=self.store)
        self.assertEqual(len(client.searches), 1)

    def test_rate_limited_failure_is_cached_as_rate_limited_not_negative(self):
        client = FakeArxivClient(error=arxiv.HTTPError(url="http://x", retry=3, status=429))
        with self.assertRaises(ArxivFetchError):
            fetch_papers("x", client=client, store=self.store)

        lookup = self.store.lookup_query(source="arxiv", query="x", params={"max_results": 30})
        self.assertEqual(lookup.status, "rate_limited")
        self.assertNotEqual(lookup.status, "negative")

    def test_non_rate_limit_failure_is_cached_as_error(self):
        client = FakeArxivClient(error=arxiv.HTTPError(url="http://x", retry=3, status=500))
        with self.assertRaises(ArxivFetchError):
            fetch_papers("x", client=client, store=self.store)

        lookup = self.store.lookup_query(source="arxiv", query="x", params={"max_results": 30})
        self.assertEqual(lookup.status, "error")

    def test_without_store_behaves_exactly_as_before(self):
        client = FakeArxivClient([_make_fixture_result()])
        records = fetch_papers("quantum error correction", client=client)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].id, "2508.00001v1")


if __name__ == "__main__":
    unittest.main()
