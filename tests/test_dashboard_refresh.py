"""Unit tests for Dashboard Refresh, Cache Invalidation, and Multi-Tenant Shop Isolation."""

import unittest
from unittest.mock import MagicMock, patch

from app import (
    clear_user_session,
    get_cached_dashboard_records,
    invalidate_dashboard_cache,
    render_dashboard,
)
from core.db import fetch_confirmed_ledger_entries
from core.i18n import t


class MockSessionState(dict):
    """Dict subclass that allows attribute access like Streamlit's SessionStateProxy."""

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class TestDashboardRefreshAndCaching(unittest.TestCase):
    """Verifies that the dashboard cache can be invalidated, refreshed, and recalculated."""

    def setUp(self) -> None:
        """Reset mock session state before each test."""
        self.mock_session_state = MockSessionState()

    def test_invalidate_dashboard_cache(self) -> None:
        """Verifies that invalidate_dashboard_cache sets records to None and refresh_needed to True."""
        with patch("app.st") as mock_st:
            mock_st.session_state = MockSessionState({
                "dashboard_records": [{"id": "entry-1"}],
                "dashboard_refresh_needed": False,
            })
            invalidate_dashboard_cache()
            self.assertIsNone(mock_st.session_state["dashboard_records"])
            self.assertTrue(mock_st.session_state["dashboard_refresh_needed"])

    def test_get_cached_dashboard_records_matching_shop(self) -> None:
        """Returns cached records when shop matches and refresh is not needed."""
        cached_data = [{"id": "entry-1", "total_amount": 100.0}]
        with patch("app.st") as mock_st:
            mock_st.session_state = MockSessionState({
                "dashboard_shop_id": "shop-123",
                "dashboard_records": cached_data,
                "dashboard_refresh_needed": False,
            })
            result = get_cached_dashboard_records("shop-123")
            self.assertEqual(result, cached_data)

    def test_get_cached_dashboard_records_shop_switch(self) -> None:
        """Returns None if active shop is different from cached shop to prevent cross-shop data leaks."""
        with patch("app.st") as mock_st:
            mock_st.session_state = MockSessionState({
                "dashboard_shop_id": "shop-123",
                "dashboard_records": [{"id": "entry-shop-1"}],
                "dashboard_refresh_needed": False,
            })
            result = get_cached_dashboard_records("shop-456")
            self.assertIsNone(result)

    def test_get_cached_dashboard_records_when_refresh_needed(self) -> None:
        """Returns None if dashboard_refresh_needed is True even if shop matches."""
        with patch("app.st") as mock_st:
            mock_st.session_state = MockSessionState({
                "dashboard_shop_id": "shop-123",
                "dashboard_records": [{"id": "stale-entry"}],
                "dashboard_refresh_needed": True,
            })
            result = get_cached_dashboard_records("shop-123")
            self.assertIsNone(result)

    def test_clear_user_session_clears_dashboard_state(self) -> None:
        """Ensures clear_user_session wipes all cached dashboard records and metadata."""
        with patch("app.st") as mock_st, patch("app.sign_out"):
            mock_st.session_state = MockSessionState({
                "auth_user": {"id": "user-1"},
                "dashboard_records": [{"id": "e1"}],
                "dashboard_shop_id": "shop-1",
                "dashboard_last_refreshed": "10:00:00 AM",
                "dashboard_refresh_needed": True,
                "last_save_status": {"count": 3},
            })
            clear_user_session()
            self.assertIsNone(mock_st.session_state.get("dashboard_records"))
            self.assertIsNone(mock_st.session_state.get("dashboard_shop_id"))
            self.assertIsNone(mock_st.session_state.get("dashboard_last_refreshed"))
            self.assertFalse(mock_st.session_state.get("dashboard_refresh_needed"))
            self.assertIsNone(mock_st.session_state.get("last_save_status"))

    @patch("app.fetch_confirmed_ledger_entries")
    @patch("app.compute_dashboard_metrics")
    @patch("app.export_ledger_to_excel")
    @patch("app.st")
    def test_save_transaction_invalidates_cache_and_renders_fresh_metrics(
        self,
        mock_st: MagicMock,
        mock_export: MagicMock,
        mock_compute: MagicMock,
        mock_fetch: MagicMock,
    ) -> None:
        """Verifies end-to-end: after saving records, cache is invalidated and dashboard renders newly saved data."""
        mock_st.columns.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.button.return_value = False

        # Step 1: Initial state has old records
        old_record = [{"id": "old-1", "total_amount": 50.0}]
        mock_st.session_state = MockSessionState({
            "auth_token": "token-1",
            "dashboard_shop_id": "shop-1",
            "dashboard_records": old_record,
            "dashboard_refresh_needed": False,
        })

        # Step 2: Invalidate cache (as happens upon successful save in tab_upload)
        invalidate_dashboard_cache()
        self.assertTrue(mock_st.session_state["dashboard_refresh_needed"])
        self.assertIsNone(mock_st.session_state["dashboard_records"])

        # Step 3: Supabase returns both old and newly saved records
        fresh_records = [
            {"id": "old-1", "total_amount": 50.0},
            {"id": "new-2", "total_amount": 150.0},
        ]
        mock_fetch.return_value = fresh_records
        mock_compute.return_value = {
            "summary": {"total_sales": 200.0, "total_collected": 200.0, "total_due": 0.0, "transaction_count": 2},
            "top_products": {"by_revenue": []},
            "customer_balances": [],
            "insights": [],
        }

        # Step 4: Render dashboard
        render_dashboard("en", active_shop_id="shop-1", active_shop_name="Ton Up Store")

        # Step 5: Verify that fresh records were fetched from Supabase
        mock_fetch.assert_called_once_with(shop_id="shop-1", access_token="token-1")
        mock_compute.assert_called_once_with(fresh_records, lang="en")
        self.assertEqual(mock_st.session_state["dashboard_records"], fresh_records)


class TestDashboardRenderAndRecalculation(unittest.TestCase):
    """Verifies that render_dashboard queries Supabase and recalculates metrics on refresh."""

    @patch("app.export_ledger_to_excel")
    @patch("app.compute_dashboard_metrics")
    @patch("app.fetch_confirmed_ledger_entries")
    @patch("app.st")
    def test_refresh_button_fetches_and_recalculates(
        self,
        mock_st: MagicMock,
        mock_fetch: MagicMock,
        mock_compute: MagicMock,
        mock_export: MagicMock,
    ) -> None:
        """Clicking Refresh Dashboard button queries Supabase and recomputes analytics."""
        # Setup mock columns: dynamic unpack based on requested columns count
        mock_st.columns.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.button.return_value = True  # Simulates user clicking Refresh Dashboard button

        # Initially no cached data, Supabase returns 2 confirmed records
        mock_fetch.return_value = [
            {
                "id": "entry-1",
                "shop_id": "shop-1",
                "customer_name": "Ramesh",
                "item_name": "Sugar",
                "total_amount": 100.0,
                "paid_amount": 80.0,
                "due_amount": 20.0,
                "status": "confirmed",
                "entry_date": "2026-03-01",
            },
            {
                "id": "entry-2",
                "shop_id": "shop-1",
                "customer_name": "Suresh",
                "item_name": "Toor Dal",
                "total_amount": 250.0,
                "paid_amount": 250.0,
                "due_amount": 0.0,
                "status": "confirmed",
                "entry_date": "2026-03-02",
            },
        ]

        mock_compute.return_value = {
            "summary": {
                "total_sales": 350.0,
                "total_collected": 330.0,
                "total_due": 20.0,
                "transaction_count": 2,
            },
            "top_products": {"by_revenue": [], "by_quantity": []},
            "customer_balances": [
                {
                    "customer_name": "Ramesh",
                    "customer_phone": None,
                    "total_billed": 100.0,
                    "total_paid": 80.0,
                    "current_due": 20.0,
                    "last_transaction_date": "2026-03-01",
                }
            ],
            "insights": [],
        }

        mock_export.return_value = b"mock-excel-bytes"
        mock_st.session_state = MockSessionState({
            "auth_token": "valid-jwt-token",
            "dashboard_records": None,
        })

        render_dashboard("en", active_shop_id="shop-1", active_shop_name="Ton Up Store")

        # 1. Verify Supabase query was called with active_shop_id and user's auth token
        mock_fetch.assert_called_once_with(shop_id="shop-1", access_token="valid-jwt-token")

        # 2. Verify compute_dashboard_metrics was called with the fresh records
        mock_compute.assert_called_once_with(mock_fetch.return_value, lang="en")

        # 3. Verify metrics were saved in session state
        self.assertEqual(mock_st.session_state["dashboard_records"], mock_fetch.return_value)
        self.assertEqual(mock_st.session_state["dashboard_shop_id"], "shop-1")
        self.assertFalse(mock_st.session_state["dashboard_refresh_needed"])
        self.assertIn("dashboard_last_refreshed", mock_st.session_state)

        # 4. Verify Excel exporter was called with fresh records
        mock_export.assert_called_once_with(
            mock_fetch.return_value,
            shop_name="Ton Up Store",
            lang="en",
        )

        # 5. Verify refresh success feedback was displayed
        mock_st.success.assert_called()


class TestMultiTenantShopIsolation(unittest.TestCase):
    """Verifies that shopkeeper A cannot see or fetch data belonging to shopkeeper B."""

    def test_fetch_confirmed_ledger_entries_scopes_strictly_to_shop_id(self) -> None:
        """Verifies database query filters strictly by shop_id and confirmed status."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_eq_shop = MagicMock()
        mock_eq_status = MagicMock()
        mock_order = MagicMock()

        mock_order.execute.return_value = MagicMock(data=[{"id": "entry-shop-A", "shop_id": "shop-A"}])
        mock_eq_status.order.return_value = mock_order
        mock_eq_shop.eq.return_value = mock_eq_status
        mock_select.eq.return_value = mock_eq_shop
        mock_table.select.return_value = mock_select
        mock_client.table.return_value = mock_table

        # Fetch for shop-A
        records_a = fetch_confirmed_ledger_entries(shop_id="shop-A", client=mock_client)
        mock_client.table.assert_called_with("ledger_entries")
        mock_select.eq.assert_called_with("shop_id", "shop-A")
        mock_eq_shop.eq.assert_called_with("status", "confirmed")
        self.assertEqual(len(records_a), 1)

    @patch("app.compute_dashboard_metrics")
    @patch("app.fetch_confirmed_ledger_entries")
    @patch("app.st")
    def test_dashboard_switches_shop_cleanly_without_data_leakage(
        self,
        mock_st: MagicMock,
        mock_fetch: MagicMock,
        mock_compute: MagicMock,
    ) -> None:
        """Switching active_shop_id immediately discards previous shop's cached data."""
        mock_st.columns.side_effect = lambda n: [MagicMock() for _ in range(n if isinstance(n, int) else len(n))]
        mock_st.button.return_value = False  # User did not click refresh button

        # Session state has cached data for shop-A
        mock_st.session_state = MockSessionState({
            "auth_token": "token-shop-B",
            "dashboard_shop_id": "shop-A",
            "dashboard_records": [{"id": "shop-A-record", "total_amount": 500.0}],
            "dashboard_refresh_needed": False,
        })

        # Supabase returns Shop B's records
        mock_fetch.return_value = [{"id": "shop-B-record", "total_amount": 120.0}]
        mock_compute.return_value = {
            "summary": {"total_sales": 120.0, "total_collected": 120.0, "total_due": 0.0, "transaction_count": 1},
            "top_products": {"by_revenue": []},
            "customer_balances": [],
            "insights": [],
        }

        # Render for shop-B
        render_dashboard("en", active_shop_id="shop-B", active_shop_name="Shop B")

        # Must have queried Supabase for shop-B, NOT used shop-A's cached records
        mock_fetch.assert_called_once_with(shop_id="shop-B", access_token="token-shop-B")
        self.assertEqual(mock_st.session_state["dashboard_shop_id"], "shop-B")
        self.assertEqual(mock_st.session_state["dashboard_records"], mock_fetch.return_value)


class TestDashboardTranslations(unittest.TestCase):
    """Verifies that all new dashboard refresh and hint keys exist in all 5 languages."""

    def test_refresh_keys_present_in_all_languages(self) -> None:
        """Checks refresh_btn, refresh_success, last_refreshed, saved_view_hint in all 5 languages."""
        keys = [
            "dashboard.refresh_btn",
            "dashboard.refresh_success",
            "dashboard.last_refreshed",
            "dashboard.saved_view_hint",
        ]
        for lang in ["en", "kn", "hi", "te", "ta"]:
            for key in keys:
                res = t(key, lang=lang, time="10:00 AM")
                self.assertTrue(len(res) > 0, f"Key '{key}' is empty in {lang}")
                self.assertNotIn(key, res, f"Key '{key}' not found in {lang}")


if __name__ == "__main__":
    unittest.main()
