"""Unit tests for decimal-safe data validation and human review assignment."""

import unittest
from datetime import datetime

from core.models import ExtractedLedgerRow
from core.validator import (
    FLAG_AMBIGUOUS_DATE,
    FLAG_CANDIDATE_READINGS,
    FLAG_INFERRED_YEAR,
    FLAG_INVALID_QUANTITY,
    FLAG_LOW_CONFIDENCE,
    FLAG_MATH_MISMATCH,
    FLAG_MISSING_AMOUNT,
    FLAG_MISSING_ITEM,
    FLAG_NEGATIVE_VALUE,
    LedgerValidator,
    get_validator,
    parse_and_validate_date,
    to_decimal,
)


class TestLedgerValidator(unittest.TestCase):
    """Verifies financial arithmetic, date sanity, and review flag assignment."""

    def setUp(self) -> None:
        self.validator = LedgerValidator(min_confidence_threshold=0.70)

    def test_decimal_safe_arithmetic_matching(self) -> None:
        # Perfectly consistent row: Paid (100.50) + Due (149.50) == Amount (250.00)
        row = ExtractedLedgerRow(
            item="Toor Dal",
            amount=250.00,
            paid=100.50,
            due=149.50,
            quantity=2.0,
            date="2026-10-24",
            field_confidence={"item": 0.95, "amount": 0.95},
        )
        validated = self.validator.validate_row(row)
        self.assertFalse(validated.needs_review)
        self.assertNotIn(FLAG_MATH_MISMATCH, validated.validation_flags)

    def test_math_discrepancy_flagging(self) -> None:
        # Discrepancy: Paid (100) + Due (100) = 200 != Amount (250)
        row = ExtractedLedgerRow(
            item="Sugar",
            amount=250.00,
            paid=100.00,
            due=100.00,
            quantity=5.0,
            date="2026-10-24",
        )
        validated = self.validator.validate_row(row)
        self.assertTrue(validated.needs_review)
        self.assertIn(FLAG_MATH_MISMATCH, validated.validation_flags)
        self.assertTrue(any("Financial discrepancy" in r for r in validated.review_reasons))

    def test_date_validation_inferred_year(self) -> None:
        # Date given without year: "24/10"
        canonical, flags, reasons = parse_and_validate_date("24/10")
        current_year = datetime.now().year
        self.assertEqual(canonical, f"{current_year}-10-24")
        self.assertIn(FLAG_INFERRED_YEAR, flags)

        row = ExtractedLedgerRow(
            item="Sunflower Oil",
            amount=140.00,
            paid=140.00,
            due=0.00,
            date="24/10",
        )
        validated = self.validator.validate_row(row)
        self.assertTrue(validated.needs_review)
        self.assertIn(FLAG_INFERRED_YEAR, validated.validation_flags)

    def test_valid_iso_date_no_flags(self) -> None:
        canonical, flags, reasons = parse_and_validate_date("2026-10-24")
        self.assertEqual(canonical, "2026-10-24")
        self.assertEqual(len(flags), 0)

    def test_low_confidence_triggers_review(self) -> None:
        # Item reading has confidence 0.52 (< threshold 0.70)
        row = ExtractedLedgerRow(
            item="Bele",
            amount=130.00,
            paid=0.00,
            due=130.00,
            field_confidence={"item": 0.52},
        )
        validated = self.validator.validate_row(row)
        self.assertTrue(validated.needs_review)
        self.assertIn(FLAG_LOW_CONFIDENCE, validated.validation_flags)

    def test_candidate_readings_triggers_review(self) -> None:
        # Multiple candidate readings detected for handwriting
        row = ExtractedLedgerRow(
            item="Bele",
            amount=130.00,
            candidate_readings={"item": ["Toor Dal", "Moong Dal"]},
        )
        validated = self.validator.validate_row(row)
        self.assertTrue(validated.needs_review)
        self.assertIn(FLAG_CANDIDATE_READINGS, validated.validation_flags)

    def test_negative_values_and_invalid_quantity(self) -> None:
        # Negative amount and negative quantity
        row = ExtractedLedgerRow(
            item="Salt",
            amount=-20.00,
            quantity=-1.0,
        )
        validated = self.validator.validate_row(row)
        self.assertTrue(validated.needs_review)
        self.assertIn(FLAG_NEGATIVE_VALUE, validated.validation_flags)
        self.assertIn(FLAG_INVALID_QUANTITY, validated.validation_flags)

    def test_missing_values_not_fabricated(self) -> None:
        # Missing quantity and customer should remain None
        row = ExtractedLedgerRow(
            item="Tea Powder",
            amount=130.00,
            quantity=None,
            customer=None,
            paid=None,
            due=None,
        )
        validated = self.validator.validate_row(row)
        self.assertIsNone(validated.quantity)
        self.assertIsNone(validated.customer)
        self.assertIsNone(validated.paid)
        self.assertIsNone(validated.due)


if __name__ == "__main__":
    unittest.main()
