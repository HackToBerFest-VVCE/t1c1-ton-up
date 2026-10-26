"""Internationalization (i18n) and Multilingual Management.

Handles loading of language packs (en, kn, hi, te, ta), fallback resolution
to English, variable interpolation, and native-speaker review status tracking.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class LanguageManager:
    """Manages multilingual packs, translation lookups, and metadata."""

    def __init__(self, languages_dir: Optional[Path] = None) -> None:
        if languages_dir is None:
            languages_dir = Path(__file__).resolve().parent.parent / "languages"
        self.languages_dir = languages_dir
        self._packs: Dict[str, Dict[str, Any]] = {}
        self._metadata: Dict[str, Dict[str, Any]] = {}
        self.load_all_languages()

    def load_all_languages(self) -> None:
        """Discovers and parses all .json translation files in languages_dir."""
        if not self.languages_dir.exists():
            return

        for json_file in sorted(self.languages_dir.glob("*.json")):
            lang_code = json_file.stem.lower()
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self._packs[lang_code] = data
                    meta = data.get("_metadata", {})
                    self._metadata[lang_code] = {
                        "code": meta.get("code", lang_code),
                        "name": meta.get("name", lang_code.title()),
                        "native_name": meta.get("native_name", meta.get("name", lang_code)),
                        "needs_native_review": bool(meta.get("needs_native_review", False)),
                        "version": meta.get("version", "1.0.0"),
                    }
            except Exception as e:
                # Log or preserve error state without crashing the manager
                self._metadata[lang_code] = {
                    "code": lang_code,
                    "name": lang_code,
                    "native_name": lang_code,
                    "needs_native_review": True,
                    "error": str(e),
                }

    def get_available_languages(self) -> List[Dict[str, Any]]:
        """Returns a list of metadata dictionaries for all loaded languages."""
        return list(self._metadata.values())

    def get_language_metadata(self, lang: str) -> Optional[Dict[str, Any]]:
        """Returns metadata dictionary for a specific language code."""
        return self._metadata.get(lang.lower())

    def needs_native_review(self, lang: str) -> bool:
        """Returns True if the language is marked as requiring native speaker review."""
        meta = self.get_language_metadata(lang)
        return bool(meta.get("needs_native_review", False)) if meta else False

    def is_language_supported(self, lang: str) -> bool:
        """Checks if a language code has been loaded."""
        return lang.lower() in self._packs

    def translate(
        self,
        key: str,
        lang: str = "en",
        fallback_lang: str = "en",
        default: Optional[str] = None,
        **kwargs: Any,
    ) -> str:
        """Translates a dotted key path with fallback and string formatting.

        Args:
            key: Dotted key path, e.g. "ledger.total_amount" or "common.confirm"
            lang: Target language code (e.g. "kn", "hi", "te", "ta", "en")
            fallback_lang: Secondary language code to check if key missing (default "en")
            default: Custom fallback string if key is completely missing
            kwargs: Values for dynamic placeholders like {count}, {item}

        Returns:
            Resolved translation string.
        """
        lang = lang.lower()
        fallback_lang = fallback_lang.lower()

        # Step 1: Look in target language pack
        val = self._resolve_dotted_key(self._packs.get(lang, {}), key)

        # Step 2: Fallback to fallback_lang if not found
        if val is None and lang != fallback_lang:
            val = self._resolve_dotted_key(self._packs.get(fallback_lang, {}), key)

        # Step 3: Default fallback
        if val is None:
            val = default if default is not None else key

        # Step 4: String interpolation if kwargs provided
        if kwargs and isinstance(val, str):
            try:
                return val.format(**kwargs)
            except Exception:
                return val

        return str(val)

    def _resolve_dotted_key(self, data: Dict[str, Any], key: str) -> Optional[Any]:
        """Traverses a nested dictionary using a dotted key string."""
        parts = key.split(".")
        current: Any = data
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current


# Global singleton instance
_GLOBAL_MANAGER: Optional[LanguageManager] = None


def get_language_manager() -> LanguageManager:
    """Returns singleton LanguageManager instance."""
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is None:
        _GLOBAL_MANAGER = LanguageManager()
    return _GLOBAL_MANAGER


def t(
    key: str,
    lang: str = "en",
    fallback_lang: str = "en",
    default: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Convenience translation function."""
    return get_language_manager().translate(
        key=key,
        lang=lang,
        fallback_lang=fallback_lang,
        default=default,
        **kwargs,
    )
