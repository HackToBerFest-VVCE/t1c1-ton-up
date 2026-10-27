"""Tests for Supabase Authentication, token scoping, and RLS error handling (Milestone 4)."""

import unittest
from unittest.mock import MagicMock, patch

from core.auth import (
    admin_create_shopkeeper_account,
    admin_create_user,
    admin_get_user_by_email,
    admin_list_shopkeepers,
    admin_list_users,
    get_admin_auth_client,
    get_authenticated_client,
    get_current_user,
    is_admin_user,
    sign_in,
    sign_out,
    sign_up,
)
from core.db import (
    admin_assign_shop_by_email,
    assign_shop_owner,
    create_shop,
    fetch_available_shops,
    provision_shop_with_owner,
    save_confirmed_entries,
)
from core.models import EntryStatus, LedgerEntry


class TestAuthAndRLS(unittest.TestCase):
    """Verifies authentication logic, user scoping, and Row Level Security behavior."""

    def test_sign_up_validation(self) -> None:
        """Validates email format and password length requirements."""
        ok, res, err = sign_up("", "password123")
        self.assertFalse(ok)
        self.assertIn("email", (err or "").lower())

        ok, res, err = sign_up("notanemail", "password123")
        self.assertFalse(ok)
        self.assertIn("valid email", (err or "").lower())

        ok, res, err = sign_up("user@example.com", "123")
        self.assertFalse(ok)
        self.assertIn("at least 6 characters", (err or "").lower())

    def test_sign_up_success_mocked(self) -> None:
        """Verifies successful user sign-up with mocked Supabase auth client."""
        mock_auth = MagicMock()
        mock_response = MagicMock()
        mock_response.user = MagicMock(id="user-uuid-1234", email="shopkeeper@example.com")
        mock_auth.sign_up.return_value = mock_response

        mock_client = MagicMock(auth=mock_auth)

        ok, res, err = sign_up(
            "shopkeeper@example.com",
            "securepassword123",
            user_metadata={"name": "Ramesh"},
            client=mock_client,
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        mock_auth.sign_up.assert_called_once()
        call_arg = mock_auth.sign_up.call_args[0][0]
        self.assertEqual(call_arg["email"], "shopkeeper@example.com")
        self.assertEqual(call_arg["password"], "securepassword123")
        self.assertEqual(call_arg["options"]["data"]["name"], "Ramesh")

    def test_sign_up_api_error_handled(self) -> None:
        """Verifies that API errors during sign-up are captured cleanly."""
        mock_auth = MagicMock()
        mock_auth.sign_up.side_effect = Exception("User already registered")
        mock_client = MagicMock(auth=mock_auth)

        ok, res, err = sign_up("existing@example.com", "password123", client=mock_client)
        self.assertFalse(ok)
        self.assertIn("already registered", err or "")

    def test_sign_in_validation(self) -> None:
        """Validates required credentials for sign in."""
        ok, res, err = sign_in("", "secret")
        self.assertFalse(ok)
        self.assertIn("valid email", (err or "").lower())

        ok, res, err = sign_in("user@example.com", "")
        self.assertFalse(ok)
        self.assertIn("password cannot be empty", (err or "").lower())

    def test_sign_in_success_mocked(self) -> None:
        """Verifies successful sign-in returning session and access token."""
        mock_auth = MagicMock()
        mock_response = MagicMock()
        mock_response.session = MagicMock(access_token="mock-jwt-token-xyz")
        mock_response.user = MagicMock(id="user-uuid-1234", email="shopkeeper@example.com")
        mock_auth.sign_in_with_password.return_value = mock_response
        mock_client = MagicMock(auth=mock_auth)

        ok, res, err = sign_in("shopkeeper@example.com", "secure123", client=mock_client)
        self.assertTrue(ok)
        self.assertIsNone(err)
        self.assertEqual(res.session.access_token, "mock-jwt-token-xyz")

    def test_sign_in_invalid_credentials_handled(self) -> None:
        """Verifies that bad credentials return clean, user-friendly error."""
        mock_auth = MagicMock()
        mock_auth.sign_in_with_password.side_effect = Exception("Invalid login credentials")
        mock_client = MagicMock(auth=mock_auth)

        ok, res, err = sign_in("shopkeeper@example.com", "wrongpassword", client=mock_client)
        self.assertFalse(ok)
        self.assertIn("Invalid email or password", err or "")

    def test_sign_out_mocked(self) -> None:
        """Verifies sign out delegates to client auth."""
        mock_auth = MagicMock()
        mock_client = MagicMock(auth=mock_auth)

        ok, err = sign_out(client=mock_client)
        self.assertTrue(ok)
        self.assertIsNone(err)
        mock_auth.sign_out.assert_called_once()

    def test_get_current_user_with_token(self) -> None:
        """Verifies get_current_user queries auth with token."""
        mock_auth = MagicMock()
        mock_user = MagicMock(id="uuid-user-1")
        mock_resp = MagicMock(user=mock_user)
        mock_auth.get_user.return_value = mock_resp
        mock_client = MagicMock(auth=mock_auth)

        user = get_current_user(access_token="valid-token", client=mock_client)
        self.assertEqual(user, mock_user)
        mock_auth.get_user.assert_called_once_with("valid-token")

    def test_create_shop_with_owner_id(self) -> None:
        """Verifies create_shop attaches owner_id to the insert payload."""
        mock_table = MagicMock()
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table
        mock_insert = MagicMock()
        mock_table.insert.return_value = mock_insert
        mock_insert.execute.return_value = MagicMock(data=[{"id": "shop-created-1"}])

        ok, sid, err = create_shop(
            {"name": "Sri Lakshmi Provision Stores"},
            owner_id="owner-uuid-456",
            client=mock_client,
        )
        self.assertTrue(ok)
        self.assertEqual(sid, "shop-created-1")
        mock_table.insert.assert_called_once()
        inserted_payload = mock_table.insert.call_args[0][0]
        self.assertEqual(inserted_payload["owner_id"], "owner-uuid-456")
        self.assertEqual(inserted_payload["name"], "Sri Lakshmi Provision Stores")

    def test_create_shop_rls_error_42501(self) -> None:
        """Simulates Supabase RLS error 42501 when creating shop and checks clear diagnostic."""
        mock_table = MagicMock()
        mock_client = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.insert.return_value.execute.side_effect = Exception(
            "{'code': '42501', 'message': 'new row violates row-level security policy for table \"shops\"'}"
        )

        ok, sid, err = create_shop({"name": "Kirana Store"}, client=mock_client)
        self.assertFalse(ok)
        self.assertIsNone(sid)
        self.assertIn("RLS_ERROR_42501", err or "")
        self.assertIn("002_add_auth_and_rls.sql", err or "")

    def test_save_confirmed_entries_rls_error_42501(self) -> None:
        """Simulates RLS policy rejection when saving entries to an unauthorized shop."""
        mock_client = MagicMock()

        # Mock verify_shop_exists -> True
        mock_shops = MagicMock()
        mock_shops.select.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "shop-1"}]
        )

        # Mock check_batch_exists -> False (0 existing)
        mock_entries_check = MagicMock()
        mock_entries_check.select.return_value.eq.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[]
        )

        # Mock insert failure with 42501
        mock_insert = MagicMock()
        mock_insert.execute.side_effect = Exception(
            "code: 42501, message: new row violates row-level security policy for table 'ledger_entries'"
        )

        def table_router(name):
            if name == "shops":
                return mock_shops
            m = MagicMock()
            m.select = mock_entries_check.select
            m.insert.return_value = mock_insert
            return m

        mock_client.table.side_effect = table_router

        entry = LedgerEntry(
            item_name="Sugar",
            shop_id="shop-1",
            total_amount=100.0,
            status=EntryStatus.CONFIRMED,
        )

        ok, msg, count = save_confirmed_entries(
            entries=[entry],
            shop_id="shop-1",
            batch_id="batch-rls-test",
            client=mock_client,
        )
        self.assertFalse(ok)
        self.assertEqual(count, 0)
        self.assertIn("RLS_ERROR_42501", msg)
        self.assertIn("002_add_auth_and_rls.sql", msg)

    def test_fetch_available_shops_filters_by_owner_id(self) -> None:
        """Verifies fetch_available_shops scopes queries by owner_id if provided."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_order = MagicMock()
        mock_eq = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.order.return_value = mock_order
        mock_order.eq.return_value = mock_eq
        mock_eq.execute.return_value = MagicMock(
            data=[{"id": "shop-1", "name": "My Shop", "owner_id": "owner-1"}]
        )

        shops = fetch_available_shops(client=mock_client, owner_id="owner-1")
        self.assertEqual(len(shops), 1)
        self.assertEqual(shops[0]["id"], "shop-1")
        mock_order.eq.assert_called_once_with("owner_id", "owner-1")

    def test_is_admin_user_security(self) -> None:
        """Verifies admin authorization does not trust client-editable user_metadata."""
        # 1. Server-managed app_metadata -> Authorized
        admin_user = MagicMock(app_metadata={"role": "admin"}, user_metadata={})
        self.assertTrue(is_admin_user(admin_user))

        # 2. Client-editable user_metadata spoof attempt -> Unauthorized!
        spoof_user = MagicMock(app_metadata={}, user_metadata={"role": "admin"})
        self.assertFalse(is_admin_user(spoof_user))

        # 3. Environment configured ADMIN_EMAIL -> Authorized
        with patch("core.auth.get_config") as mock_conf:
            mock_conf.return_value = MagicMock(admin_email="admin@tonup.in")
            email_admin = MagicMock(email="admin@tonup.in", app_metadata={})
            self.assertTrue(is_admin_user(email_admin))

            regular_user = MagicMock(email="shopkeeper@tonup.in", app_metadata={})
            self.assertFalse(is_admin_user(regular_user))

    def test_assign_shop_owner_success(self) -> None:
        """Verifies assign_shop_owner updates the shop owner_id."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_update = MagicMock()
        mock_eq = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.update.return_value = mock_update
        mock_update.eq.return_value = mock_eq
        mock_eq.execute.return_value = MagicMock(data=[{"id": "shop-123", "owner_id": "user-456"}])

        ok, err = assign_shop_owner(
            shop_id="shop-123",
            new_owner_id="user-456",
            client=mock_client,
        )
        self.assertTrue(ok)
        self.assertIsNone(err)
        mock_table.update.assert_called_once_with({"owner_id": "user-456"})
        mock_update.eq.assert_called_once_with("id", "shop-123")

    def test_assign_shop_owner_validation_and_rls(self) -> None:
        """Verifies assign_shop_owner validates inputs and handles 42501 error cleanly."""
        ok, err = assign_shop_owner("", "user-1")
        self.assertFalse(ok)
        self.assertIn("Shop ID is required", err or "")

        ok, err = assign_shop_owner("shop-1", "")
        self.assertFalse(ok)
        self.assertIn("User UUID is required", err or "")

        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.update.return_value.eq.return_value.execute.side_effect = Exception(
            "code: 42501, message: permission denied for table shops"
        )
        ok, err = assign_shop_owner("shop-1", "user-1", client=mock_client)
        self.assertFalse(ok)
        self.assertIn("RLS_ERROR_42501", err or "")

    def test_fetch_available_shops_is_admin_flag(self) -> None:
        """Verifies admin users can list all shops without owner_id filtering."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_order = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.order.return_value = mock_order
        mock_order.execute.return_value = MagicMock(
            data=[
                {"id": "shop-1", "name": "Store 1", "owner_id": "user-1"},
                {"id": "shop-2", "name": "Store 2", "owner_id": "user-2"},
            ]
        )

        shops = fetch_available_shops(client=mock_client, owner_id="user-1", is_admin=True)
        self.assertEqual(len(shops), 2)
        # Verify .eq was NOT called because is_admin is True
        self.assertFalse(hasattr(mock_order, "eq") and mock_order.eq.called)

    def test_ordinary_user_shop_creation_rejected_by_rls(self) -> None:
        """Verifies that ordinary shopkeepers attempting direct shop insertion are blocked by RLS."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_table.insert.return_value.execute.side_effect = Exception(
            "{'code': '42501', 'message': 'new row violates row-level security policy for table \"shops\"'}"
        )

        # Non-admin user calls create_shop directly
        ok, sid, err = create_shop(
            {"name": "Sneaky Self-Created Shop"},
            owner_id="ordinary-user-uuid",
            client=mock_client,
        )
        self.assertFalse(ok)
        self.assertIsNone(sid)
        self.assertIn("RLS_ERROR_42501", err or "")

    def test_admin_shop_creation_allowed(self) -> None:
        """Verifies that an authorized admin user can create a shop assigned to a shopkeeper."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_client.table.return_value = mock_table
        mock_insert = MagicMock()
        mock_table.insert.return_value = mock_insert
        mock_insert.execute.return_value = MagicMock(data=[{"id": "shop-admin-created"}])

        ok, sid, err = create_shop(
            {"name": "Authorized Provisioned Kirana"},
            owner_id="target-shopkeeper-uuid",
            client=mock_client,
        )
        self.assertTrue(ok)
        self.assertEqual(sid, "shop-admin-created")
        self.assertIsNone(err)
        inserted_payload = mock_table.insert.call_args[0][0]
        self.assertEqual(inserted_payload["owner_id"], "target-shopkeeper-uuid")

    def test_cross_shop_read_isolation(self) -> None:
        """Verifies that ordinary shopkeepers only receive their own shops from fetch_available_shops."""
        mock_client = MagicMock()
        mock_table = MagicMock()
        mock_select = MagicMock()
        mock_order = MagicMock()
        mock_eq = MagicMock()

        mock_client.table.return_value = mock_table
        mock_table.select.return_value = mock_select
        mock_select.order.return_value = mock_order
        mock_order.eq.return_value = mock_eq
        mock_eq.execute.return_value = MagicMock(
            data=[{"id": "shop-101", "name": "Ramesh Stores", "owner_id": "ramesh-uuid"}]
        )

        # Ramesh queries his shops (is_admin=False)
        shops = fetch_available_shops(client=mock_client, owner_id="ramesh-uuid", is_admin=False)
        self.assertEqual(len(shops), 1)
        self.assertEqual(shops[0]["owner_id"], "ramesh-uuid")
        mock_order.eq.assert_called_once_with("owner_id", "ramesh-uuid")


class TestAuthenticationUIAndRouting(unittest.TestCase):
    """Verifies Milestone 4 authentication UI requirements:
    1. Logged-out state hides main application and only displays clean login screen.
    2. Admin routing enables provisioning expander and access to all shops.
    3. Shopkeeper routing restricts access to owned shop only and hides admin tools.
    4. Sign-out clears all auth/session state completely.
    """

    def test_logged_out_ui_hiding(self) -> None:
        """When logged out, main application tabs and admin panels must be hidden."""
        from app import is_user_authenticated, should_show_admin_panel, should_show_main_app

        empty_state = {}
        self.assertFalse(is_user_authenticated(empty_state))
        self.assertFalse(should_show_main_app(None))
        self.assertFalse(should_show_admin_panel(None))

        none_state = {"auth_user": None}
        self.assertFalse(is_user_authenticated(none_state))
        self.assertFalse(should_show_main_app(none_state["auth_user"]))
        self.assertFalse(should_show_admin_panel(none_state["auth_user"]))

    def test_admin_routing(self) -> None:
        """Admins must see main application and admin provisioning panel."""
        from app import is_user_authenticated, should_show_admin_panel, should_show_main_app

        admin_user = {
            "id": "admin-uuid-001",
            "email": "admin@tonup.in",
            "app_metadata": {"role": "admin"},
        }
        state = {"auth_user": admin_user}

        self.assertTrue(is_user_authenticated(state))
        self.assertTrue(should_show_main_app(admin_user))
        self.assertTrue(should_show_admin_panel(admin_user))
        self.assertTrue(is_admin_user(admin_user))

    def test_shopkeeper_routing(self) -> None:
        """Shopkeepers see main application but are strictly blocked from admin panel."""
        from app import is_user_authenticated, should_show_admin_panel, should_show_main_app

        shopkeeper_user = {
            "id": "shopkeeper-uuid-002",
            "email": "ramesh@kirana.in",
            "app_metadata": {},
        }
        state = {"auth_user": shopkeeper_user}

        self.assertTrue(is_user_authenticated(state))
        self.assertTrue(should_show_main_app(shopkeeper_user))
        self.assertFalse(should_show_admin_panel(shopkeeper_user))
        self.assertFalse(is_admin_user(shopkeeper_user))

    def test_sign_out_clears_session_state(self) -> None:
        """On sign-out, session state is cleared completely and returns to login-only screen."""
        from app import clear_user_session

        class MockSessionState(dict):
            def __getattr__(self, key):
                return self.get(key)
            def __setattr__(self, key, value):
                self[key] = value

        mock_state = MockSessionState({
            "auth_user": {"id": "user-uuid-1"},
            "auth_token": "mock-token-xyz",
            "selected_shop_id": "shop-123",
            "extraction_result": {"status": "ok"},
            "preprocessed_result": {"image": "bytes"},
        })

        with patch("app.st") as mock_st, patch("app.sign_out") as mock_sign_out:
            mock_st.session_state = mock_state
            clear_user_session()
            mock_sign_out.assert_called_once()
            self.assertIsNone(mock_state["auth_user"])
            self.assertIsNone(mock_state["auth_token"])
            self.assertIsNone(mock_state["selected_shop_id"])
            self.assertIsNone(mock_state["extraction_result"])
            self.assertIsNone(mock_state["preprocessed_result"])

    def test_login_tab_labels_present_across_all_languages(self) -> None:
        """Verifies clear shopkeeper and admin login labels across all five languages."""
        from core.i18n import get_language_manager

        mgr = get_language_manager()
        languages = ["en", "kn", "hi", "te", "ta"]

        for lang in languages:
            sk_label = mgr.translate("auth.shopkeeper_login", lang=lang)
            admin_label = mgr.translate("auth.admin_login", lang=lang)
            sk_btn = mgr.translate("auth.login_btn_shopkeeper", lang=lang)
            admin_btn = mgr.translate("auth.login_btn_admin", lang=lang)

            self.assertTrue(bool(sk_label))
            self.assertTrue(bool(admin_label))
            self.assertTrue(bool(sk_btn))
            self.assertTrue(bool(admin_btn))
            self.assertNotEqual(sk_label, "auth.shopkeeper_login")
            self.assertNotEqual(admin_label, "auth.admin_login")

    def test_admin_create_shopkeeper_account_success(self) -> None:
        """Verifies admin_create_shopkeeper_account provisions user via admin client."""
        mock_admin_api = MagicMock()
        mock_user = MagicMock(id="new-shopkeeper-uuid-777")
        mock_res = MagicMock(user=mock_user)
        mock_admin_api.create_user.return_value = mock_res

        mock_auth = MagicMock(admin=mock_admin_api)
        mock_client = MagicMock(auth=mock_auth)

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}

        ok, user_uuid, err = admin_create_shopkeeper_account(
            email="newkeeper@kirana.in",
            password="securepwd123",
            user_metadata={"name": "Suresh"},
            current_admin_user=admin_user,
            admin_client=mock_client,
        )
        self.assertTrue(ok)
        self.assertEqual(user_uuid, "new-shopkeeper-uuid-777")
        self.assertIsNone(err)

        mock_admin_api.create_user.assert_called_once()
        call_attrs = mock_admin_api.create_user.call_args[0][0]
        self.assertEqual(call_attrs["email"], "newkeeper@kirana.in")
        self.assertEqual(call_attrs["password"], "securepwd123")
        self.assertTrue(call_attrs["email_confirm"])
        self.assertEqual(call_attrs["user_metadata"]["name"], "Suresh")

    def test_admin_create_shopkeeper_account_unauthorized(self) -> None:
        """Non-admin users cannot provision shopkeeper accounts."""
        regular_user = {"email": "shopkeeper@kirana.in", "app_metadata": {}}
        ok, user_uuid, err = admin_create_shopkeeper_account(
            email="someone@kirana.in",
            password="password123",
            current_admin_user=regular_user,
        )
        self.assertFalse(ok)
        self.assertIsNone(user_uuid)
        self.assertIn("Admin authorization required", err or "")

    def test_admin_create_shopkeeper_account_duplicate_email(self) -> None:
        """Handles duplicate email error cleanly and returns clear user-friendly guidance."""
        mock_admin_api = MagicMock()
        mock_admin_api.create_user.side_effect = Exception("User already registered with this email")
        mock_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}
        ok, user_uuid, err = admin_create_shopkeeper_account(
            email="existing@kirana.in",
            password="password123",
            current_admin_user=admin_user,
            admin_client=mock_client,
        )
        self.assertFalse(ok)
        self.assertIsNone(user_uuid)
        self.assertIn("already exists", err or "")

    def test_admin_create_user_success_without_shop_creation(self) -> None:
        """Verifies admin_create_user creates user account with email/pwd and DOES NOT create a shop."""
        mock_admin_api = MagicMock()
        mock_admin_api.create_user.return_value = MagicMock(user=MagicMock(id="user-xyz-123"))
        mock_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}
        ok, user_id, err = admin_create_user(
            email="newuser@kirana.in",
            password="securepwd123",
            user_metadata={"name": "New User"},
            current_admin_user=admin_user,
            admin_client=mock_client,
        )
        self.assertTrue(ok)
        self.assertEqual(user_id, "user-xyz-123")
        self.assertIsNone(err)

        # Assert create_user called with proper params
        mock_admin_api.create_user.assert_called_once()
        call_args = mock_admin_api.create_user.call_args[0][0]
        self.assertEqual(call_args["email"], "newuser@kirana.in")
        self.assertEqual(call_args["password"], "securepwd123")
        self.assertTrue(call_args["email_confirm"])
        self.assertEqual(call_args["user_metadata"]["name"], "New User")

    def test_admin_create_user_unauthorized_blocked(self) -> None:
        """Non-admin user cannot invoke admin_create_user."""
        normal_user = {"email": "user@kirana.in", "app_metadata": {}}
        ok, user_id, err = admin_create_user(
            email="victim@kirana.in",
            password="password123",
            current_admin_user=normal_user,
        )
        self.assertFalse(ok)
        self.assertIsNone(user_id)
        self.assertIn("Admin authorization required", err or "")

    def test_admin_list_users_authorized(self) -> None:
        """Verifies admin_list_users returns user details including email, id, name, created_at."""
        mock_u1 = MagicMock(id="u1", email="user1@kirana.in", user_metadata={"name": "User One"}, created_at="2026-01-01")
        mock_u2 = MagicMock(id="u2", email="user2@kirana.in", user_metadata={}, created_at="2026-01-02")
        mock_admin_api = MagicMock()
        mock_admin_api.list_users.return_value = [mock_u1, mock_u2]
        mock_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}
        users = admin_list_users(current_admin_user=admin_user, admin_client=mock_client)
        self.assertEqual(len(users), 2)
        self.assertEqual(users[0]["email"], "user1@kirana.in")
        self.assertEqual(users[0]["name"], "User One")
        self.assertEqual(users[1]["email"], "user2@kirana.in")
        self.assertEqual(users[1]["name"], "user2@kirana.in")

    def test_provision_shop_with_owner_success(self) -> None:
        """Verifies full automated provisioning: creates user account then creates and links shop."""
        mock_admin_api = MagicMock()
        mock_admin_api.create_user.return_value = MagicMock(
            user=MagicMock(id="auto-user-uuid-999")
        )
        mock_admin_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        mock_db_table = MagicMock()
        mock_db_table.insert.return_value.execute.return_value = MagicMock(
            data=[{"id": "auto-shop-uuid-888"}]
        )
        mock_db_client = MagicMock()
        mock_db_client.table.return_value = mock_db_table

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}

        ok, shop_id, user_uuid, err = provision_shop_with_owner(
            shop_data={"name": "Auto Kirana Store", "owner_name": "Ramesh"},
            owner_email="ramesh@kirana.in",
            owner_password="password123",
            current_admin_user=admin_user,
            admin_client=mock_admin_client,
            db_client=mock_db_client,
        )
        self.assertTrue(ok)
        self.assertEqual(shop_id, "auto-shop-uuid-888")
        self.assertEqual(user_uuid, "auto-user-uuid-999")
        self.assertIsNone(err)

        mock_db_table.insert.assert_called_once()
        inserted = mock_db_table.insert.call_args[0][0]
        self.assertEqual(inserted["name"], "Auto Kirana Store")
        self.assertEqual(inserted["owner_id"], "auto-user-uuid-999")

    def test_provision_shop_with_owner_partial_failure(self) -> None:
        """If user creation succeeds but shop creation fails, returns User UUID with recovery path."""
        mock_admin_api = MagicMock()
        mock_admin_api.create_user.return_value = MagicMock(
            user=MagicMock(id="user-created-uuid-111")
        )
        mock_admin_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        mock_db_table = MagicMock()
        mock_db_table.insert.return_value.execute.side_effect = Exception("Database connection failure")
        mock_db_client = MagicMock()
        mock_db_client.table.return_value = mock_db_table

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}

        ok, shop_id, user_uuid, err = provision_shop_with_owner(
            shop_data={"name": "Failed Kirana"},
            owner_email="orphan@kirana.in",
            owner_password="password123",
            current_admin_user=admin_user,
            admin_client=mock_admin_client,
            db_client=mock_db_client,
        )
        self.assertFalse(ok)
        self.assertIsNone(shop_id)
        self.assertEqual(user_uuid, "user-created-uuid-111")
        self.assertIn("orphan@kirana.in", err or "")
        self.assertIn("shop registration failed", err or "")
        mock_admin_api.delete_user.assert_called_once_with("user-created-uuid-111")

    def test_admin_list_shopkeepers_authorized(self) -> None:
        """Verifies admin_list_shopkeepers returns shopkeeper details for an authorized admin."""
        mock_user1 = MagicMock(id="uuid-1", email="ramesh@kirana.in", user_metadata={"name": "Ramesh"})
        mock_user2 = MagicMock(id="uuid-2", email="suresh@kirana.in", user_metadata={})
        mock_admin_api = MagicMock()
        mock_admin_api.list_users.return_value = [mock_user1, mock_user2]
        mock_admin_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}
        shopkeepers = admin_list_shopkeepers(
            current_admin_user=admin_user,
            admin_client=mock_admin_client,
        )
        self.assertEqual(len(shopkeepers), 2)
        self.assertEqual(shopkeepers[0]["email"], "ramesh@kirana.in")
        self.assertEqual(shopkeepers[0]["name"], "Ramesh")
        self.assertEqual(shopkeepers[1]["email"], "suresh@kirana.in")
        self.assertEqual(shopkeepers[1]["name"], "suresh@kirana.in")

    def test_admin_list_shopkeepers_unauthorized(self) -> None:
        """Non-admin users cannot list shopkeeper accounts."""
        non_admin = {"email": "shopkeeper@kirana.in", "app_metadata": {}}
        shopkeepers = admin_list_shopkeepers(current_admin_user=non_admin)
        self.assertEqual(shopkeepers, [])

    def test_admin_get_user_by_email(self) -> None:
        """Verifies email-to-UUID lookup succeeds case-insensitively and returns None when missing."""
        mock_user = MagicMock(id="target-uuid-555", email="found@kirana.in", user_metadata={"name": "Pooja"})
        mock_admin_api = MagicMock()
        mock_admin_api.list_users.return_value = [mock_user]
        mock_admin_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        found = admin_get_user_by_email("FOUND@kirana.in", admin_client=mock_admin_client)
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "target-uuid-555")

        missing = admin_get_user_by_email("missing@kirana.in", admin_client=mock_admin_client)
        self.assertIsNone(missing)

    def test_admin_assign_shop_by_email_success(self) -> None:
        """Verifies admin can reassign a shop by shopkeeper email without dealing with UUIDs."""
        mock_user = MagicMock(id="resolved-uuid-777", email="assignee@kirana.in", user_metadata={})
        mock_admin_api = MagicMock()
        mock_admin_api.list_users.return_value = [mock_user]
        mock_admin_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        mock_db_table = MagicMock()
        mock_db_table.update.return_value.eq.return_value.execute.return_value = MagicMock(
            data=[{"id": "shop-456", "owner_id": "resolved-uuid-777"}]
        )
        mock_db_client = MagicMock()
        mock_db_client.table.return_value = mock_db_table

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}
        ok, err = admin_assign_shop_by_email(
            shop_id="shop-456",
            shopkeeper_email="assignee@kirana.in",
            current_admin_user=admin_user,
            admin_client=mock_admin_client,
            db_client=mock_db_client,
        )
        self.assertTrue(ok)
        self.assertIsNone(err)

        mock_db_table.update.assert_called_with({"owner_id": "resolved-uuid-777"})

    def test_admin_assign_shop_by_email_unauthorized(self) -> None:
        """Non-admin user cannot invoke shop reassignment."""
        non_admin = {"email": "user@kirana.in", "app_metadata": {}}
        ok, err = admin_assign_shop_by_email(
            shop_id="shop-456",
            shopkeeper_email="other@kirana.in",
            current_admin_user=non_admin,
        )
        self.assertFalse(ok)
        self.assertIn("Admin authorization required", err or "")

    def test_admin_assign_shop_by_email_user_not_found(self) -> None:
        """Returns clear error when the target shopkeeper email is not found."""
        mock_admin_api = MagicMock()
        mock_admin_api.list_users.return_value = []
        mock_admin_client = MagicMock(auth=MagicMock(admin=mock_admin_api))

        admin_user = {"email": "admin@tonup.in", "app_metadata": {"role": "admin"}}
        ok, err = admin_assign_shop_by_email(
            shop_id="shop-456",
            shopkeeper_email="unknown@kirana.in",
            current_admin_user=admin_user,
            admin_client=mock_admin_client,
        )
        self.assertFalse(ok)
        self.assertIn("was not found", err or "")

    def test_admin_ui_simplified_for_single_shop_owner(self) -> None:
        """Ensures that app.py removes unnecessary multi-shopkeeper forms and raw UUID inputs."""
        from pathlib import Path
        app_code = Path("app.py").read_text(encoding="utf-8")

        self.assertNotIn("manual_target_uuid", app_code)
        self.assertNotIn("reassign_target_uuid", app_code)
        self.assertNotIn("admin_manual_shop_form", app_code)
        self.assertNotIn("admin_reassign_by_email_form", app_code)
        self.assertNotIn("admin_provision_form", app_code)

    def test_single_owner_shop_isolation(self) -> None:
        """Verifies that an owner can access only their own shop through resolve_owner_shop."""
        from core.db import resolve_owner_shop

        mock_client = MagicMock()
        mock_response = MagicMock(data=[
            {"id": "shop-owner-1", "name": "Owner 1 Store", "owner_id": "owner-1"}
        ])
        mock_client.table.return_value.select.return_value.order.return_value.eq.return_value.execute.return_value = mock_response

        # When owner-1 logs in
        sid, sname, sdict = resolve_owner_shop(
            owner_id="owner-1",
            client=mock_client,
            is_admin=False,
        )
        self.assertEqual(sid, "shop-owner-1")
        self.assertEqual(sname, "Owner 1 Store")
        self.assertEqual(sdict["owner_id"], "owner-1")

        # Verify query explicitly filtered by owner_id == owner-1
        mock_client.table("shops").select.return_value.order.return_value.eq.assert_called_with("owner_id", "owner-1")


if __name__ == "__main__":
    unittest.main()
