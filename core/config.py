"""Configuration management module for Kirana Ledger Digitizer.

Handles environment variable loading, validation, and secret sanitization
using Pydantic v2 conventions while ensuring secrets are never leaked.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

# Supported language codes across the platform
SUPPORTED_LANGUAGES = ("kn", "en", "hi", "te", "ta")
PLACEHOLDER_SUBSTRINGS = (
    "your_",
    "here",
    "placeholder",
    "dummy",
    "example",
    "replace_me",
    "todo",
)


def _load_dotenv_simple(env_path: Path) -> Dict[str, str]:
    """Lightweight .env parser avoiding external dependencies for fallback."""
    env_vars: Dict[str, str] = {}
    if not env_path.exists():
        return env_vars

    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'\"")
                env_vars[k] = v
    except Exception:
        # Ignore read errors in simple fallback
        pass
    return env_vars


def mask_secret(secret: Optional[str], visible_suffix_len: int = 4) -> str:
    """Safely masks sensitive tokens for logging or display.

    Example:
        'ai_studio_key_123456789' -> '***************6789'
    """
    if not secret or not secret.strip():
        return "<NOT_SET>"
    if is_placeholder_secret(secret):
        return "<PLACEHOLDER_SET>"
    secret = secret.strip()
    if len(secret) <= visible_suffix_len:
        return "*" * len(secret)
    masked_len = len(secret) - visible_suffix_len
    return ("*" * masked_len) + secret[-visible_suffix_len:]


def is_placeholder_secret(value: Optional[str]) -> bool:
    """Checks if a secret is unset, empty, or contains known dummy placeholder text."""
    if not value or not value.strip():
        return True
    val_lower = value.strip().lower()
    return any(p in val_lower for p in PLACEHOLDER_SUBSTRINGS)


# Try Pydantic v2 integration, with pure-dataclass fallback if dependencies
# have not been installed yet.
try:
    from pydantic import BaseModel, Field, field_validator

    class AppConfig(BaseModel):
        """Pydantic v2 Application Configuration."""

        app_env: str = Field(default="development", description="Application environment")
        debug: bool = Field(default=False, description="Enable debug diagnostics")
        default_language: str = Field(default="kn", description="Default UI language code")

        # Gemma 4 Configuration
        gemma_api_key: str = Field(default="", description="Google GenAI / Gemma 4 API Key")
        gemma_model_name: str = Field(default="gemma-4-26b-a4b-it", description="Target Gemma model ID")

        # Supabase PostgreSQL Configuration
        supabase_url: str = Field(default="", description="Supabase project URL")
        supabase_key: str = Field(default="", description="Supabase anon or service key")
        supabase_service_role_key: str = Field(
            default="", description="Supabase service_role key for trusted server-side provisioning"
        )
        shop_id: Optional[str] = Field(default=None, description="Active Shop UUID if pre-configured")
        admin_email: str = Field(default="", description="Administrator email address for shop provisioning oversight")

        # Application Tunables
        catalog_match_threshold: float = Field(
            default=75.0, ge=0.0, le=100.0, description="RapidFuzz fuzzy score threshold"
        )
        upload_dir: str = Field(default="data/uploads", description="Directory for ledger images")
        max_upload_size_mb: int = Field(default=15, ge=1, le=100, description="Upload limit in MB")

        @field_validator("default_language")
        @classmethod
        def validate_language(cls, v: str) -> str:
            if v not in SUPPORTED_LANGUAGES:
                raise ValueError(
                    f"Unsupported language: '{v}'. Must be one of {SUPPORTED_LANGUAGES}"
                )
            return v

        @property
        def is_gemma_configured(self) -> bool:
            return not is_placeholder_secret(self.gemma_api_key)

        @property
        def is_supabase_configured(self) -> bool:
            return (
                not is_placeholder_secret(self.supabase_url)
                and not is_placeholder_secret(self.supabase_key)
            )

        @property
        def is_supabase_admin_configured(self) -> bool:
            return (
                not is_placeholder_secret(self.supabase_url)
                and not is_placeholder_secret(self.supabase_service_role_key)
            )

        def get_safe_summary(self) -> Dict[str, Any]:
            """Returns config dict safe for logging/display without secret exposure."""
            return {
                "app_env": self.app_env,
                "debug": self.debug,
                "default_language": self.default_language,
                "gemma_model_name": self.gemma_model_name,
                "gemma_api_key_status": mask_secret(self.gemma_api_key),
                "is_gemma_configured": self.is_gemma_configured,
                "supabase_url": self.supabase_url if not is_placeholder_secret(self.supabase_url) else "<NOT_CONFIGURED>",
                "supabase_key_status": mask_secret(self.supabase_key),
                "supabase_service_role_key_status": mask_secret(self.supabase_service_role_key),
                "is_supabase_configured": self.is_supabase_configured,
                "is_supabase_admin_configured": self.is_supabase_admin_configured,
                "catalog_match_threshold": self.catalog_match_threshold,
                "upload_dir": self.upload_dir,
                "max_upload_size_mb": self.max_upload_size_mb,
            }

    HAS_PYDANTIC = True

except ImportError:
    HAS_PYDANTIC = False

    @dataclass
    class AppConfig:  # type: ignore[no-redef]
        """Fallback dataclass configuration when Pydantic is not installed."""

        app_env: str = "development"
        debug: bool = False
        default_language: str = "kn"
        gemma_api_key: str = ""
        gemma_model_name: str = "gemma-4-26b-a4b-it"
        supabase_url: str = ""
        supabase_key: str = ""
        supabase_service_role_key: str = ""
        shop_id: Optional[str] = None
        admin_email: str = ""
        catalog_match_threshold: float = 75.0
        upload_dir: str = "data/uploads"
        max_upload_size_mb: int = 15

        def __post_init__(self) -> None:
            if self.default_language not in SUPPORTED_LANGUAGES:
                raise ValueError(
                    f"Unsupported language: '{self.default_language}'. Must be one of {SUPPORTED_LANGUAGES}"
                )

        @property
        def is_gemma_configured(self) -> bool:
            return not is_placeholder_secret(self.gemma_api_key)

        @property
        def is_supabase_configured(self) -> bool:
            return (
                not is_placeholder_secret(self.supabase_url)
                and not is_placeholder_secret(self.supabase_key)
            )

        @property
        def is_supabase_admin_configured(self) -> bool:
            return (
                not is_placeholder_secret(self.supabase_url)
                and not is_placeholder_secret(self.supabase_service_role_key)
            )

        def get_safe_summary(self) -> Dict[str, Any]:
            return {
                "app_env": self.app_env,
                "debug": self.debug,
                "default_language": self.default_language,
                "gemma_model_name": self.gemma_model_name,
                "gemma_api_key_status": mask_secret(self.gemma_api_key),
                "is_gemma_configured": self.is_gemma_configured,
                "supabase_url": self.supabase_url if not is_placeholder_secret(self.supabase_url) else "<NOT_CONFIGURED>",
                "supabase_key_status": mask_secret(self.supabase_key),
                "supabase_service_role_key_status": mask_secret(self.supabase_service_role_key),
                "is_supabase_configured": self.is_supabase_configured,
                "is_supabase_admin_configured": self.is_supabase_admin_configured,
                "catalog_match_threshold": self.catalog_match_threshold,
                "upload_dir": self.upload_dir,
                "max_upload_size_mb": self.max_upload_size_mb,
            }


def load_config(root_dir: Optional[Path] = None) -> AppConfig:
    """Constructs application configuration from environment and optional .env file."""
    if root_dir is None:
        root_dir = Path(__file__).resolve().parent.parent

    # Try python-dotenv if installed, otherwise fallback to simple reader
    env_file = root_dir / ".env"
    dotenv_vars = _load_dotenv_simple(env_file)

    def _get(key: str, default: str = "") -> str:
        # Priority: os.environ > .env file > default
        return os.environ.get(key, dotenv_vars.get(key, default))

    app_env = _get("APP_ENV", "development").lower()
    debug_raw = _get("DEBUG", "false").lower()
    debug = debug_raw in ("true", "1", "yes", "on")

    default_lang = _get("DEFAULT_LANGUAGE", "kn").lower()
    if default_lang not in SUPPORTED_LANGUAGES:
        default_lang = "kn"

    try:
        catalog_threshold = float(_get("CATALOG_MATCH_THRESHOLD", "75.0"))
    except ValueError:
        catalog_threshold = 75.0

    try:
        max_upload = int(_get("MAX_UPLOAD_SIZE_MB", "15"))
    except ValueError:
        max_upload = 15

    # Check GEMMA_MODEL first, falling back to GEMMA_MODEL_NAME or default 'gemma-4-26b-a4b-it'
    gemma_model = _get("GEMMA_MODEL", "") or _get("GEMMA_MODEL_NAME", "gemma-4-26b-a4b-it")
    gemma_key = _get("GEMMA_API_KEY", "")

    # Optional fallback to Streamlit secrets if running inside Streamlit
    if not gemma_key:
        try:
            import streamlit as st
            if hasattr(st, "secrets"):
                gemma_key = str(st.secrets.get("GEMMA_API_KEY", "") or st.secrets.get("gemma_api_key", ""))
                if not gemma_model or gemma_model == "gemma-4-26b-a4b-it":
                    gemma_model = str(st.secrets.get("GEMMA_MODEL", "") or st.secrets.get("gemma_model", gemma_model))
        except Exception:
            pass

    supabase_service_key = (
        _get("SUPABASE_SERVICE_ROLE_KEY", "") or _get("SUPABASE_SERVICE_KEY", "")
    )

    return AppConfig(
        app_env=app_env,
        debug=debug,
        default_language=default_lang,
        gemma_api_key=gemma_key,
        gemma_model_name=gemma_model,
        supabase_url=_get("SUPABASE_URL", ""),
        supabase_key=_get("SUPABASE_KEY", ""),
        supabase_service_role_key=supabase_service_key,
        shop_id=_get("SHOP_ID", "").strip() or None,
        admin_email=_get("ADMIN_EMAIL", "").strip().lower(),
        catalog_match_threshold=catalog_threshold,
        upload_dir=_get("UPLOAD_DIR", "data/uploads"),
        max_upload_size_mb=max_upload,
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Returns cached application configuration instance."""
    return load_config()
