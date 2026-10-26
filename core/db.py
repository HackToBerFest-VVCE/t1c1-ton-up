"""Supabase PostgreSQL connection and client management.

Provides safe client initialization, diagnostics, and graceful fallback
when running with placeholder configuration in development/testing.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.config import get_config, is_placeholder_secret


class SupabaseConnectionManager:
    """Manages Supabase client lifecycle and connection diagnostics."""

    def __init__(self) -> None:
        self.config = get_config()
        self._client: Optional[Any] = None

    def get_status(self) -> Dict[str, Any]:
        """Returns connection readiness diagnostics without exposing keys."""
        is_configured = self.config.is_supabase_configured
        has_sdk = self._is_sdk_available()

        if not has_sdk:
            message = "Supabase SDK (supabase-py) is not yet installed."
            state = "missing_dependency"
        elif not is_configured:
            message = "Supabase credentials are not configured or are set to placeholder values in .env."
            state = "unconfigured"
        else:
            message = "Supabase configuration is present and ready for Milestone 2 connection."
            state = "ready"

        return {
            "status": state,
            "has_sdk": has_sdk,
            "is_configured": is_configured,
            "url_set": not is_placeholder_secret(self.config.supabase_url),
            "key_set": not is_placeholder_secret(self.config.supabase_key),
            "message": message,
        }

    def _is_sdk_available(self) -> bool:
        try:
            import supabase  # noqa: F401
            return True
        except ImportError:
            return False

    def get_client(self) -> Optional[Any]:
        """Initializes and returns Supabase client if configured."""
        if not self.config.is_supabase_configured or not self._is_sdk_available():
            return None

        if self._client is None:
            try:
                from supabase import create_client
                self._client = create_client(self.config.supabase_url, self.config.supabase_key)
            except Exception:
                self._client = None
        return self._client


_GLOBAL_DB: Optional[SupabaseConnectionManager] = None


def get_db_manager() -> SupabaseConnectionManager:
    """Returns singleton SupabaseConnectionManager instance."""
    global _GLOBAL_DB
    if _GLOBAL_DB is None:
        _GLOBAL_DB = SupabaseConnectionManager()
    return _GLOBAL_DB
