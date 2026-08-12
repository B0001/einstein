import tempfile
import unittest
from pathlib import Path

from einstein.schema import Record
from einstein.store import DEFAULT_NEGATIVE_TTL_DAYS, Store


class MutableClock:
    """An injectable clock whose value a test can move forward without
    sleeping -- `lookup_query`'s TTL check reads "now" from the same clock
    a test uses to set `fetched_at`, so moving this value forward is how a
    test simulates time passing."""

    def __init__(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


def _record(**overrides) -> Record:
    kwargs = dict(
        type="paper",
        id="2508.00001",
        title="A Paper",
        summary="v1",
        url="https://arxiv.org/abs/2508.00001",
        ts="2026-08-08T00:00:00+00:00",
        raw={"v": 1},
    )
    kwargs.update(overrides)
    return Record(**kwargs)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = Path(self._tmpdir.name) / "einstein.db"
        self._ticks = iter(
            [
                "2026-08-08T00:00:00+00:00",
                "2026-08-09T00:00:00+00:00",
                "2026-08-10T00:00:00+00:00",
                "2026-08-11T00:00:00+00:00",
            ]
        )
        self.store = Store(self.db_path, clock=lambda: next(self._ticks))
        self.addCleanup(self.store.close)

    def test_upsert_record_inserts_new_row(self):
        self.store.upsert_record(_record())
        self.assertEqual(len(self.store.all_records()), 1)
        self.assertEqual(self.store.get_record("paper", "2508.00001").summary, "v1")

    def test_upsert_twice_yields_one_row_with_updated_timestamps(self):
        self.store.upsert_record(_record(summary="v1"))
        self.store.upsert_record(_record(summary="v2"))

        self.assertEqual(len(self.store.all_records()), 1)
        self.assertEqual(self.store.get_record("paper", "2508.00001").summary, "v2")

        first_seen, last_seen = self.store.record_seen_at("paper", "2508.00001")
        self.assertEqual(first_seen, "2026-08-08T00:00:00+00:00")
        self.assertEqual(last_seen, "2026-08-09T00:00:00+00:00")

    def test_first_seen_survives_a_third_upsert(self):
        self.store.upsert_record(_record(summary="v1"))
        self.store.upsert_record(_record(summary="v2"))
        self.store.upsert_record(_record(summary="v3"))

        first_seen, last_seen = self.store.record_seen_at("paper", "2508.00001")
        self.assertEqual(first_seen, "2026-08-08T00:00:00+00:00")
        self.assertEqual(last_seen, "2026-08-10T00:00:00+00:00")

    def test_records_keyed_by_type_and_id_not_id_alone(self):
        self.store.upsert_record(_record(type="paper", id="shared-id", title="paper"))
        self.store.upsert_record(_record(type="repo", id="shared-id", title="repo"))

        self.assertEqual(len(self.store.all_records()), 2)
        self.assertEqual(self.store.get_record("paper", "shared-id").title, "paper")
        self.assertEqual(self.store.get_record("repo", "shared-id").title, "repo")

    def test_get_record_missing_returns_none(self):
        self.assertIsNone(self.store.get_record("paper", "does-not-exist"))

    def test_raw_round_trips_through_json(self):
        self.store.upsert_record(_record(raw={"nested": {"a": [1, 2, 3]}}))
        self.assertEqual(self.store.get_record("paper", "2508.00001").raw, {"nested": {"a": [1, 2, 3]}})

    def test_upsert_gap_inserts_new_row(self):
        self.store.upsert_gap("gap-1", "cross_pollination", {"score": 0.9})
        gaps = self.store.all_gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["kind"], "cross_pollination")
        self.assertEqual(gaps[0]["payload"], {"score": 0.9})

    def test_upsert_gap_twice_yields_one_row_with_updated_timestamps(self):
        self.store.upsert_gap("gap-1", "cross_pollination", {"score": 0.9})
        self.store.upsert_gap("gap-1", "cross_pollination", {"score": 0.95})

        gaps = self.store.all_gaps()
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["payload"], {"score": 0.95})
        self.assertEqual(gaps[0]["first_seen"], "2026-08-08T00:00:00+00:00")
        self.assertEqual(gaps[0]["last_seen"], "2026-08-09T00:00:00+00:00")

    def test_get_gap_missing_returns_none(self):
        self.assertIsNone(self.store.get_gap("does-not-exist"))

    def test_data_persists_across_reopen(self):
        self.store.upsert_record(_record())
        self.store.upsert_gap("gap-1", "cross_pollination", {"score": 0.9})
        self.store.close()

        reopened = Store(self.db_path, clock=lambda: "2026-08-12T00:00:00+00:00")
        self.addCleanup(reopened.close)
        self.assertEqual(len(reopened.all_records()), 1)
        self.assertEqual(len(reopened.all_gaps()), 1)

    def test_context_manager_closes_connection(self):
        db_path = Path(self._tmpdir.name) / "ctx.db"
        with Store(db_path, clock=lambda: "2026-08-08T00:00:00+00:00") as store:
            store.upsert_record(_record())
        with self.assertRaises(Exception):
            store.all_records()


class QueryCacheTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.db_path = Path(self._tmpdir.name) / "queries.db"
        self.clock = MutableClock("2026-08-08T00:00:00+00:00")
        self.store = Store(self.db_path, clock=self.clock)
        self.addCleanup(self.store.close)

    def test_never_queried_is_absent(self):
        result = self.store.lookup_query(source="uspto", query="quantum computing", params={"max_results": 10})
        self.assertEqual(result.status, "absent")
        self.assertEqual(result.ids, ())
        self.assertIsNone(result.fetched_at)

    def test_zero_result_search_is_recorded_and_read_back_as_negative(self):
        self.store.upsert_query(
            source="uspto", query="q", params={"max_results": 10}, status="ok", record_type="patent", ids=[]
        )
        result = self.store.lookup_query(source="uspto", query="q", params={"max_results": 10})
        self.assertEqual(result.status, "negative")
        self.assertEqual(result.ids, ())
        self.assertEqual(result.fetched_at, "2026-08-08T00:00:00+00:00")

    def test_positive_result_round_trips_ids(self):
        self.store.upsert_query(
            source="uspto",
            query="q",
            params={"max_results": 10},
            status="ok",
            record_type="patent",
            ids=["US1", "US2"],
        )
        result = self.store.lookup_query(source="uspto", query="q", params={"max_results": 10})
        self.assertEqual(result.status, "positive")
        self.assertEqual(result.ids, ("US1", "US2"))
        self.assertEqual(result.record_type, "patent")

    def test_positive_result_does_not_expire(self):
        self.store.upsert_query(
            source="uspto", query="q", params={}, status="ok", record_type="patent", ids=["US1"]
        )
        self.clock.value = "2099-01-01T00:00:00+00:00"
        result = self.store.lookup_query(source="uspto", query="q", params={}, ttl_days=30)
        self.assertEqual(result.status, "positive")

    def test_rate_limited_is_never_readable_as_negative(self):
        self.store.upsert_query(source="github", query="q", params={}, status="rate_limited", record_type="repo")
        result = self.store.lookup_query(source="github", query="q", params={})
        self.assertEqual(result.status, "rate_limited")
        self.assertNotIn(result.status, ("negative", "positive", "expired"))

    def test_error_is_never_readable_as_negative(self):
        self.store.upsert_query(source="arxiv", query="q", params={}, status="error", record_type="paper")
        result = self.store.lookup_query(source="arxiv", query="q", params={})
        self.assertEqual(result.status, "error")

    def test_rate_limited_ignores_ttl_even_when_old(self):
        self.store.upsert_query(source="github", query="q", params={}, status="rate_limited", record_type="repo")
        self.clock.value = "2099-01-01T00:00:00+00:00"
        result = self.store.lookup_query(source="github", query="q", params={}, ttl_days=30)
        self.assertEqual(result.status, "rate_limited")

    def test_negative_within_ttl_is_not_expired(self):
        self.store.upsert_query(source="uspto", query="q", params={}, status="ok", record_type="patent", ids=[])
        self.clock.value = "2026-09-06T00:00:00+00:00"  # 29 days later
        result = self.store.lookup_query(source="uspto", query="q", params={}, ttl_days=30)
        self.assertEqual(result.status, "negative")

    def test_negative_past_ttl_reads_as_expired_not_absent(self):
        self.store.upsert_query(source="uspto", query="q", params={}, status="ok", record_type="patent", ids=[])
        self.clock.value = "2026-09-10T00:00:00+00:00"  # 33 days later
        result = self.store.lookup_query(source="uspto", query="q", params={}, ttl_days=30)
        self.assertEqual(result.status, "expired")
        self.assertNotEqual(result.status, "absent")

    def test_default_ttl_is_30_days(self):
        self.assertEqual(DEFAULT_NEGATIVE_TTL_DAYS, 30)
        self.store.upsert_query(source="uspto", query="q", params={}, status="ok", record_type="patent", ids=[])
        self.clock.value = "2026-09-10T00:00:00+00:00"  # 33 days later, no explicit ttl_days passed
        result = self.store.lookup_query(source="uspto", query="q", params={})
        self.assertEqual(result.status, "expired")

    def test_params_are_part_of_the_cache_key(self):
        self.store.upsert_query(
            source="uspto", query="q", params={"max_results": 10}, status="ok", record_type="patent", ids=["US1"]
        )
        result = self.store.lookup_query(source="uspto", query="q", params={"max_results": 20})
        self.assertEqual(result.status, "absent")

    def test_params_key_order_does_not_matter(self):
        self.store.upsert_query(
            source="uspto", query="q", params={"a": 1, "b": 2}, status="ok", record_type="patent", ids=["US1"]
        )
        result = self.store.lookup_query(source="uspto", query="q", params={"b": 2, "a": 1})
        self.assertEqual(result.status, "positive")

    def test_repeat_upsert_overwrites_the_cached_row(self):
        self.store.upsert_query(source="github", query="q", params={}, status="rate_limited", record_type="repo")
        self.store.upsert_query(
            source="github", query="q", params={}, status="ok", record_type="repo", ids=["owner/repo"]
        )
        result = self.store.lookup_query(source="github", query="q", params={})
        self.assertEqual(result.status, "positive")
        self.assertEqual(result.ids, ("owner/repo",))


if __name__ == "__main__":
    unittest.main()
