"""Unit tests for Administrator Portal: Multi-Shop Management, Shopkeeper Inspection, Editing & Safe Deletion."""

import unittest
from unittest.mock import MagicMock, patch

from core.auth import is_admin_user
from core.db import (
    count_shop_ledger_entries,
    delete_shop,
    update_shop,
)
from core.i18n import t


class TestAdminShopOperations(unittest.TestCase):
    """Verifies backend database logic for updating, counting, and safely deleting shops."""

    def test_update_shop_validation(self) -> None:
        """Fails validation if shop_id or name is missing."""
        ok, err = update_shop("", {"name": "Test Shop"})
        self.assertFalse(ok)
        self.assertIn("Shop ID is required", err or "")

        ok, err = update_shop("shop-123", {"name": ""})
        self.assertFalse(ok)
        self.assertIn("Shop name is required", err or "")

    def test_update_shop_no_client(self) -> None:
        """Returns error when Supabase client is not connected."""
        with patch("core.db._resolve_client", return_value=None):
            ok, err = update_shop("shop-123", {"name": "Test Shop"})
            self.assertFalse(ok)
            self.assertIn("not connected", err or "")

    def test_update_shop_success(self) -> None:
        """Successfully updates shop fields in Supabase."""
        mock_table = MagicMock()
        mock_response = MagicMock(
            data=[{"id": "shop-123", "name": "Updated Kirana", "owner_name": "Ramesh", "default_language": "kn"}]
        )
        mock_table.update.return_value.eq.return_value.execute.return_value = mock_response

        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        payload = {
            "name": "Updated Kirana",
            "owner_name": "Ramesh",
            "phone": "9876543210",
            "address": "Gandhi Bazaar, Bengaluru",
            "default_language": "kn",
        }
        ok, err = update_shop("shop-123", payload, client=mock_client)
        self.assertTrue(ok)
        self.assertIsNone(err)

        mock_table.update.assert_called_once_with({
            "name": "Updated Kirana",
            "owner_name": "Ramesh",
            "phone": "9876543210",
            "address": "Gandhi Bazaar, Bengaluru",
            "default_language": "kn",
        })

    def test_update_shop_rls_error_42501(self) -> None:
        """Simulates RLS 42501 error on shop update and verifies actionable diagnostic."""
        mock_table = MagicMock()
        mock_table.update.return_value.eq.return_value.execute.side_effect = Exception(
            'new row violates row-level security policy for table "shops" (code: 42501)'
        )
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        ok, err = update_shop("shop-123", {"name": "Renamed"}, client=mock_client)
        self.assertFalse(ok)
        self.assertIn("RLS_ERROR_42501", err or "")
        self.assertIn("Only authorized administrators or the shop owner", err or "")

    def test_count_shop_ledger_entries(self) -> None:
        """Counts ledger entries for a shop."""
        mock_table = MagicMock()
        mock_table.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "entry-1"}, {"id": "entry-2"}, {"id": "entry-3"}]
        )
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        count = count_shop_ledger_entries("shop-123", client=mock_client)
        self.assertEqual(count, 3)

        # Blank shop_id returns 0
        self.assertEqual(count_shop_ledger_entries(""), 0)

    def test_delete_shop_blocked_by_ledger_history(self) -> None:
        """Shop deletion is blocked when historical ledger entries exist and allow_cascade is False."""
        mock_client = MagicMock()
        # Mock counting to return 5 entries
        with patch("core.db.count_shop_ledger_entries", return_value=5):
            ok, err, count = delete_shop("shop-123", client=mock_client, allow_cascade=False)
            self.assertFalse(ok)
            self.assertEqual(count, 5)
            self.assertIn("Cannot delete shop because it contains 5 transaction records", err or "")
            self.assertIn("blocked to prevent accidental loss of historical financial data", err or "")

            # Verify client.table("shops").delete() was NEVER called
            mock_client.table.assert_not_called()

    def test_delete_shop_cascade_confirmed(self) -> None:
        """Shop deletion proceeds when allow_cascade is True, deleting shop and cascading records."""
        mock_table = MagicMock()
        mock_table.delete.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "shop-123", "name": "Old Shop"}]
        )
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        with patch("core.db.count_shop_ledger_entries", return_value=5):
            ok, err, count = delete_shop("shop-123", client=mock_client, allow_cascade=True)
            self.assertTrue(ok)
            self.assertIsNone(err)
            self.assertEqual(count, 5)
            mock_table.delete.assert_called_once()

    def test_delete_shop_zero_entries_safe_delete(self) -> None:
        """Shop with 0 entries can be safely deleted without requiring cascade override."""
        mock_table = MagicMock()
        mock_table.delete.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "shop-empty", "name": "Empty Shop"}]
        )
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        with patch("core.db.count_shop_ledger_entries", return_value=0):
            ok, err, count = delete_shop("shop-empty", client=mock_client, allow_cascade=False)
            self.assertTrue(ok)
            self.assertIsNone(err)
            self.assertEqual(count, 0)
            mock_table.delete.assert_called_once()

    def test_delete_shop_rls_error_42501(self) -> None:
        """Simulates RLS 42501 error when non-admin attempts shop deletion."""
        mock_table = MagicMock()
        mock_table.delete.return_value.eq.return_value.execute.side_effect = Exception(
            'permission denied for table shops (code: 42501)'
        )
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        with patch("core.db.count_shop_ledger_entries", return_value=0):
            ok, err, count = delete_shop("shop-123", client=mock_client, allow_cascade=False)
            self.assertFalse(ok)
            self.assertIn("RLS_ERROR_42501", err or "")
            self.assertIn("Only authorized administrators can delete shops", err or "")

    def test_delete_shop_preserves_auth_users(self) -> None:
        """Verifies that delete_shop operates exclusively on the public.shops table and never touches auth.users."""
        mock_table = MagicMock()
        mock_table.delete.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "shop-123"}]
        )
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table

        with patch("core.db.count_shop_ledger_entries", return_value=0):
            ok, err, count = delete_shop("shop-123", client=mock_client, allow_cascade=False)

        self.assertTrue(ok)
        # Confirm only "shops" table was queried, not "auth.users"
        mock_client.table.assert_called_with("shops")
        self.assertEqual(mock_client.auth.admin.delete_user.call_count, 0)


class TestAdminRoutingAndSecurity(unittest.TestCase):
    """Verifies that administrator role routes to Admin Portal and blocks shopkeeper dashboard, and vice-versa."""

    def test_admin_vs_shopkeeper_routing_flags(self) -> None:
        """Verifies should_show_admin_portal and should_show_shopkeeper_dashboard behavior."""
        from app import should_show_admin_panel, should_show_admin_portal, should_show_shopkeeper_dashboard

        admin_user = {
            "id": "admin-1",
            "email": "admin@tonup.in",
            "app_metadata": {"role": "admin"},
        }
        shopkeeper_user = {
            "id": "sk-1",
            "email": "shopkeeper@kirana.in",
            "app_metadata": {},
        }
        guest_user = None

        # Admin User: sees Admin Portal, does NOT see Shopkeeper Dashboard
        self.assertTrue(should_show_admin_panel(admin_user))
        self.assertTrue(should_show_admin_portal(admin_user))
        self.assertFalse(should_show_shopkeeper_dashboard(admin_user))

        # Shopkeeper User: sees Shopkeeper Dashboard, does NOT see Admin Portal
        self.assertFalse(should_show_admin_panel(shopkeeper_user))
        self.assertFalse(should_show_admin_portal(shopkeeper_user))
        self.assertTrue(should_show_shopkeeper_dashboard(shopkeeper_user))

        # Guest User: sees neither
        self.assertFalse(should_show_admin_portal(guest_user))
        self.assertFalse(should_show_shopkeeper_dashboard(guest_user))


class TestAdminPortalRendering(unittest.TestCase):
    """Verifies render_admin_portal renders metrics, profile, and management forms with mocked streamlit."""

    @patch("app.st")
    @patch("app.count_shop_ledger_entries", return_value=12)
    @patch("app.resolve_owner_shop")
    def test_render_admin_portal_mocked(
        self,
        mock_resolve_shop: MagicMock,
        mock_count_entries: MagicMock,
        mock_st: MagicMock,
    ) -> None:
        """Executes render_admin_portal with sample data and verifies single-shop metrics and profile are rendered."""
        from app import render_admin_portal

        mock_resolve_shop.return_value = (
            "shop-1",
            "Bazaar Store",
            {
                "id": "shop-1",
                "name": "Bazaar Store",
                "owner_id": "sk-uuid-1",
                "owner_name": "Suresh",
                "phone": "9876543210",
                "address": "Main Street",
                "default_language": "kn",
                "created_at": "2026-03-01T10:00:00",
            },
        )

        # Setup st column and tab mocks
        mock_st.columns.return_value = [MagicMock(), MagicMock(), MagicMock(), MagicMock()]
        mock_tab_edit = MagicMock()
        mock_tab_delete = MagicMock()
        mock_st.tabs.return_value = [mock_tab_edit, mock_tab_delete]
        mock_st.session_state = {"auth_token": "mock-admin-token"}

        admin_user = {
            "id": "admin-1",
            "email": "admin@tonup.in",
            "app_metadata": {"role": "admin"},
        }

        # Should execute cleanly without raising any exceptions
        render_admin_portal("en", admin_user)

        # Verify metrics were rendered for Store Status, Shop Name, Owner, Confirmed Transactions
        metric_calls = mock_st.metric.call_args_list
        self.assertGreaterEqual(len(metric_calls), 4)

        # Verify single store profile dataframe was rendered (without multi-shopkeeper table)
        self.assertGreaterEqual(mock_st.dataframe.call_count, 1)

    @patch("app.st")
    @patch("app.resolve_owner_shop")
    def test_render_admin_portal_initial_setup_when_no_shop(
        self,
        mock_resolve_shop: MagicMock,
        mock_st: MagicMock,
    ) -> None:
        """When owner has no shop registered yet, renders clean one-time initial setup form."""
        from app import render_admin_portal

        mock_resolve_shop.return_value = (None, "Kirana Store", None)
        mock_st.session_state = {"auth_token": "mock-token"}

        owner_user = {
            "id": "owner-new",
            "email": "newowner@kirana.in",
            "app_metadata": {},
        }

        render_admin_portal("en", owner_user)

        # Verified that initial setup form was rendered
        mock_st.form.assert_called_with("owner_initial_setup_form")
        # No metrics or delete tabs should be rendered
        self.assertEqual(mock_st.metric.call_count, 0)
        self.assertEqual(mock_st.dataframe.call_count, 0)


class TestAdminI18n(unittest.TestCase):
    """Verifies that all admin portal translation strings exist in all 5 supported languages."""

    def test_admin_portal_translations_in_all_languages(self) -> None:
        """Checks admin translation keys across en, kn, hi, te, ta."""
        expected_keys = [
            "admin.portal_title",
            "admin.total_shops",
            "admin.total_shopkeepers",
            "admin.assigned_shops",
            "admin.unassigned_shops",
            "admin.edit_shop_title",
            "admin.edit_shop_btn",
            "admin.edit_shop_success",
            "admin.delete_shop_title",
            "admin.delete_shop_btn",
            "admin.delete_shop_confirm",
            "admin.delete_shop_success",
            "admin.delete_blocked_history",
        ]
        for lang in ["en", "kn", "hi", "te", "ta"]:
            for key in expected_keys:
                translated = t(key, lang=lang, name="TestStore", count=3)
                self.assertTrue(
                    len(translated) > 0,
                    f"Translation key '{key}' in language '{lang}' should not be empty.",
                )
                self.assertNotIn(
                    key,
                    translated,
                    f"Translation key '{key}' was not found in language '{lang}' (returned key as fallback).",
                )


class TestSingleShopOwnerAccess(unittest.TestCase):
    """Verifies that the single shop owner can access only their own shop, with strict tenant isolation."""

    def test_resolve_owner_shop_matches_owner_id(self) -> None:
        """Owner A gets Owner A's shop, never Owner B's shop."""
        from core.db import resolve_owner_shop

        mock_client = MagicMock()
        mock_response_a = MagicMock(data=[{"id": "shop-a", "name": "Store A", "owner_id": "user-a"}])
        mock_client.table.return_value.select.return_value.order.return_value.eq.return_value.execute.return_value = mock_response_a

        sid, sname, sdict = resolve_owner_shop(
            owner_id="user-a",
            client=mock_client,
            is_admin=False,
        )
        self.assertEqual(sid, "shop-a")
        self.assertEqual(sname, "Store A")
        self.assertEqual(sdict["owner_id"], "user-a")

        # Verify query specifically filtered by owner_id == user-a
        mock_client.table("shops").select.return_value.order.return_value.eq.assert_called_with("owner_id", "user-a")

    def test_resolve_owner_shop_does_not_create_duplicates(self) -> None:
        """Calling resolve_owner_shop multiple times for the same user only selects, never inserts."""
        from core.db import resolve_owner_shop

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.order.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "shop-1", "name": "Existing Store", "owner_id": "owner-1"}]
        )

        for _ in range(5):
            sid, sname, _ = resolve_owner_shop(owner_id="owner-1", client=mock_client)
            self.assertEqual(sid, "shop-1")

        # Verify table("shops").insert() was NEVER called
        self.assertEqual(mock_client.table("shops").insert.call_count, 0)

    def test_owner_without_shop_returns_none(self) -> None:
        """When user owns no shop, returns None without crashing or fabricating shops."""
        from core.db import resolve_owner_shop

        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.order.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )

        sid, sname, sdict = resolve_owner_shop(owner_id="new-user", client=mock_client)
        self.assertIsNone(sid)
        self.assertEqual(sname, "Kirana Store")
        self.assertIsNone(sdict)


class TestMultiShopSingleUserWorkflow(unittest.TestCase):
    """Verifies multi-shop provisioning, single-user-per-shop isolation, automatic shop resolution, and duplicate/failure handling."""

    def setUp(self) -> None:
        self.admin_user = {
            "id": "admin-super-uuid",
            "email": "admin@tonup.in",
            "app_metadata": {"role": "admin"},
        }
        self.user_a = {
            "id": "user-a-uuid",
            "email": "annapoorna@kirana.in",
            "app_metadata": {},
        }
        self.user_b = {
            "id": "user-b-uuid",
            "email": "lakshmi@kirana.in",
            "app_metadata": {},
        }

    def test_provision_multiple_shops_with_distinct_users(self) -> None:
        """Admin provisions Shop A + User A, and Shop B + User B, securely linking shops to user UUIDs."""
        from core.db import provision_shop_with_owner

        mock_admin_client = MagicMock()
        mock_db_client = MagicMock()

        # Step 1: Provision Shop A + User A
        with patch("core.auth.admin_get_user_by_email", return_value=None), \
             patch("core.auth.admin_create_user", return_value=(True, "user-a-uuid", None)), \
             patch("core.db.create_shop", return_value=(True, "shop-a-id", None)) as mock_create_shop_a:

            ok_a, sid_a, uid_a, err_a = provision_shop_with_owner(
                shop_data={"name": "Annapoorna Stores", "owner_name": "Ramesh"},
                owner_email="annapoorna@kirana.in",
                owner_password="password123",
                current_admin_user=self.admin_user,
                admin_client=mock_admin_client,
                db_client=mock_db_client,
            )
            self.assertTrue(ok_a)
            self.assertEqual(sid_a, "shop-a-id")
            self.assertEqual(uid_a, "user-a-uuid")
            self.assertIsNone(err_a)
            mock_create_shop_a.assert_called_once_with(
                shop_data={"name": "Annapoorna Stores", "owner_name": "Ramesh"},
                owner_id="user-a-uuid",
                access_token=None,
                client=mock_db_client,
            )

        # Step 2: Provision Shop B + User B
        with patch("core.auth.admin_get_user_by_email", return_value=None), \
             patch("core.auth.admin_create_user", return_value=(True, "user-b-uuid", None)), \
             patch("core.db.create_shop", return_value=(True, "shop-b-id", None)) as mock_create_shop_b:

            ok_b, sid_b, uid_b, err_b = provision_shop_with_owner(
                shop_data={"name": "Lakshmi Traders", "owner_name": "Suresh"},
                owner_email="lakshmi@kirana.in",
                owner_password="password456",
                current_admin_user=self.admin_user,
                admin_client=mock_admin_client,
                db_client=mock_db_client,
            )
            self.assertTrue(ok_b)
            self.assertEqual(sid_b, "shop-b-id")
            self.assertEqual(uid_b, "user-b-uuid")
            self.assertIsNone(err_b)
            mock_create_shop_b.assert_called_once_with(
                shop_data={"name": "Lakshmi Traders", "owner_name": "Suresh"},
                owner_id="user-b-uuid",
                access_token=None,
                client=mock_db_client,
            )

        # Ensure distinct IDs
        self.assertNotEqual(sid_a, sid_b)
        self.assertNotEqual(uid_a, uid_b)

    def test_automatic_shop_resolution_on_login(self) -> None:
        """When User A logs in, their designated Shop A is automatically selected; User B gets Shop B."""
        from core.db import resolve_owner_shop

        shop_a = {"id": "shop-a-id", "name": "Annapoorna Stores", "owner_id": "user-a-uuid"}
        shop_b = {"id": "shop-b-id", "name": "Lakshmi Traders", "owner_id": "user-b-uuid"}

        # Simulate User A logging in: fetch_available_shops returns only shop A
        with patch("core.db.fetch_available_shops", side_effect=lambda owner_id=None, **k: [shop_a] if owner_id == "user-a-uuid" else [shop_b]):
            sid_a, sname_a, sdict_a = resolve_owner_shop(auth_user=self.user_a, is_admin=False)
            self.assertEqual(sid_a, "shop-a-id")
            self.assertEqual(sname_a, "Annapoorna Stores")
            self.assertEqual(sdict_a["owner_id"], "user-a-uuid")

            # Simulate User B logging in: gets only shop B
            sid_b, sname_b, sdict_b = resolve_owner_shop(auth_user=self.user_b, is_admin=False)
            self.assertEqual(sid_b, "shop-b-id")
            self.assertEqual(sname_b, "Lakshmi Traders")
            self.assertEqual(sdict_b["owner_id"], "user-b-uuid")

    def test_duplicate_prevention_on_retry(self) -> None:
        """Calling provision_shop_with_owner with identical email and shop name acts idempotently without duplicates."""
        from core.db import provision_shop_with_owner

        existing_user = {"id": "user-a-uuid", "email": "annapoorna@kirana.in"}
        existing_shop = {"id": "shop-a-id", "name": "Annapoorna Stores", "owner_id": "user-a-uuid"}

        mock_db_client = MagicMock()
        mock_db_client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[existing_shop]
        )

        with patch("core.auth.admin_get_user_by_email", return_value=existing_user), \
             patch("core.auth.admin_create_user") as mock_admin_create, \
             patch("core.db.create_shop") as mock_create_shop:

            ok, sid, uid, err = provision_shop_with_owner(
                shop_data={"name": "Annapoorna Stores"},
                owner_email="annapoorna@kirana.in",
                owner_password="password123",
                current_admin_user=self.admin_user,
                db_client=mock_db_client,
            )
            self.assertTrue(ok)
            self.assertEqual(sid, "shop-a-id")
            self.assertEqual(uid, "user-a-uuid")
            self.assertIsNone(err)

            # Assert neither duplicate user nor duplicate shop was inserted
            mock_admin_create.assert_not_called()
            mock_create_shop.assert_not_called()

    def test_single_user_per_shop_conflict(self) -> None:
        """Attempting to assign a second different shop to the same owner account is strictly blocked."""
        from core.db import provision_shop_with_owner

        existing_user = {"id": "user-a-uuid", "email": "annapoorna@kirana.in"}
        existing_shop = {"id": "shop-a-id", "name": "Annapoorna Stores", "owner_id": "user-a-uuid"}

        mock_db_client = MagicMock()
        mock_db_client.table.return_value.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[existing_shop]
        )

        with patch("core.auth.admin_get_user_by_email", return_value=existing_user), \
             patch("core.db.create_shop") as mock_create_shop:

            ok, sid, uid, err = provision_shop_with_owner(
                shop_data={"name": "Different Shop"},
                owner_email="annapoorna@kirana.in",
                owner_password="password123",
                current_admin_user=self.admin_user,
                db_client=mock_db_client,
            )
            self.assertFalse(ok)
            self.assertIsNone(sid)
            self.assertEqual(uid, "user-a-uuid")
            self.assertIn("already owns registered shop 'Annapoorna Stores'", err)
            self.assertIn("Each account can only access one shop", err)
            mock_create_shop.assert_not_called()

    def test_partial_failure_cleanup_rollback(self) -> None:
        """When user account is created but shop creation fails, orphaned user is immediately deleted."""
        from core.db import provision_shop_with_owner

        mock_admin_client = MagicMock()
        mock_db_client = MagicMock()

        with patch("core.auth.admin_get_user_by_email", return_value=None), \
             patch("core.auth.admin_create_user", return_value=(True, "new-orphaned-uuid", None)), \
             patch("core.db.create_shop", return_value=(False, None, "Database table constraint violation")):

            ok, sid, uid, err = provision_shop_with_owner(
                shop_data={"name": "Failing Shop"},
                owner_email="fail@kirana.in",
                owner_password="password123",
                current_admin_user=self.admin_user,
                admin_client=mock_admin_client,
                db_client=mock_db_client,
            )
            self.assertFalse(ok)
            self.assertIsNone(sid)
            self.assertIn("shop registration failed", err)
            # Verify orphaned auth user was purged
            mock_admin_client.auth.admin.delete_user.assert_called_once_with("new-orphaned-uuid")

    def test_cross_shop_data_isolation(self) -> None:
        """Verifies that queries to fetch confirmed ledger entries strictly scope to the shop and cannot bleed across tenants."""
        from core.db import fetch_confirmed_ledger_entries

        mock_client = MagicMock()
        mock_response_a = MagicMock(data=[
            {"id": "entry-a-1", "shop_id": "shop-a-id", "item": "Toor Dal", "status": "CONFIRMED"}
        ])
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.order.return_value.execute.return_value = mock_response_a

        # Fetch for Shop A
        entries_a = fetch_confirmed_ledger_entries(shop_id="shop-a-id", client=mock_client)
        self.assertEqual(len(entries_a), 1)
        self.assertEqual(entries_a[0]["shop_id"], "shop-a-id")

        # Verify query explicitly filtered by shop_id == "shop-a-id"
        mock_client.table("ledger_entries").select.return_value.eq.assert_called_with("shop_id", "shop-a-id")


if __name__ == "__main__":
    unittest.main()
