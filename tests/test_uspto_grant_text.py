import unittest
from pathlib import Path
from unittest.mock import patch

from einstein.patent_claims import independent_claims_for_record
from einstein.schema import Record
from einstein.uspto_grant_text import (
    GrantText,
    GrantTextError,
    fetch_grant_text,
    parse_grant_xml,
    record_with_grant_text,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
GRANT_XML = (FIXTURES_DIR / "uspto_grant_text.xml").read_bytes()
GRANT_XML_NO_ABSTRACT = (FIXTURES_DIR / "uspto_grant_text_no_abstract.xml").read_bytes()

FILE_URI = (
    "https://api.uspto.gov/api/v1/datasets/products/files/"
    "PTGRXML-SPLT/2026/ipg260915/19658124_12735810.xml"
)


def _granted_record(**raw_overrides):
    raw = {
        "grantDocumentMetaData": {"fileLocationURI": FILE_URI},
        "applicationMetaData": {"inventionTitle": "Widget", "patentNumber": "12735810"},
    }
    raw.update(raw_overrides)
    return Record(
        type="patent",
        id="12735810",
        title="Widget",
        summary="Widget",
        url="https://patents.google.com/patent/US12735810/en",
        ts="2026-09-15",
        raw=raw,
    )


class FakeResponse:
    def __init__(self, status_code, content=b"", reason="Error"):
        self.status_code = status_code
        self.content = content
        self.reason = reason


class FakeHttp:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


class ParseGrantXmlTest(unittest.TestCase):
    def test_parses_real_shaped_abstract_and_claims(self):
        result = parse_grant_xml(GRANT_XML)
        self.assertTrue(result.abstract.startswith("The disclosure provides systems"))
        self.assertTrue(result.claims_text.startswith("1. A method, comprising:"))
        self.assertIn("2. The method of claim 1,", result.claims_text)
        self.assertIn("3. The method of claim 2,", result.claims_text)

    def test_design_patent_has_no_abstract_element_and_that_is_not_an_error(self):
        result = parse_grant_xml(GRANT_XML_NO_ABSTRACT)
        self.assertEqual(result.abstract, "")
        self.assertEqual(result.claims_text, "1. The ornamental design for a massager, as shown and described.")

    def test_malformed_xml_raises_grant_text_error(self):
        with self.assertRaises(GrantTextError):
            parse_grant_xml(b"<not><valid xml")

    def test_claims_text_is_compatible_with_patent_claims_parser(self):
        # No format translation should be needed between this module's output
        # and einstein.patent_claims's parser -- confirmed against real grant
        # XML (einstein-tix); this locks that compatibility in as a test.
        result = parse_grant_xml(GRANT_XML)
        record = record_with_grant_text(_granted_record(), result)
        claims = independent_claims_for_record(record)
        self.assertEqual(len(claims), 1)
        self.assertTrue(claims[0].text.startswith("A method, comprising:"))


class FetchGrantTextTest(unittest.TestCase):
    def test_fetches_and_parses_via_file_location_uri(self):
        http = FakeHttp(FakeResponse(200, content=GRANT_XML))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            result = fetch_grant_text(_granted_record(), http=http)
        self.assertTrue(result.claims_text.startswith("1. A method"))
        url, kwargs = http.calls[0]
        self.assertEqual(url, FILE_URI)
        self.assertEqual(kwargs["headers"]["X-API-KEY"], "k")

    def test_missing_grant_document_metadata_raises(self):
        from dataclasses import replace

        record = _granted_record()
        raw = dict(record.raw)
        del raw["grantDocumentMetaData"]
        record = replace(record, raw=raw)
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            with self.assertRaises(GrantTextError):
                fetch_grant_text(record, http=FakeHttp(FakeResponse(200)))

    def test_non_patent_record_raises_value_error(self):
        record = Record(
            type="paper", id="x", title="t", summary="s", url="https://example.com", ts="2026-01-01", raw={}
        )
        with self.assertRaises(ValueError):
            fetch_grant_text(record, http=FakeHttp(FakeResponse(200)), api_key="k")

    def test_missing_api_key_raises_before_any_request(self):
        http = FakeHttp(FakeResponse(200, content=GRANT_XML))
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(GrantTextError):
                fetch_grant_text(_granted_record(), http=http)
        self.assertEqual(http.calls, [])

    def test_non_200_response_raises(self):
        http = FakeHttp(FakeResponse(404, reason="Not Found"))
        with patch.dict("os.environ", {"USPTO_API_KEY": "k"}, clear=True):
            with self.assertRaises(GrantTextError):
                fetch_grant_text(_granted_record(), http=http)


class RecordWithGrantTextTest(unittest.TestCase):
    def test_merges_claims_and_rebuilds_summary_with_abstract(self):
        grant_text = GrantText(abstract="A widget that does things.", claims_text="1. Claim one.")
        record = record_with_grant_text(_granted_record(), grant_text)
        self.assertEqual(record.summary, "Widget A widget that does things.")
        self.assertEqual(record.raw["claimsText"], "1. Claim one.")

    def test_empty_abstract_keeps_title_only_summary(self):
        grant_text = GrantText(abstract="", claims_text="1. Claim one.")
        record = record_with_grant_text(_granted_record(), grant_text)
        self.assertEqual(record.summary, "Widget")

    def test_empty_claims_text_does_not_add_claims_text_key(self):
        grant_text = GrantText(abstract="An abstract.", claims_text="")
        record = record_with_grant_text(_granted_record(), grant_text)
        self.assertNotIn("claimsText", record.raw)

    def test_original_record_is_not_mutated(self):
        original = _granted_record()
        record_with_grant_text(original, GrantText(abstract="x", claims_text="1. y"))
        self.assertEqual(original.summary, "Widget")
        self.assertNotIn("claimsText", original.raw)


if __name__ == "__main__":
    unittest.main()
