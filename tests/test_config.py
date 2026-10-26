"""Tests for configuration management, secret masking, and environment safety."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from core.config import (
    SUPPORTED_LANGUAGES,
    AppConfig,
    is_placeholder_secret,
    load_config,
    mask_secret,
)


class TestConfigSafety(unittest.TestCase):
    """Verifies that secrets are masked and configuration defaults are secure."""

    def test_mask_secret_masks_sensitive_values(self) -> None:
        raw_key = "ai_studio_api_key_123456789"
        masked = mask_secret(raw_key, visible_suffix_len=4)
        self.assertNotIn("12345", masked)
        self.assertTrue(masked.endswith("6789"))
        self.assertTrue(masked.startswith("*****"))

    def test_mask_secret_handles_empty_and_none(self) -> None:
        self.assertEqual(mask_secret(None), "<NOT_SET>")
        self.assertEqual(mask_secret(""), "<NOT_SET>")
        self.assertEqual(mask_secret("   "), "<NOT_SET>")

    def test_mask_secret_identifies_placeholders(self) -> None:
        self.assertEqual(mask_secret("your_gemma_api_key_here"), "<PLACEHOLDER_SET>")
        self.assertEqual(mask_secret("placeholder_key"), "<PLACEHOLDER_SET>")

    def test_is_placeholder_secret(self) -> None:
        self.assertTrue(is_placeholder_secret(None))
        self.assertTrue(is_placeholder_secret(""))
        self.assertTrue(is_placeholder_secret("your_supabase_anon_key_here"))
        self.assertTrue(is_placeholder_secret("dummy_key"))
        self.assertFalse(is_placeholder_secret("real_secret_token_abc_xyz_9988"))

    def test_safe_summary_does_not_contain_raw_secrets(self) -> None:
        cfg = AppConfig(
            gemma_api_key="very_secret_gemma_key_xyz_7777",
            supabase_key="very_secret_supabase_key_abc_8888",
            supabase_url="https://valid-project.supabase.co",
        )
        summary = cfg.get_safe_summary()
        self.assertNotIn("very_secret_gemma_key", str(summary))
        self.assertNotIn("very_secret_supabase_key", str(summary))
        self.assertTrue(summary["gemma_api_key_status"].endswith("7777"))
        self.assertTrue(summary["supabase_key_status"].endswith("8888"))

    def test_default_language_validation(self) -> None:
        for lang in SUPPORTED_LANGUAGES:
            cfg = AppConfig(default_language=lang)
            self.assertEqual(cfg.default_language, lang)

        with self.assertRaises(ValueError):
            AppConfig(default_language="invalid_language_code")

    def test_gitignore_protects_env_file(self) -> None:
        root_dir = Path(__file__).resolve().parent.parent
        gitignore_path = root_dir / ".gitignore"
        self.assertTrue(gitignore_path.exists(), ".gitignore must exist")
        content = gitignore_path.read_text(encoding="utf-8")
        lines = [line.strip() for line in content.splitlines()]
        self.assertIn(".env", lines, ".env must be explicitly ignored in .gitignore")


if __name__ == "__main__":
    unittest.main()
