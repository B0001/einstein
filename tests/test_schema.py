import unittest
from datetime import datetime

from einstein.schema import Record, RECORD_TYPES


def _valid_kwargs(**overrides):
    kwargs = dict(
        type="paper",
        id="2508.00001",
        title="A Paper",
        summary="An abstract.",
        url="https://arxiv.org/abs/2508.00001",
        ts="2026-08-08T00:00:00+00:00",
        raw={"source": "arxiv"},
    )
    kwargs.update(overrides)
    return kwargs


class RecordSchemaTest(unittest.TestCase):
    def test_all_three_types_construct(self):
        for record_type in RECORD_TYPES:
            record = Record(**_valid_kwargs(type=record_type))
            self.assertEqual(record.type, record_type)

    def test_empty_summary_is_allowed(self):
        record = Record(**_valid_kwargs(summary=""))
        self.assertEqual(record.summary, "")

    def test_ts_round_trips_through_fromisoformat(self):
        record = Record(**_valid_kwargs())
        self.assertIsInstance(datetime.fromisoformat(record.ts), datetime)

    def test_rejects_unknown_type(self):
        with self.assertRaises(AssertionError):
            Record(**_valid_kwargs(type="song"))

    def test_rejects_empty_id(self):
        with self.assertRaises(AssertionError):
            Record(**_valid_kwargs(id=""))

    def test_rejects_empty_title(self):
        with self.assertRaises(AssertionError):
            Record(**_valid_kwargs(title=""))

    def test_rejects_empty_url(self):
        with self.assertRaises(AssertionError):
            Record(**_valid_kwargs(url=""))

    def test_rejects_non_iso_ts(self):
        with self.assertRaises(AssertionError):
            Record(**_valid_kwargs(ts="not-a-date"))

    def test_rejects_non_dict_raw(self):
        with self.assertRaises(AssertionError):
            Record(**_valid_kwargs(raw=None))

    def test_record_is_frozen(self):
        record = Record(**_valid_kwargs())
        with self.assertRaises(AttributeError):
            record.title = "mutated"


if __name__ == "__main__":
    unittest.main()
