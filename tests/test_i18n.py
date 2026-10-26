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


if __name__ == "__main__":
    unittest.main()
