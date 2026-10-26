"""Unit tests for Supabase shop management, confirmed entry persistence, and idempotency.

Uses pure-Python mocks to simulate Supabase client responses, network errors,
and constraint violations without connecting to a live database or modifying state.
"""

import unittest
from unittest.mock import MagicMock

from core.db import (
    check_batch_exists,
    create_shop,
    fetch_available_shops,
    save_confirmed_entries,
    verify_shop_exists,
)
from core.models import EntryStatus, LedgerEntry, TransactionType


class TestDatabasePersistence(unittest.TestCase):
    """Verifies safe database persistence, shop creation, and duplicate protection."""

    def setUp(self) -> None:
        self.mock_client = MagicMock()

    def test_fetch_available_shops(self) -> None:
        """Verifies querying and formatting available shops."""
        mock_response = MagicMock()
        mock_response.data = [
            {"id": "shop-1", "name": "Sri Manjunatha Store", "owner_name": "Ramesh"},
            {"id": "shop-2", "name": "Ganesh Provisions", "owner_name": "Suresh"},
        ]
        self.mock_client.table.return_value.select.return_value.order.return_value.execute.return_value = (
            mock_response
        )

        shops = fetch_available_shops(client=self.mock_client)
        self.assertEqual(len(shops), 2)
        self.assertEqual(shops[0]["name"], "Sri Manjunatha Store")

    def test_create_shop_valid_data(self) -> None:
        """Valid shop creation generates proper payload and returns created ID."""
        mock_response = MagicMock()
        mock_response.data = [{"id": "shop-uuid-123", "name": "Mysuru Kirana"}]
        self.mock_client.table.return_value.insert.return_value.execute.return_value = (
            mock_response
        )

        success, shop_id, err = create_shop(
            {
                "name": "Mysuru Kirana",
                "owner_name": "Kumar",
                "phone": "9845012345",
                "address": "Kuvempunagar",
                "default_language": "kn",
            },
            client=self.mock_client,
        )

        self.assertTrue(success)
        self.assertEqual(shop_id, "shop-uuid-123")
        self.assertIsNone(err)

    def test_create_shop_missing_name_fails(self) -> None:
        """Creating a shop without a name must fail immediately."""
        success, shop_id, err = create_shop({"name": "   "}, client=self.mock_client)
        self.assertFalse(success)
        self.assertIsNone(shop_id)
        self.assertIn("required", err or "")

    def test_save_confirmed_entries_payload_structure(self) -> None:
        """Confirmed entries preserve None as SQL NULL and enforce idempotency."""
        # 1. Mock verify_shop_exists
        mock_shop_resp = MagicMock()
        mock_shop_resp.data = [{"id": "shop-1"}]
        self.mock_client.table("shops").select.return_value.eq.return_value.execute.return_value = (
            mock_shop_resp
        )

        # 2. Mock batch existence check (no existing batch)
        mock_batch_resp = MagicMock()
        mock_batch_resp.data = []
        self.mock_client.table("ledger_entries").select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_batch_resp
        )

        # 3. Mock insert response
        mock_insert_resp = MagicMock()
        mock_insert_resp.data = [{"id": "entry-1"}, {"id": "entry-2"}]
        self.mock_client.table("ledger_entries").insert.return_value.execute.return_value = (
            mock_insert_resp
        )

        entry1 = LedgerEntry(
            item_name="Toor Dal",
            total_amount=160.0,
            customer_name="   ",  # Blank -> must become None
            paid_amount=None,  # Missing -> must stay None
            due_amount=None,  # Missing -> must stay None
            status=EntryStatus.CONFIRMED,
        )
        entry2 = LedgerEntry(
            item_name="Sugar",
            total_amount=44.0,
            customer_name="Ravi Kumar",
            paid_amount=44.0,
            due_amount=0.0,
            status=EntryStatus.CONFIRMED,
        )

        success, msg, count = save_confirmed_entries(
            entries=[entry1, entry2],
            shop_id="shop-1",
            batch_id="batch-sha256-test",
            client=self.mock_client,
        )

        self.assertTrue(success)
        self.assertEqual(count, 2)

        # Inspect inserted payload
        insert_call = self.mock_client.table("ledger_entries").insert.call_args[0][0]
        self.assertEqual(len(insert_call), 2)
        # Entry 1: customer_name and paid/due MUST be None (SQL NULL), never 0.0 or ""
        self.assertIsNone(insert_call[0]["customer_name"])
        self.assertIsNone(insert_call[0]["paid_amount"])
        self.assertIsNone(insert_call[0]["due_amount"])
        self.assertEqual(insert_call[0]["batch_id"], "batch-sha256-test")
        self.assertEqual(insert_call[0]["row_index"], 1)

        # Entry 2: customer_name is present, paid is 44.0
        self.assertEqual(insert_call[1]["customer_name"], "Ravi Kumar")
        self.assertEqual(insert_call[1]["paid_amount"], 44.0)
        self.assertEqual(insert_call[1]["due_amount"], 0.0)

    def test_save_unconfirmed_entries_rejected(self) -> None:
        """Pending unverified entries must never be saved to Supabase."""
        pending_entry = LedgerEntry(
            item_name="Rice",
            total_amount=60.0,
            status=EntryStatus.PENDING,  # Still pending verification!
        )

        success, msg, count = save_confirmed_entries(
            entries=[pending_entry],
            shop_id="shop-1",
            batch_id="batch-123",
            client=self.mock_client,
        )

        self.assertFalse(success)
        self.assertEqual(count, 0)
        self.assertIn("No confirmed", msg)

    def test_idempotency_prevents_duplicate_save(self) -> None:
        """Existing batch_id triggers duplicate alert and prevents insert."""
        # Mock shop exists
        mock_shop_resp = MagicMock()
        mock_shop_resp.data = [{"id": "shop-1"}]
        self.mock_client.table("shops").select.return_value.eq.return_value.execute.return_value = (
            mock_shop_resp
        )

        # Mock existing batch with 3 records
        mock_batch_resp = MagicMock()
        mock_batch_resp.data = [{"id": "e1"}, {"id": "e2"}, {"id": "e3"}]
        self.mock_client.table("ledger_entries").select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_batch_resp
        )

        entry = LedgerEntry(
            item_name="Rice",
            total_amount=60.0,
            status=EntryStatus.CONFIRMED,
        )

        success, msg, count = save_confirmed_entries(
            entries=[entry],
            shop_id="shop-1",
            batch_id="batch-already-saved",
            allow_duplicate=False,
            client=self.mock_client,
        )

        self.assertFalse(success)
        self.assertIn("DUPLICATE_BATCH", msg)
        self.assertEqual(count, 3)
        # Verify insert was NOT called
        self.mock_client.table("ledger_entries").insert.assert_not_called()

    def test_missing_shop_fails_safely(self) -> None:
        """Saving with a non-existent shop ID fails with a clear message."""
        mock_shop_resp = MagicMock()
        mock_shop_resp.data = []  # Shop not found
        self.mock_client.table("shops").select.return_value.eq.return_value.execute.return_value = (
            mock_shop_resp
        )

        entry = LedgerEntry(item_name="Rice", status=EntryStatus.CONFIRMED)
        success, msg, count = save_confirmed_entries(
            entries=[entry],
            shop_id="non-existent-shop",
            batch_id="batch-1",
            client=self.mock_client,
        )

        self.assertFalse(success)
        self.assertIn("not found in the database", msg)

    def test_migration_required_error_detection(self) -> None:
        """Identifies when Supabase is missing migration 001 columns."""
        # Mock shop exists
        mock_shop_resp = MagicMock()
        mock_shop_resp.data = [{"id": "shop-1"}]
        self.mock_client.table("shops").select.return_value.eq.return_value.execute.return_value = (
            mock_shop_resp
        )

        # Mock no existing batch
        mock_batch_resp = MagicMock()
        mock_batch_resp.data = []
        self.mock_client.table("ledger_entries").select.return_value.eq.return_value.eq.return_value.execute.return_value = (
            mock_batch_resp
        )

        # Mock Postgres error indicating column does not exist
        self.mock_client.table("ledger_entries").insert.return_value.execute.side_effect = (
            Exception("column 'paid_amount' does not exist in relation 'ledger_entries'")
        )

        entry = LedgerEntry(item_name="Rice", status=EntryStatus.CONFIRMED)
        success, msg, count = save_confirmed_entries(
            entries=[entry],
            shop_id="shop-1",
            batch_id="batch-1",
            client=self.mock_client,
        )

        self.assertFalse(success)
        self.assertIn("MIGRATION_REQUIRED", msg)
        self.assertIn("001_add_paid_due_and_idempotency.sql", msg)


if __name__ == "__main__":
    unittest.main()
