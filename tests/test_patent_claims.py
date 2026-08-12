import unittest

from einstein.patent_claims import (
    Claim,
    ClaimsNotFoundError,
    independent_claims,
    independent_claims_for_record,
    parse_claims,
)
from einstein.schema import Record

# A realistic-shaped claim set: two independent claims (1 system, 5 method),
# plus dependents exercising single refs, "and" lists, hyphen ranges, "to"
# ranges, and "any of the preceding claims".
CLAIMS_TEXT = """
1. A system comprising: a processor; and a memory storing instructions
that, when executed by the processor, cause the system to perform quantum
error correction on a plurality of qubits.

2. The system of claim 1, wherein the processor is a quantum processing
unit.

3. The system of claim 1, further comprising a classical control unit
coupled to the processor.

4. The system of claim 2, wherein the quantum processing unit comprises a
plurality of superconducting qubits.

5. A method comprising: receiving quantum state data; and applying an
error correction code to the quantum state data to produce corrected state
data.

6. The method of claim 5, wherein the error correction code is a surface
code.

7. The method of claims 5 and 6, further comprising outputting the
corrected state data to a classical readout circuit.

8. The method of claims 5-6, wherein the quantum state data is received
from a plurality of qubits.

9. The method of claim 5, wherein applying the error correction code
comprises applying it to claims 5 to 6 style syndrome data.

10. The system of any of the preceding claims, wherein the memory is
non-volatile.
"""


def patent_record(raw: dict) -> Record:
    return Record(
        type="patent",
        id="US11234567",
        title="System and Method for Quantum Error Correction",
        summary="A system for correcting errors in a quantum processor.",
        url="https://patents.google.com/patent/US11234567/en",
        ts="2023-05-16T00:00:00+00:00",
        raw=raw,
    )


class ParseClaimsTest(unittest.TestCase):
    def test_empty_text_returns_empty_list(self):
        self.assertEqual(parse_claims(""), [])
        self.assertEqual(parse_claims("   \n  "), [])

    def test_text_with_no_numbered_claims_raises(self):
        with self.assertRaises(ClaimsNotFoundError):
            parse_claims("A system for correcting errors in a quantum processor.")

    def test_splits_into_correct_count_and_numbers(self):
        claims = parse_claims(CLAIMS_TEXT)
        self.assertEqual([c.number for c in claims], list(range(1, 11)))

    def test_independent_claims_have_no_references(self):
        claims = {c.number: c for c in parse_claims(CLAIMS_TEXT)}
        self.assertTrue(claims[1].is_independent)
        self.assertEqual(claims[1].depends_on, ())
        self.assertTrue(claims[5].is_independent)
        self.assertEqual(claims[5].depends_on, ())

    def test_single_reference_dependent_claim(self):
        claims = {c.number: c for c in parse_claims(CLAIMS_TEXT)}
        self.assertFalse(claims[2].is_independent)
        self.assertEqual(claims[2].depends_on, (1,))
        self.assertFalse(claims[3].is_independent)
        self.assertEqual(claims[3].depends_on, (1,))
        self.assertFalse(claims[4].is_independent)
        self.assertEqual(claims[4].depends_on, (2,))
        self.assertFalse(claims[6].is_independent)
        self.assertEqual(claims[6].depends_on, (5,))

    def test_and_list_reference(self):
        claims = {c.number: c for c in parse_claims(CLAIMS_TEXT)}
        self.assertEqual(claims[7].depends_on, (5, 6))

    def test_hyphen_range_reference(self):
        claims = {c.number: c for c in parse_claims(CLAIMS_TEXT)}
        self.assertEqual(claims[8].depends_on, (5, 6))

    def test_to_range_reference_mid_sentence(self):
        # Claim 9 references "claim 5" (the real dependency) and mentions
        # "claims 5 to 6" again later in descriptive text -- both mentions
        # resolve to the same claim numbers, so depends_on is still {5, 6}.
        claims = {c.number: c for c in parse_claims(CLAIMS_TEXT)}
        self.assertEqual(claims[9].depends_on, (5, 6))

    def test_preceding_claims_phrase_expands_to_all_prior_numbers(self):
        claims = {c.number: c for c in parse_claims(CLAIMS_TEXT)}
        self.assertFalse(claims[10].is_independent)
        self.assertEqual(claims[10].depends_on, tuple(range(1, 10)))

    def test_claim_text_excludes_leading_number(self):
        claims = {c.number: c for c in parse_claims(CLAIMS_TEXT)}
        self.assertTrue(claims[1].text.startswith("A system comprising"))

    def test_parenthetical_dot_numbering_supported(self):
        text = "1) A widget.\n\n2) The widget of claim 1, further comprising a handle.\n"
        claims = parse_claims(text)
        self.assertEqual(len(claims), 2)
        self.assertTrue(claims[0].is_independent)
        self.assertEqual(claims[1].depends_on, (1,))

    def test_status_prefix_does_not_break_parsing(self):
        text = (
            "1. (Currently Amended) A widget comprising a housing.\n\n"
            "2. (Original) The widget of claim 1, wherein the housing is metal.\n"
        )
        claims = {c.number: c for c in parse_claims(text)}
        self.assertTrue(claims[1].is_independent)
        self.assertEqual(claims[2].depends_on, (1,))

    def test_canceled_claim_is_not_independent(self):
        text = "1. A widget comprising a housing.\n\n2. (Canceled)\n\n3. The widget of claim 1, wherein the housing is metal.\n"
        claims = {c.number: c for c in parse_claims(text)}
        self.assertTrue(claims[2].canceled)
        self.assertFalse(claims[2].is_independent)
        self.assertEqual(claims[2].depends_on, ())

    def test_returns_claim_dataclass_instances(self):
        claims = parse_claims("1. A widget.\n")
        self.assertIsInstance(claims[0], Claim)


class IndependentClaimsTest(unittest.TestCase):
    def test_filters_to_only_independent(self):
        result = independent_claims(CLAIMS_TEXT)
        self.assertEqual([c.number for c in result], [1, 5])
        self.assertTrue(all(c.is_independent for c in result))

    def test_empty_text_returns_empty_list(self):
        self.assertEqual(independent_claims(""), [])

    def test_all_dependent_returns_empty_list(self):
        # Malformed/unusual but should not crash: every claim depends on
        # something, independent_claims should just come back empty.
        text = "1. The gizmo of claim 2, wherein X.\n\n2. The gizmo of claim 1, wherein Y.\n"
        self.assertEqual(independent_claims(text), [])


class IndependentClaimsForRecordTest(unittest.TestCase):
    def test_reads_default_claims_text_field(self):
        record = patent_record({"claimsText": CLAIMS_TEXT})
        result = independent_claims_for_record(record)
        self.assertEqual([c.number for c in result], [1, 5])

    def test_reads_alternate_default_field_name(self):
        record = patent_record({"claimText": "1. A widget.\n"})
        result = independent_claims_for_record(record)
        self.assertEqual([c.number for c in result], [1])

    def test_explicit_claims_field_overrides_defaults(self):
        record = patent_record({"weirdFieldName": "1. A widget.\n"})
        result = independent_claims_for_record(record, claims_field="weirdFieldName")
        self.assertEqual([c.number for c in result], [1])

    def test_explicit_claims_text_bypasses_raw_lookup(self):
        record = patent_record({})
        result = independent_claims_for_record(record, claims_text="1. A widget.\n")
        self.assertEqual([c.number for c in result], [1])

    def test_missing_claim_text_raises_not_falls_back_to_abstract(self):
        record = patent_record({})
        with self.assertRaises(ClaimsNotFoundError) as ctx:
            independent_claims_for_record(record)
        self.assertIn(record.id, str(ctx.exception))

    def test_non_patent_record_raises_value_error(self):
        paper = Record(
            type="paper",
            id="2508.00001",
            title="A Paper",
            summary="text",
            url="https://arxiv.org/abs/2508.00001",
            ts="2026-08-08T00:00:00+00:00",
            raw={"claimsText": "1. A widget.\n"},
        )
        with self.assertRaises(ValueError):
            independent_claims_for_record(paper)


if __name__ == "__main__":
    unittest.main()
