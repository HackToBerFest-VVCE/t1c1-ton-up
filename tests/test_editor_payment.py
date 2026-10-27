"""Unit tests for interactive Paid and Due payment handling in Transaction Review Table.

Validates:
1. Fully paid transaction (Paid = Total, Due = 0, status = ✅ Fully Paid, cash transaction).
2. Partial payment with due amount (Due = 500, Total = 1050 -> Paid = 550, status = ❌ Has Due, credit transaction).
3. Due equal to total (Due = Total -> Paid = 0, status = ❌ Has Due, 100% credit).
4. Due equal to zero treated as fully paid (Due = 0 -> Paid = Total, status = ✅ Fully Paid).
5. Negative due (clamped to 0, treated as fully paid) and Due > Total (clamped to Total, Paid = 0). Paid never negative.
6. Recalculation after editing total amount (upward and downward adjustments, maintaining financial consistency).
7. Supabase save payload integrity, dashboard calculations without double-counting, and Excel export generation.
"""

from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock

import pandas as pd

from core.analytics import calculate_customer_balances, calculate_sales_summary
from core.db import save_confirmed_entries
from core.exporter import export_ledger_to_excel
from core.models import EntryStatus, ExtractedLedgerRow, TransactionType
from core.validator import (
    FLAG_MATH_MISMATCH,
    FLAG_NEGATIVE_VALUE,
    STATUS_FULLY_PAID,
    STATUS_HAS_DUE,
    calculate_payment_split,
    get_validator,
    revalidate_row,
    sync_editor_rows_with_changes,
    update_row_payment,
)


class TestPaymentHandlingAndIntegrity(unittest.TestCase):
    """Test suite for interactive payment handling and downstream data integrity."""

    def setUp(self) -> None:
        self.validator = get_validator()

    def test_01_fully_paid_transaction(self) -> None:
        """When user selects ✅ Fully Paid: Paid = Total, Due = 0, cash transaction."""
        total = 1000.0
        status, paid, due = calculate_payment_split(
            total_amount=total,
            payment_status=STATUS_FULLY_PAID,
            due_amount=200.0,  # Any previous due amount should be ignored/reset to 0
        )
        self.assertEqual(status, STATUS_FULLY_PAID)
        self.assertEqual(paid, 1000.0)
        self.assertEqual(due, 0.0)

        # Update ExtractedLedgerRow
        row = ExtractedLedgerRow(
            row_id=1,
            item="Basmati Rice",
            amount=total,
            date="2026-10-27",
        )
        update_row_payment(row, is_fully_paid=True)

        self.assertEqual(row.payment_status, STATUS_FULLY_PAID)
        self.assertEqual(row.paid, 1000.0)
        self.assertEqual(row.due, 0.0)
        self.assertNotIn(FLAG_MATH_MISMATCH, row.validation_flags)
        self.assertNotIn(FLAG_NEGATIVE_VALUE, row.validation_flags)

        # Check conversion to database LedgerEntry
        entry = row.to_ledger_entry(shop_id="shop-123")
        self.assertEqual(entry.total_amount, 1000.0)
        self.assertEqual(entry.paid_amount, 1000.0)
        self.assertEqual(entry.due_amount, 0.0)
        self.assertEqual(entry.transaction_type, TransactionType.CASH)

    def test_02_partial_payment_with_due_amount(self) -> None:
        """When user selects ❌ Has Due: Paid = Total - Due (Total=1050, Due=500 -> Paid=550)."""
        total = 1050.0
        due_input = 500.0
        status, paid, due = calculate_payment_split(
            total_amount=total,
            payment_status=STATUS_HAS_DUE,
            due_amount=due_input,
        )
        self.assertEqual(status, STATUS_HAS_DUE)
        self.assertEqual(paid, 550.0)
        self.assertEqual(due, 500.0)
        self.assertEqual(paid + due, total)

        row = ExtractedLedgerRow(
            row_id=2,
            item="Toor Dal 5kg",
            amount=total,
            customer="Ramesh",
            date="2026-10-27",
        )
        update_row_payment(row, is_fully_paid=False, due_amount=due_input)

        self.assertEqual(row.payment_status, STATUS_HAS_DUE)
        self.assertEqual(row.paid, 550.0)
        self.assertEqual(row.due, 500.0)
        self.assertNotIn(FLAG_MATH_MISMATCH, row.validation_flags)

        entry = row.to_ledger_entry(shop_id="shop-123")
        self.assertEqual(entry.total_amount, 1050.0)
        self.assertEqual(entry.paid_amount, 550.0)
        self.assertEqual(entry.due_amount, 500.0)
        self.assertEqual(entry.transaction_type, TransactionType.CREDIT)

    def test_03_due_equal_to_total(self) -> None:
        """When Due is equal to Total: Paid = 0, Due = Total (100% credit)."""
        total = 800.0
        status, paid, due = calculate_payment_split(
            total_amount=total,
            payment_status=STATUS_HAS_DUE,
            due_amount=800.0,
        )
        self.assertEqual(status, STATUS_HAS_DUE)
        self.assertEqual(paid, 0.0)
        self.assertEqual(due, 800.0)

        row = ExtractedLedgerRow(
            row_id=3,
            item="Sunflower Oil 5L",
            amount=total,
            customer="Suresh",
            date="2026-10-27",
        )
        update_row_payment(row, payment_status=STATUS_HAS_DUE, due_amount=800.0)

        self.assertEqual(row.paid, 0.0)
        self.assertEqual(row.due, 800.0)
        self.assertEqual(row.payment_status, STATUS_HAS_DUE)
        self.assertNotIn(FLAG_MATH_MISMATCH, row.validation_flags)
        self.assertNotIn(FLAG_NEGATIVE_VALUE, row.validation_flags)

        entry = row.to_ledger_entry(shop_id="shop-123")
        self.assertEqual(entry.paid_amount, 0.0)
        self.assertEqual(entry.due_amount, 800.0)
        self.assertEqual(entry.transaction_type, TransactionType.CREDIT)

    def test_04_due_equal_to_zero_treated_as_fully_paid(self) -> None:
        """Rule: If Due = 0, treat as fully paid."""
        total = 450.0
        status, paid, due = calculate_payment_split(
            total_amount=total,
            payment_status=STATUS_HAS_DUE,
            due_amount=0.0,  # User typed 0
        )
        self.assertEqual(status, STATUS_FULLY_PAID)
        self.assertEqual(paid, 450.0)
        self.assertEqual(due, 0.0)

        row = ExtractedLedgerRow(
            row_id=4,
            item="Atta 10kg",
            amount=total,
            date="2026-10-27",
        )
        update_row_payment(row, payment_status=STATUS_HAS_DUE, due_amount=0.0)

        self.assertEqual(row.payment_status, STATUS_FULLY_PAID)
        self.assertEqual(row.paid, 450.0)
        self.assertEqual(row.due, 0.0)
        self.assertNotIn(FLAG_MATH_MISMATCH, row.validation_flags)

        entry = row.to_ledger_entry(shop_id="shop-123")
        self.assertEqual(entry.transaction_type, TransactionType.CASH)

    def test_05_negative_due_and_due_greater_than_total(self) -> None:
        """Validation: 0 <= Due <= Total, Paid must never be negative."""
        total = 1000.0

        # Case A: Negative Due (-100) -> clamped to 0.0 -> treated as fully paid
        status_neg, paid_neg, due_neg = calculate_payment_split(
            total_amount=total,
            payment_status=STATUS_HAS_DUE,
            due_amount=-100.0,
        )
        self.assertEqual(due_neg, 0.0)
        self.assertEqual(paid_neg, 1000.0)
        self.assertEqual(status_neg, STATUS_FULLY_PAID)
        self.assertGreaterEqual(paid_neg, 0.0)

        # Case B: Due > Total (1500 > 1000) -> clamped to Total (1000) -> Paid = 0
        status_over, paid_over, due_over = calculate_payment_split(
            total_amount=total,
            payment_status=STATUS_HAS_DUE,
            due_amount=1500.0,
        )
        self.assertEqual(due_over, 1000.0)
        self.assertEqual(paid_over, 0.0)
        self.assertEqual(status_over, STATUS_HAS_DUE)
        self.assertGreaterEqual(paid_over, 0.0)

        # In row model
        row = ExtractedLedgerRow(item="Sugar", amount=total, date="2026-10-27")
        update_row_payment(row, payment_status=STATUS_HAS_DUE, due_amount=1500.0)
        self.assertEqual(row.due, 1000.0)
        self.assertEqual(row.paid, 0.0)
        self.assertNotIn(FLAG_NEGATIVE_VALUE, row.validation_flags)
        self.assertNotIn(FLAG_MATH_MISMATCH, row.validation_flags)

    def test_06_recalculation_after_editing_total(self) -> None:
        """Recalculate values whenever Total or Due changes."""
        # Initial: Total 1050, Due 500, Paid 550
        status, paid, due = calculate_payment_split(1050.0, STATUS_HAS_DUE, 500.0)
        self.assertEqual(paid, 550.0)
        self.assertEqual(due, 500.0)

        # Edit Total up to 1500 (Due stays 500 -> Paid becomes 1000)
        status_up, paid_up, due_up = calculate_payment_split(1500.0, STATUS_HAS_DUE, 500.0)
        self.assertEqual(status_up, STATUS_HAS_DUE)
        self.assertEqual(due_up, 500.0)
        self.assertEqual(paid_up, 1000.0)

        # Edit Total down to 400 (Due was 500 > 400 -> Due clamped to 400, Paid = 0)
        status_down, paid_down, due_down = calculate_payment_split(400.0, STATUS_HAS_DUE, 500.0)
        self.assertEqual(status_down, STATUS_HAS_DUE)
        self.assertEqual(due_down, 400.0)
        self.assertEqual(paid_down, 0.0)

        # Edit Total on Fully Paid transaction (1000 -> 1200)
        status_fp, paid_fp, due_fp = calculate_payment_split(1200.0, STATUS_FULLY_PAID, 0.0)
        self.assertEqual(status_fp, STATUS_FULLY_PAID)
        self.assertEqual(due_fp, 0.0)
        self.assertEqual(paid_fp, 1200.0)

        # Blank or invalid numeric inputs handled safely
        status_safe, paid_safe, due_safe = calculate_payment_split(None, STATUS_FULLY_PAID, None)
        self.assertEqual(paid_safe, 0.0)
        self.assertEqual(due_safe, 0.0)

    def test_07_supabase_save_dashboard_and_excel_export(self) -> None:
        """Verifies Supabase save payload, dashboard summary calculations, and Excel export."""
        # 3 confirmed rows:
        # Row 1: Ramesh, Sugar: Total 1050, Due 500, Paid 550
        # Row 2: Suresh, Rice: Total 600, Due 0, Paid 600
        # Row 3: Ramesh, Tea: Total 400, Due 400, Paid 0
        r1 = ExtractedLedgerRow(
            row_id=1,
            item="Sugar",
            customer="Ramesh",
            amount=1050.0,
            date="2026-10-26",
        )
        update_row_payment(r1, payment_status=STATUS_HAS_DUE, due_amount=500.0)

        r2 = ExtractedLedgerRow(
            row_id=2,
            item="Rice",
            customer="Suresh",
            amount=600.0,
            date="2026-10-26",
        )
        update_row_payment(r2, is_fully_paid=True)

        r3 = ExtractedLedgerRow(
            row_id=3,
            item="Tea",
            customer="Ramesh",
            amount=400.0,
            date="2026-10-27",
        )
        update_row_payment(r3, payment_status=STATUS_HAS_DUE, due_amount=400.0)

        entries = [
            r1.to_ledger_entry(shop_id="shop-123", batch_id="batch-001"),
            r2.to_ledger_entry(shop_id="shop-123", batch_id="batch-001"),
            r3.to_ledger_entry(shop_id="shop-123", batch_id="batch-001"),
        ]

        # 1. Supabase save payload inspection (mocked client)
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.select.return_value.eq.return_value.execute.return_value.data = [{"id": "shop-123"}]
        mock_table.select.return_value.eq.return_value.eq.return_value.execute.return_value.data = []
        mock_table.insert.return_value.execute.return_value.data = [{"id": "1"}, {"id": "2"}, {"id": "3"}]

        success, msg, count = save_confirmed_entries(
            shop_id="shop-123",
            entries=entries,
            batch_id="batch-001",
            client=mock_client,
        )
        self.assertTrue(success)
        self.assertEqual(count, 3)

        # Verify insert payload structure
        call_args = mock_table.insert.call_args[0][0]
        self.assertEqual(len(call_args), 3)

        # Row 1 payload check
        self.assertEqual(call_args[0]["customer_name"], "Ramesh")
        self.assertEqual(call_args[0]["total_amount"], 1050.0)
        self.assertEqual(call_args[0]["paid_amount"], 550.0)
        self.assertEqual(call_args[0]["due_amount"], 500.0)
        self.assertEqual(call_args[0]["transaction_type"], "credit")

        # Row 2 payload check
        self.assertEqual(call_args[1]["customer_name"], "Suresh")
        self.assertEqual(call_args[1]["total_amount"], 600.0)
        self.assertEqual(call_args[1]["paid_amount"], 600.0)
        self.assertEqual(call_args[1]["due_amount"], 0.0)
        self.assertEqual(call_args[1]["transaction_type"], "cash")

        # 2. Dashboard Sales Summary check
        summary = calculate_sales_summary(entries)
        self.assertEqual(summary["total_sales"], 2050.0)
        self.assertEqual(summary["total_collected"], 1150.0)
        self.assertEqual(summary["total_due"], 900.0)
        self.assertEqual(summary["cash_sales"], 600.0)
        self.assertEqual(summary["credit_sales"], 1450.0)
        self.assertEqual(summary["transaction_count"], 3)
        # Check no discrepancy: total_collected + total_due == total_sales
        self.assertEqual(summary["total_collected"] + summary["total_due"], summary["total_sales"])

        # 3. Customer Dues check (no double-counting)
        balances = calculate_customer_balances(entries)
        self.assertEqual(len(balances), 2)
        ramesh_bal = next(b for b in balances if b["customer_name"] == "Ramesh")
        suresh_bal = next(b for b in balances if b["customer_name"] == "Suresh")

        self.assertEqual(ramesh_bal["total_billed"], 1450.0)
        self.assertEqual(ramesh_bal["total_paid"], 550.0)
        self.assertEqual(ramesh_bal["current_due"], 900.0)
        self.assertEqual(ramesh_bal["transaction_count"], 2)

        self.assertEqual(suresh_bal["total_billed"], 600.0)
        self.assertEqual(suresh_bal["total_paid"], 600.0)
        self.assertEqual(suresh_bal["current_due"], 0.0)
        self.assertEqual(suresh_bal["transaction_count"], 1)

        # 4. Excel Export check
        excel_data = [
            {
                "entry_date": e.entry_date,
                "customer_name": e.customer_name,
                "customer_phone": e.customer_phone,
                "item_name": e.item_name,
                "quantity": e.quantity,
                "unit": e.unit,
                "unit_price": e.unit_price,
                "total_amount": e.total_amount,
                "paid_amount": e.paid_amount,
                "due_amount": e.due_amount,
                "transaction_type": e.transaction_type.value,
                "status": "confirmed",
                "batch_id": e.batch_id,
            }
            for e in entries
        ]
        excel_bytes = export_ledger_to_excel(excel_data, shop_name="Kirana Store")
        self.assertIsInstance(excel_bytes, bytes)
        self.assertGreater(len(excel_bytes), 100)

        # Verify sheets inside generated Excel
        df_tx = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="Transactions")
        df_dues = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="Customer Dues")

        self.assertEqual(len(df_tx), 3)
        self.assertIn("Paid Amount (INR)", df_tx.columns)
        self.assertIn("Due Amount (INR)", df_tx.columns)
        self.assertEqual(df_tx["Paid Amount (INR)"].sum(), 1150.0)
        self.assertEqual(df_tx["Due Amount (INR)"].sum(), 900.0)

        self.assertEqual(len(df_dues), 2)
        ramesh_excel = df_dues[df_dues["Customer Name"] == "Ramesh"].iloc[0]
        self.assertEqual(ramesh_excel["Current Due (INR)"], 900.0)
        self.assertEqual(ramesh_excel["Total Billed (INR)"], 1450.0)

    def test_08_exact_sugar_case_total_42_due_10_paid_32(self) -> None:
        """Regression test for the exact Sugar bug: Total 42, Due 10 -> Paid MUST be 32 (not 0)."""
        row = ExtractedLedgerRow(
            row_id=1,
            item="Sugar",
            amount=42.0,
            payment_status=STATUS_FULLY_PAID,
            paid=42.0,
            due=0.0,
            date="2026-10-27",
            customer="Geetha",
        )
        rows = [row]

        # Case A: User selects '❌ Has Due' and enters Due = 10
        diff = {
            "edited_rows": {
                0: {"payment": STATUS_HAS_DUE, "due": 10.0}
            }
        }
        synced = sync_editor_rows_with_changes(rows, diff)
        self.assertEqual(len(synced), 1)
        sugar_row = synced[0]
        self.assertEqual(sugar_row.amount, 42.0)
        self.assertEqual(sugar_row.due, 10.0)
        self.assertEqual(sugar_row.paid, 32.0)
        self.assertEqual(sugar_row.payment_status, STATUS_HAS_DUE)
        self.assertNotIn(FLAG_MATH_MISMATCH, sugar_row.validation_flags)

        # Database LedgerEntry conversion check
        entry = sugar_row.to_ledger_entry(shop_id="shop-test")
        self.assertEqual(entry.total_amount, 42.0)
        self.assertEqual(entry.due_amount, 10.0)
        self.assertEqual(entry.paid_amount, 32.0)
        self.assertEqual(entry.transaction_type, TransactionType.CREDIT)

        # Case B: Sequential edit - row was already '❌ Has Due' with due=42, user edits Due to 10
        row_has_due = ExtractedLedgerRow(
            row_id=1,
            item="Sugar",
            amount=42.0,
            payment_status=STATUS_HAS_DUE,
            paid=0.0,
            due=42.0,
            date="2026-10-27",
        )
        diff_due_only = {
            "edited_rows": {
                0: {"due": 10.0}
            }
        }
        synced_b = sync_editor_rows_with_changes([row_has_due], diff_due_only)
        self.assertEqual(synced_b[0].paid, 32.0)
        self.assertEqual(synced_b[0].due, 10.0)
        self.assertEqual(synced_b[0].payment_status, STATUS_HAS_DUE)

    def test_09_multiple_simultaneous_row_edits(self) -> None:
        """Every row independently recalculates Paid = Total - Due without cross-row dependency."""
        r1 = ExtractedLedgerRow(row_id=1, item="Sugar", amount=42.0, payment_status=STATUS_FULLY_PAID, paid=42.0, due=0.0)
        r2 = ExtractedLedgerRow(row_id=2, item="Rice", amount=100.0, payment_status=STATUS_FULLY_PAID, paid=100.0, due=0.0)
        r3 = ExtractedLedgerRow(row_id=3, item="Tea", amount=50.0, payment_status=STATUS_HAS_DUE, paid=0.0, due=50.0)
        rows = [r1, r2, r3]

        diff = {
            "edited_rows": {
                0: {"payment": STATUS_HAS_DUE, "due": 10.0},
                1: {"payment": STATUS_HAS_DUE, "due": 30.0},
                2: {"payment": STATUS_FULLY_PAID, "due": 0.0},
            }
        }
        synced = sync_editor_rows_with_changes(rows, diff)
        self.assertEqual(len(synced), 3)

        # Row 0: Sugar: 42 - 10 = 32
        self.assertEqual(synced[0].paid, 32.0)
        self.assertEqual(synced[0].due, 10.0)
        self.assertEqual(synced[0].payment_status, STATUS_HAS_DUE)

        # Row 1: Rice: 100 - 30 = 70
        self.assertEqual(synced[1].paid, 70.0)
        self.assertEqual(synced[1].due, 30.0)
        self.assertEqual(synced[1].payment_status, STATUS_HAS_DUE)

        # Row 2: Tea: 50 - 0 = 50 (switched to Fully Paid)
        self.assertEqual(synced[2].paid, 50.0)
        self.assertEqual(synced[2].due, 0.0)
        self.assertEqual(synced[2].payment_status, STATUS_FULLY_PAID)

    def test_10_repeated_edits_same_row(self) -> None:
        """Sequential and repeated edits to the same row maintain consistent calculations."""
        row = ExtractedLedgerRow(row_id=1, item="Sugar", amount=42.0, payment_status=STATUS_FULLY_PAID, paid=42.0, due=0.0)
        rows = [row]

        # Edit 1: Due = 10 -> Paid = 32
        synced = sync_editor_rows_with_changes(rows, {"edited_rows": {0: {"due": 10.0, "payment": STATUS_HAS_DUE}}})
        self.assertEqual(synced[0].paid, 32.0)
        self.assertEqual(synced[0].due, 10.0)

        # Edit 2: Due = 20 -> Paid = 22
        synced = sync_editor_rows_with_changes(synced, {"edited_rows": {0: {"due": 20.0}}})
        self.assertEqual(synced[0].paid, 22.0)
        self.assertEqual(synced[0].due, 20.0)

        # Edit 3: Switch back to Fully Paid -> Paid = 42, Due = 0
        synced = sync_editor_rows_with_changes(synced, {"edited_rows": {0: {"payment": STATUS_FULLY_PAID}}})
        self.assertEqual(synced[0].paid, 42.0)
        self.assertEqual(synced[0].due, 0.0)
        self.assertEqual(synced[0].payment_status, STATUS_FULLY_PAID)

        # Edit 4: Switch to Has Due (no due specified) -> Due = 42, Paid = 0
        synced = sync_editor_rows_with_changes(synced, {"edited_rows": {0: {"payment": STATUS_HAS_DUE}}})
        self.assertEqual(synced[0].paid, 0.0)
        self.assertEqual(synced[0].due, 42.0)

        # Edit 5: Due = 15 -> Paid = 27
        synced = sync_editor_rows_with_changes(synced, {"edited_rows": {0: {"due": 15.0}}})
        self.assertEqual(synced[0].paid, 27.0)
        self.assertEqual(synced[0].due, 15.0)

    def test_11_due_validation_and_no_silent_overwrites(self) -> None:
        """Valid user-entered Due is never overwritten, and invalid values (negative or > total) are clamped."""
        row = ExtractedLedgerRow(row_id=1, item="Sugar", amount=42.0, payment_status=STATUS_FULLY_PAID, paid=42.0, due=0.0)

        # Negative Due -> clamped to 0 -> Treated as Fully Paid
        synced_neg = sync_editor_rows_with_changes([row], {"edited_rows": {0: {"payment": STATUS_HAS_DUE, "due": -10.0}}})
        self.assertEqual(synced_neg[0].due, 0.0)
        self.assertEqual(synced_neg[0].paid, 42.0)
        self.assertEqual(synced_neg[0].payment_status, STATUS_FULLY_PAID)

        # Due > Total (50 > 42) -> clamped to Total (42) -> Paid = 0
        synced_over = sync_editor_rows_with_changes([row], {"edited_rows": {0: {"payment": STATUS_HAS_DUE, "due": 50.0}}})
        self.assertEqual(synced_over[0].due, 42.0)
        self.assertEqual(synced_over[0].paid, 0.0)
        self.assertEqual(synced_over[0].payment_status, STATUS_HAS_DUE)

        # Valid Due (10.0) -> Preserved exactly as 10.0, never overwritten
        synced_valid = sync_editor_rows_with_changes([row], {"edited_rows": {0: {"payment": STATUS_HAS_DUE, "due": 10.0}}})
        self.assertEqual(synced_valid[0].due, 10.0)
        self.assertEqual(synced_valid[0].paid, 32.0)

    def test_12_downstream_analytics_and_export_consistency(self) -> None:
        """Downstream analytics, balances, and Excel export receive the exact recalculated values."""
        row = ExtractedLedgerRow(
            row_id=1,
            item="Sugar",
            amount=42.0,
            payment_status=STATUS_FULLY_PAID,
            paid=42.0,
            due=0.0,
            customer="Ramesh",
            date="2026-10-27",
        )
        synced = sync_editor_rows_with_changes([row], {"edited_rows": {0: {"payment": STATUS_HAS_DUE, "due": 10.0}}})
        entries = [synced[0].to_ledger_entry(shop_id="shop-abc")]

        # Analytics summary
        summary = calculate_sales_summary(entries)
        self.assertEqual(summary["total_sales"], 42.0)
        self.assertEqual(summary["total_collected"], 32.0)
        self.assertEqual(summary["total_due"], 10.0)

        # Customer balances
        balances = calculate_customer_balances(entries)
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0]["current_due"], 10.0)
        self.assertEqual(balances[0]["total_paid"], 32.0)
        self.assertEqual(balances[0]["total_billed"], 42.0)

        # Excel export
        excel_data = [
            {
                "entry_date": e.entry_date,
                "customer_name": e.customer_name,
                "item_name": e.item_name,
                "quantity": e.quantity,
                "unit": e.unit,
                "unit_price": e.unit_price,
                "total_amount": e.total_amount,
                "paid_amount": e.paid_amount,
                "due_amount": e.due_amount,
                "transaction_type": e.transaction_type.value,
                "status": "confirmed",
                "batch_id": e.batch_id,
            }
            for e in entries
        ]
        excel_bytes = export_ledger_to_excel(excel_data, shop_name="Kirana Store")
        df_tx = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="Transactions")
        self.assertEqual(float(df_tx["Paid Amount (INR)"].iloc[0]), 32.0)
        self.assertEqual(float(df_tx["Due Amount (INR)"].iloc[0]), 10.0)


if __name__ == "__main__":
    unittest.main()
