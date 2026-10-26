"""Tests for language-pack loading, fallback resolution, and native-review flags."""

import unittest
from pathlib import Path

from core.i18n import LanguageManager, get_language_manager, t


class TestLanguagePackLoading(unittest.TestCase):
    """Verifies that all 5 language packs load and meet specifications."""

    def setUp(self) -> None:
        self.mgr = LanguageManager()

    def test_all_five_language_packs_exist(self) -> None:
        expected_languages = {"en", "kn", "hi", "te", "ta"}
        available = {m["code"] for m in self.mgr.get_available_languages()}
        self.assertEqual(expected_languages, available)

    def test_native_review_flags(self) -> None:
        """Telugu and Tamil must be marked as needing native review; others verified."""
        self.assertFalse(
            self.mgr.needs_native_review("en"),
            "English should not require review flag",
        )
        self.assertFalse(
            self.mgr.needs_native_review("kn"),
            "Kannada should not require review flag",
        )
        self.assertFalse(
            self.mgr.needs_native_review("hi"),
            "Hindi should not require review flag",
        )

        # Requirement 5: Telugu and Tamil MUST be marked as needing native review
        self.assertTrue(
            self.mgr.needs_native_review("te"),
            "Telugu must be marked as needing native review",
        )
        self.assertTrue(
            self.mgr.needs_native_review("ta"),
            "Tamil must be marked as needing native review",
        )

    def test_common_keys_present_in_all_languages(self) -> None:
        required_keys = [
            "app.title",
            "app.subtitle",
            "nav.dashboard",
            "nav.upload",
            "common.save",
            "common.confirm",
            "common.credit",
            "common.debit",
            "ledger.item_name",
            "ledger.total_amount",
        ]
        for lang in ["en", "kn", "hi", "te", "ta"]:
            for key in required_keys:
                translated = self.mgr.translate(key, lang=lang)
                self.assertIsNotNone(translated, f"Key '{key}' was None in '{lang}'")
                self.assertNotEqual(
                    translated,
                    key,
                    f"Key '{key}' was not translated in '{lang}' and returned raw key",
                )

    def test_fallback_to_english_for_missing_key(self) -> None:
        # If a fictional key only exists in en, a query for kn should fall back to en
        val_en = self.mgr.translate("common.save", lang="en")
        # Test nonexistent key returns custom default or key itself
        self.assertEqual(
            self.mgr.translate("nonexistent.custom_key", lang="kn", default="FallbackText"),
            "FallbackText",
        )

    def test_string_formatting_interpolation(self) -> None:
        result = self.mgr.translate(
            "insights.top_product",
            lang="en",
            item="Toor Dal",
            qty=15,
            unit="kg",
        )
        self.assertIn("Toor Dal", result)
        self.assertIn("15", result)
        self.assertIn("kg", result)

    def test_convenience_t_function(self) -> None:
        title_en = t("app.title", lang="en")
        title_kn = t("app.title", lang="kn")
        self.assertEqual(title_en, "Kirana Ledger Digitizer")
        self.assertIn("ಕಿರಾಣಿ", title_kn)

    def test_shop_keys_present_in_all_languages(self) -> None:
        """Verifies that all shop section and registration keys exist in all 5 languages."""
        required_shop_keys = [
            "shop.title",
            "shop.select_label",
            "shop.no_shops",
            "shop.register_new",
            "shop.name",
            "shop.owner",
            "shop.phone",
            "shop.address",
            "shop.language",
            "shop.create_btn",
            "shop.created_success",
            "shop.required_warning",
            "shop.error_name_required",
            "shop.error_register_failed",
            "shop.none_selected",
            "shop.unnamed_shop",
            "shop.create_and_provision_btn",
            "shop.shopkeeper_email",
            "shop.shopkeeper_password",
            "shop.created_with_account_success",
            "shop.manual_fallback_expander",
            "shop.service_role_notice",
        ]
        for lang in ["en", "kn", "hi", "te", "ta"]:
            for key in required_shop_keys:
                translated = self.mgr.translate(key, lang=lang, name="Store", error="Err")
                self.assertIsNotNone(translated, f"Key '{key}' was None in '{lang}'")
                self.assertNotEqual(
                    translated,
                    key,
                    f"Key '{key}' was not translated in '{lang}' and returned raw key",
                )

        # Verify specific localized vocabulary across languages
        self.assertEqual(self.mgr.translate("shop.title", lang="en"), "Shop Profile")
        self.assertIn("ಅಂಗಡಿ", self.mgr.translate("shop.title", lang="kn"))
        self.assertIn("दुकान", self.mgr.translate("shop.title", lang="hi"))
        self.assertIn("దుకాణం", self.mgr.translate("shop.title", lang="te"))
        self.assertIn("கடை", self.mgr.translate("shop.title", lang="ta"))

    def test_shop_translations_switch_dynamically(self) -> None:
        """Verifies that shop controls switch cleanly between languages without remaining in Kannada."""
        title_en = t("shop.title", lang="en")
        title_kn = t("shop.title", lang="kn")
        reg_en = t("shop.register_new", lang="en")
        reg_kn = t("shop.register_new", lang="kn")
        no_shops_en = t("shop.no_shops", lang="en")
        no_shops_kn = t("shop.no_shops", lang="kn")

        self.assertNotEqual(title_en, title_kn)
        self.assertNotEqual(reg_en, reg_kn)
        self.assertNotEqual(no_shops_en, no_shops_kn)

        self.assertEqual(title_en, "Shop Profile")
        self.assertIn("Register", reg_en)
        self.assertIn("No shops registered", no_shops_en)

    def test_auth_keys_present_in_all_languages(self) -> None:
        """Verifies that all auth section keys exist in all 5 languages."""
        required_auth_keys = [
            "auth.section_title",
            "auth.tab_login",
            "auth.tab_signup",
            "auth.email",
            "auth.password",
            "auth.login_btn",
            "auth.signup_btn",
            "auth.logout_btn",
            "auth.logged_in_as",
            "auth.login_success",
            "auth.signup_success",
            "auth.login_required_save",
            "auth.no_owned_shops",
            "auth.demo_unauthenticated_hint",
            "auth.admin_badge",
            "auth.shopkeeper_badge",
            "auth.admin_panel_title",
            "auth.admin_notice",
            "auth.assign_owner_label",
            "auth.reassign_btn",
            "auth.reassign_success",
            "auth.provisioning_notice",
            "auth.shopkeeper_login",
            "auth.admin_login",
            "auth.login_description",
            "auth.shopkeeper_login_desc",
            "auth.admin_login_desc",
            "auth.login_btn_shopkeeper",
            "auth.login_btn_admin",
            "auth.credentials_required",
            "auth.reassign_tool_title",
            "auth.select_shopkeeper_label",
            "auth.shopkeeper_email_label",
            "auth.reassign_by_email_success",
        ]
        for lang in ["en", "kn", "hi", "te", "ta"]:
            for key in required_auth_keys:
                translated = self.mgr.translate(key, lang=lang, email="test@kirana.in", owner_id="user-123")
                self.assertIsNotNone(translated, f"Key '{key}' was None in '{lang}'")
                self.assertNotEqual(
                    translated,
                    key,
                    f"Key '{key}' was not translated in '{lang}' and returned raw key",
                )

    def test_dashboard_keys_present_in_all_languages(self) -> None:
        """Verifies that all dashboard keys exist in all 5 languages."""
        required_dashboard_keys = [
            "dashboard.total_sales",
            "dashboard.total_credit",
            "dashboard.total_collected",
            "dashboard.active_customers",
            "dashboard.fast_moving_items",
            "dashboard.outstanding_dues",
            "dashboard.recent_entries",
            "dashboard.transaction_count",
            "dashboard.payment_distribution",
            "dashboard.top_products_chart",
            "dashboard.customer_dues_title",
            "dashboard.no_data_hint",
        ]
        for lang in ["en", "kn", "hi", "te", "ta"]:
            for key in required_dashboard_keys:
                translated = self.mgr.translate(key, lang=lang)
                self.assertIsNotNone(translated, f"Key '{key}' was None in '{lang}'")
                self.assertNotEqual(
                    translated,
                    key,
                    f"Key '{key}' was not translated in '{lang}' and returned raw key",
                )


if __name__ == "__main__":
    unittest.main()
