"""Supabase Authentication service for Kirana Ledger Digitizer.

Manages shopkeeper sign-up, sign-in, session tokens, and authenticated
Supabase client creation to support Row Level Security (RLS).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from core.config import get_config, is_placeholder_secret


def get_auth_client(client: Optional[Any] = None) -> Optional[Any]:
    """Retrieves the Supabase Auth (GoTrue) client instance."""
    if client is not None and hasattr(client, "auth"):
        return client.auth

    from core.db import get_db_manager

    base_client = get_db_manager().get_client()
    if base_client is not None and hasattr(base_client, "auth"):
        return base_client.auth
    return None


def get_authenticated_client(access_token: Optional[str] = None) -> Optional[Any]:
    """Initializes and returns a Supabase client configured with a user's JWT access token.

    If no access token is provided or client cannot be created, returns the base client.
    """
    config = get_config()
    if not config.is_supabase_configured:
        return None

    if not access_token:
        from core.db import get_db_manager

        return get_db_manager().get_client()

    try:
        from supabase import ClientOptions, create_client

        options = ClientOptions(headers={"Authorization": f"Bearer {access_token}"})
        client = create_client(config.supabase_url, config.supabase_key, options=options)
        if hasattr(client, "postgrest"):
            client.postgrest.auth(access_token)
        return client
    except Exception:
        from core.db import get_db_manager

        return get_db_manager().get_client()


def sign_up(
    email: str,
    password: str,
    user_metadata: Optional[Dict[str, Any]] = None,
    client: Optional[Any] = None,
) -> Tuple[bool, Optional[Any], Optional[str]]:
    """Registers a new shopkeeper account in Supabase Auth.

    Returns:
        (success, response_or_user, error_message)
    """
    clean_email = (email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return False, None, "A valid email address is required."

    if not password or len(password) < 6:
        return False, None, "Password must be at least 6 characters long."

    auth = get_auth_client(client)
    if auth is None:
        return False, None, "Supabase client is not connected or configured in .env."

    try:
        credentials: Dict[str, Any] = {
            "email": clean_email,
            "password": password,
        }
        if user_metadata:
            credentials["options"] = {"data": user_metadata}

        res = auth.sign_up(credentials)
        return True, res, None
    except Exception as exc:
        err = str(exc)
        return False, None, err


def sign_in(
    email: str,
    password: str,
    client: Optional[Any] = None,
) -> Tuple[bool, Optional[Any], Optional[str]]:
    """Authenticates an existing shopkeeper and retrieves a session.

    Returns:
        (success, response, error_message)
    """
    clean_email = (email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return False, None, "A valid email address is required."

    if not password:
        return False, None, "Password cannot be empty."

    auth = get_auth_client(client)
    if auth is None:
        return False, None, "Supabase client is not connected or configured in .env."

    try:
        credentials = {"email": clean_email, "password": password}
        res = auth.sign_in_with_password(credentials)
        session = getattr(res, "session", None)
        user = getattr(res, "user", None)
        if session or user:
            return True, res, None
        return False, None, "Invalid email or password."
    except Exception as exc:
        err = str(exc)
        if "invalid_credentials" in err.lower() or "invalid login credentials" in err.lower():
            return False, None, "Invalid email or password."
        return False, None, err


def sign_out(client: Optional[Any] = None) -> Tuple[bool, Optional[str]]:
    """Signs out the currently authenticated user."""
    auth = get_auth_client(client)
    if auth is None:
        return True, None

    try:
        auth.sign_out()
        return True, None
    except Exception as exc:
        return False, str(exc)


def get_current_user(
    access_token: Optional[str] = None,
    client: Optional[Any] = None,
) -> Optional[Any]:
    """Retrieves user profile for the current session or access token."""
    auth = get_auth_client(client)
    if auth is None:
        return None

    try:
        if access_token:
            resp = auth.get_user(access_token)
            return getattr(resp, "user", None)
        resp = auth.get_user()
        return getattr(resp, "user", None)
    except Exception:
        return None


def is_admin_user(user: Optional[Any]) -> bool:
    """Securely checks whether a user has administrative authorization.

    Evaluates server-controlled attributes only:
    1. user.app_metadata.get('role') == 'admin' (set via Supabase service-role/dashboard).
    2. user.email matches ADMIN_EMAIL configured in the application environment.

    Security Guarantee: Does NOT trust user_metadata which can be modified by clients.
    """
    if not user:
        return False

    app_meta = None
    email = None
    if isinstance(user, dict):
        app_meta = user.get("app_metadata")
        email = user.get("email")
    else:
        app_meta = getattr(user, "app_metadata", None)
        email = getattr(user, "email", None)

    if isinstance(app_meta, dict) and app_meta.get("role") == "admin":
        return True

    config = get_config()
    target_admin = (config.admin_email or "").strip().lower()
    user_email = (str(email) if email else "").strip().lower()
    if target_admin and user_email and target_admin == user_email:
        return True

    return False


def get_admin_auth_client(service_role_key: Optional[str] = None) -> Optional[Any]:
    """Initializes and returns a privileged Supabase client for administrative auth operations.

    Uses the service_role key which possesses full administrative rights.
    Security guarantee: Never expose this client to frontend/client-facing scopes.
    """
    config = get_config()
    key = service_role_key or config.supabase_service_role_key
    if not config.supabase_url or not key or is_placeholder_secret(key):
        return None

    try:
        from supabase import create_client

        return create_client(config.supabase_url, key)
    except Exception:
        return None


def admin_create_shopkeeper_account(
    email: str,
    password: str,
    user_metadata: Optional[Dict[str, Any]] = None,
    current_admin_user: Optional[Any] = None,
    admin_client: Optional[Any] = None,
    service_role_key: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Creates a new shopkeeper account with administrator authorization.

    1. Enforces admin authorization via is_admin_user(current_admin_user).
    2. Validates email and password format.
    3. Provisions the user with email_confirm=True via Supabase Admin API.
    4. Automatically extracts and returns the user's UUID.

    Returns:
        (success, user_uuid, error_message)
    """
    if not is_admin_user(current_admin_user):
        return False, None, "Admin authorization required to provision shopkeeper accounts."

    clean_email = (email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return False, None, "A valid email address is required."

    if not password or len(password) < 6:
        return False, None, "Password must be at least 6 characters long."

    # Tier 1: Privileged Admin API via service_role key
    client = admin_client or get_admin_auth_client(service_role_key)
    if client is not None and hasattr(client, "auth") and hasattr(client.auth, "admin"):
        try:
            attrs = {
                "email": clean_email,
                "password": password,
                "email_confirm": True,
                "user_metadata": user_metadata or {},
            }
            res = client.auth.admin.create_user(attrs)
            user = getattr(res, "user", None)
            user_id = getattr(user, "id", None) if user else None
            if user_id:
                return True, str(user_id), None
            return False, None, "Failed to retrieve user ID from created account."
        except Exception as exc:
            err = str(exc)
            err_lower = err.lower()
            if (
                "already registered" in err_lower
                or "duplicate" in err_lower
                or "already exists" in err_lower
            ):
                return (
                    False,
                    None,
                    f"An account with email '{clean_email}' already exists. Use the assignment tool below to link an existing shopkeeper.",
                )
            return False, None, f"Supabase admin user creation failed: {err}"

    # Tier 2: Fallback when SUPABASE_SERVICE_ROLE_KEY is not configured
    try:
        ok, res, err = sign_up(clean_email, password, user_metadata=user_metadata)
        if ok and res:
            user = getattr(res, "user", None)
            user_id = getattr(user, "id", None) if user else None
            if user_id:
                return True, str(user_id), None

        err_msg = err or "Could not provision user automatically."
        if "already registered" in err_msg.lower():
            return (
                False,
                None,
                f"An account with email '{clean_email}' already exists. Use the assignment tool below to link an existing shopkeeper.",
            )
        return (
            False,
            None,
            f"Automated user provisioning requires SUPABASE_SERVICE_ROLE_KEY in .env. ({err_msg})",
        )
    except Exception as exc:
        return (
            False,
            None,
            f"Automated user provisioning requires SUPABASE_SERVICE_ROLE_KEY in .env. ({exc})",
        )


def admin_list_shopkeepers(
    current_admin_user: Optional[Any] = None,
    service_role_key: Optional[str] = None,
    admin_client: Optional[Any] = None,
) -> List[Dict[str, str]]:
    """Lists existing shopkeepers (email and id) for administrative assignment without exposing UUIDs to UI."""
    if not is_admin_user(current_admin_user):
        return []

    client = admin_client or get_admin_auth_client(service_role_key)
    if client is not None and hasattr(client, "auth") and hasattr(client.auth, "admin"):
        try:
            users = client.auth.admin.list_users()
            shopkeepers: List[Dict[str, str]] = []
            for u in users:
                uid = getattr(u, "id", None)
                uemail = getattr(u, "email", None)
                meta = getattr(u, "user_metadata", {}) or {}
                name = meta.get("name") if isinstance(meta, dict) else ""
                if uid and uemail:
                    shopkeepers.append(
                        {
                            "id": str(uid),
                            "email": str(uemail),
                            "name": str(name) if name else str(uemail),
                        }
                    )
            return shopkeepers
        except Exception:
            return []
    return []


def admin_get_user_by_email(
    email: str,
    admin_client: Optional[Any] = None,
    service_role_key: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Looks up a user's details and internal UUID by email using the administrative client."""
    clean_email = (email or "").strip().lower()
    if not clean_email:
        return None

    client = admin_client or get_admin_auth_client(service_role_key)
    if client is not None and hasattr(client, "auth") and hasattr(client.auth, "admin"):
        try:
            users = client.auth.admin.list_users()
            for u in users:
                u_email = (getattr(u, "email", "") or "").strip().lower()
                if u_email == clean_email:
                    return {
                        "id": str(getattr(u, "id")),
                        "email": getattr(u, "email", ""),
                        "user_metadata": getattr(u, "user_metadata", {}) or {},
                    }
        except Exception:
            return None
    return None



