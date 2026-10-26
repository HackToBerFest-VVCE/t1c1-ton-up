"""Unit tests for field-aware revalidation and optional value preservation.

Verifies:
1. Math discrepancy is resolved when the user fixes amounts.
2. Untouched uncertainty flags (e.g. inferred year on dates) are preserved when editing amounts.
3. Editing an ambiguous or partial date to a full ISO date clears the date flag.
4. Blank, None, and NaN values for optional fields (customer, paid, due) remain strictly None.
5. Dynamic row addition with invalid inputs correctly generates review flags.
"""

import unittest
from decimal import Decimal

from core.models import ExtractedLedgerRow
from core.validator import (
    FLAG_AMBIGUOUS_DATE,
    FLAG_INFERRED_YEAR,
    FLAG_INVALID_DATE,
    FLAG_INVALID_QUANTITY,
    FLAG_LOW_CONFIDENCE,
    FLAG_MATH_MISMATCH,
    FLAG_MISSING_AMOUNT,
    FLAG_MISSING_ITEM,
    FLAG_NEGATIVE_VALUE,
    get_validator,
    revalidate_row,
)


class TestRevalidationEngine(unittest.TestCase):
    """Tests field-aware revalidation rules."""

    def setUp(self) -> None:
        self.validator = get_validator()

    def test_math_discrepancy_resolved_on_edit(self) -> None:
        """User corrects due amount to balance math -> FLAG_MATH_MISMATCH is cleared."""
        # Initial row: paid (40) + due (50) != amount (100)
        row = ExtractedLedgerRow(
            item="Toor Dal",
            amount=100.0,
            paid=40.0,
            due=50.0,
            date="2026-05-10",
        )
        self.validator.validate_row(row)
        self.assertIn(FLAG_MATH_MISMATCH, row.validation_flags)
        self.assertTrue(row.needs_review)

        # User edits due to 60.0 (40 + 60 == 100)
        row.due = 60.0
        revalidate_row(row)

        self.assertNotIn(FLAG_MATH_MISMATCH, row.validation_flags)
        self.assertFalse(row.needs_review)

    def test_untouched_date_flag_preserved_when_editing_amounts(self) -> None:
        """Editing amount should NOT clear date's INFERRED_YEAR flag."""
        # Initial row with partial date '15/08' -> triggers INFERRED_YEAR
        row = ExtractedLedgerRow(
            item="Sugar",
            amount=100.0,
            paid=40.0,
            due=50.0,
            date="15/08",
        )
        self.validator.validate_row(row)
        self.assertIn(FLAG_INFERRED_YEAR, row.validation_flags)
        self.assertIn(FLAG_MATH_MISMATCH, row.validation_flags)

        # User fixes due amount only
        row.due = 60.0
        revalidate_row(row)

        # Math mismatch resolved, but inferred year MUST be preserved
        self.assertNotIn(FLAG_MATH_MISMATCH, row.validation_flags)
        self.assertIn(FLAG_INFERRED_YEAR, row.validation_flags)
        self.assertTrue(row.needs_review)

    def test_editing_date_clears_date_warning_if_valid(self) -> None:
        """When user provides an explicit corrected date, uncertainty flags are cleared."""
        row = ExtractedLedgerRow(
            item="Sugar",
            amount=50.0,
            paid=50.0,
            due=0.0,
            date="15/08",  # Inferred current year
        )
        self.validator.validate_row(row)
        self.assertIn(FLAG_INFERRED_YEAR, row.validation_flags)

        # User explicitly changes the date to the correct historical date (2025-08-15)
        row.date = "2025-08-15"
        revalidate_row(row)

        self.assertNotIn(FLAG_INFERRED_YEAR, row.validation_flags)
        self.assertNotIn(FLAG_INVALID_DATE, row.validation_flags)
        self.assertFalse(row.needs_review)

    def test_optional_fields_remain_none(self) -> None:
        """Blank strings, 'None', 'NaN' for customer, paid, due remain None."""
        row = ExtractedLedgerRow(
            item="Rice",
            amount=120.0,
            customer="   ",
            customer_phone="",
            paid=None,
            due=None,
        )
        revalidate_row(row)

        self.assertIsNone(row.customer)
        self.assertIsNone(row.customer_phone)
        self.assertIsNone(row.paid)
        self.assertIsNone(row.due)

    def test_editing_item_clears_missing_item_and_updates_catalog(self) -> None:
        """Adding missing item name resolves MISSING_ITEM flag."""
        row = ExtractedLedgerRow(
            item="",
            amount=50.0,
        )
        self.validator.validate_row(row)
        self.assertIn(FLAG_MISSING_ITEM, row.validation_flags)

        # User enters item name
        row.item = "Toor Dal"
        revalidate_row(row)

        self.assertNotIn(FLAG_MISSING_ITEM, row.validation_flags)
        self.assertEqual(row.standardized_item, "Toor Dal")

    def test_dynamic_row_addition_and_validation(self) -> None:
        """Manually added row without original_data undergoes full validation."""
        new_row = ExtractedLedgerRow(
            row_id=99,
            item="",
            quantity=-2.0,
            amount=-50.0,
        )
        revalidate_row(new_row)

        self.assertIn(FLAG_MISSING_ITEM, new_row.validation_flags)
        self.assertIn(FLAG_INVALID_QUANTITY, new_row.validation_flags)
        self.assertIn(FLAG_NEGATIVE_VALUE, new_row.validation_flags)
        self.assertTrue(new_row.needs_review)

    def test_to_ledger_entry_conversion_preserves_none(self) -> None:
        """Conversion to LedgerEntry preserves None for optional fields."""
        row = ExtractedLedgerRow(
            item="Salt",
            amount=20.0,
            customer=None,
            paid=None,
            due=None,
            date="2026-05-01",
        )
        entry = row.to_ledger_entry(shop_id="shop-123", batch_id="batch-abc", row_index=1)

        self.assertEqual(entry.shop_id, "shop-123")
        self.assertEqual(entry.batch_id, "batch-abc")
        self.assertEqual(entry.row_index, 1)
        self.assertEqual(entry.item_name, "Salt")
        self.assertIsNone(entry.customer_name)
        self.assertIsNone(entry.customer_phone)
        self.assertIsNone(entry.paid_amount)
        self.assertIsNone(entry.due_amount)
        self.assertEqual(entry.total_amount, 20.0)


if __name__ == "__main__":
    unittest.main()
