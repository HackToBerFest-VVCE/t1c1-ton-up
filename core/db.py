"""Supabase PostgreSQL connection and client management.

Provides safe client initialization, diagnostics, and graceful fallback
when running with placeholder configuration in development/testing.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

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


def _resolve_client(
    client: Optional[Any] = None, access_token: Optional[str] = None
) -> Optional[Any]:
    """Helper to resolve the appropriate Supabase client with token authentication if provided."""
    if client is not None:
        return client
    if access_token:
        try:
            from core.auth import get_authenticated_client
            return get_authenticated_client(access_token)
        except Exception:
            pass
    return get_db_manager().get_client()


def fetch_available_shops(
    client: Optional[Any] = None,
    owner_id: Optional[str] = None,
    access_token: Optional[str] = None,
    is_admin: bool = False,
) -> List[Dict[str, Any]]:
    """Retrieves registered kirana stores from Supabase.

    If is_admin is True, retrieves all stores for platform management.
    Otherwise, if owner_id is provided, filters for shops owned by that user.
    Returns an empty list if offline, unconfigured, or on query error.
    """
    client = _resolve_client(client=client, access_token=access_token)
    if client is None:
        return []

    try:
        query = (
            client.table("shops")
            .select("id, name, owner_name, phone, address, default_language, owner_id, created_at")
            .order("created_at", desc=True)
        )
        if not is_admin and owner_id:
            query = query.eq("owner_id", owner_id)
        response = query.execute()
        return response.data or []
    except Exception:
        return []


def verify_shop_exists(
    shop_id: str, client: Optional[Any] = None, access_token: Optional[str] = None
) -> bool:
    """Verifies that a shop UUID exists in the database."""
    if not shop_id:
        return False
    client = _resolve_client(client=client, access_token=access_token)
    if client is None:
        return False

    try:
        response = client.table("shops").select("id").eq("id", shop_id).execute()
        return bool(response.data and len(response.data) > 0)
    except Exception:
        return False


def create_shop(
    shop_data: Dict[str, Any],
    client: Optional[Any] = None,
    owner_id: Optional[str] = None,
    access_token: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """Creates a new shop record in Supabase.

    Returns:
        (success, shop_id, error_message)
    """
    name = str(shop_data.get("name") or "").strip()
    if not name:
        return False, None, "Shop name is required."

    target_owner_id = owner_id or shop_data.get("owner_id")

    payload: Dict[str, Any] = {
        "name": name,
        "owner_name": str(shop_data.get("owner_name") or "").strip() or None,
        "phone": str(shop_data.get("phone") or "").strip() or None,
        "address": str(shop_data.get("address") or "").strip() or None,
        "default_language": str(shop_data.get("default_language") or "kn").strip() or "kn",
    }
    if target_owner_id:
        payload["owner_id"] = str(target_owner_id).strip()

    client = _resolve_client(client=client, access_token=access_token)
    if client is None:
        return False, None, "Supabase client is not connected or configured."

    try:
        response = client.table("shops").insert(payload).execute()
        if response.data and len(response.data) > 0:
            created_id = response.data[0].get("id")
            return True, created_id, None
        return False, None, "Database did not return a valid shop ID."
    except Exception as exc:
        err_msg = str(exc)
        if "42501" in err_msg or "row-level security" in err_msg.lower():
            return (
                False,
                None,
                "RLS_ERROR_42501: Row Level Security policy violated for table 'shops'. "
                "Please ensure you are logged in to an authorized account and migration '002_add_auth_and_rls.sql' is applied.",
            )
        return False, None, f"Database error creating shop: {err_msg}"


def assign_shop_owner(
    shop_id: str,
    new_owner_id: str,
    client: Optional[Any] = None,
    access_token: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """Assigns or updates a shop's owner_id to a designated shopkeeper (Admin feature).

    Returns:
        (success, error_message)
    """
    clean_sid = str(shop_id or "").strip()
    clean_oid = str(new_owner_id or "").strip()
    if not clean_sid:
        return False, "Shop ID is required."
    if not clean_oid:
        return False, "Shopkeeper User UUID is required."

    client = _resolve_client(client=client, access_token=access_token)
    if client is None:
        return False, "Supabase client is not connected or configured."

    try:
        response = (
            client.table("shops")
            .update({"owner_id": clean_oid})
            .eq("id", clean_sid)
            .execute()
        )
        if response.data and len(response.data) > 0:
            return True, None
        return False, f"Shop ID '{clean_sid}' not found or update returned no rows."
    except Exception as exc:
        err_msg = str(exc)
        if "42501" in err_msg or "row-level security" in err_msg.lower():
            return (
                False,
                "RLS_ERROR_42501: Insufficient privileges to assign this shop. "
                "Only authorized administrators can modify shop ownership.",
            )
        return False, f"Database error assigning shop: {err_msg}"


def provision_shop_with_owner(
    shop_data: Dict[str, Any],
    owner_email: str,
    owner_password: str,
    current_admin_user: Optional[Any],
    access_token: Optional[str] = None,
    service_role_key: Optional[str] = None,
    admin_client: Optional[Any] = None,
    db_client: Optional[Any] = None,
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Provisions a shopkeeper account and creates a linked shop record in one atomic operation.

    1. Validates input fields (shop name, email, password).
    2. Verifies admin authorization.
    3. Calls admin_create_shopkeeper_account to provision the user and retrieve their UUID.
    4. Calls create_shop with the new user's UUID as owner_id.
    5. In case of partial failure (account created but shop creation failed), returns the created
       User UUID along with a clear recovery path to prevent orphaned/unusable accounts.

    Returns:
        (success, shop_id, user_uuid, error_message)
    """
    from core.auth import admin_create_shopkeeper_account, is_admin_user

    if not is_admin_user(current_admin_user):
        return False, None, None, "Admin authorization required to provision shops."

    name = str(shop_data.get("name") or "").strip()
    if not name:
        return False, None, None, "Shop name is required."

    clean_email = (owner_email or "").strip().lower()
    if not clean_email or "@" not in clean_email:
        return False, None, None, "A valid shopkeeper email address is required."

    if not owner_password or len(owner_password) < 6:
        return False, None, None, "Password must be at least 6 characters long."

    # Step 1: Provision the user account
    user_metadata = {
        "name": str(shop_data.get("owner_name") or "").strip() or None,
        "phone": str(shop_data.get("phone") or "").strip() or None,
    }
    user_ok, user_uuid, user_err = admin_create_shopkeeper_account(
        email=clean_email,
        password=owner_password,
        user_metadata=user_metadata,
        current_admin_user=current_admin_user,
        admin_client=admin_client,
        service_role_key=service_role_key,
    )
    if not user_ok or not user_uuid:
        return False, None, None, user_err or "Failed to create shopkeeper account."

    # Step 2: Create the shop assigned to the new user's UUID
    shop_ok, shop_id, shop_err = create_shop(
        shop_data=shop_data,
        owner_id=user_uuid,
        access_token=access_token,
        client=db_client,
    )
    if not shop_ok or not shop_id:
        recovery_err = (
            f"Account created successfully for '{clean_email}', "
            f"but shop registration failed: {shop_err}. "
            f"You can link a shop to '{clean_email}' directly using the Reassign Shop tool below without recreating the account."
        )
        return False, None, user_uuid, recovery_err

    return True, shop_id, user_uuid, None


def admin_assign_shop_by_email(
    shop_id: str,
    shopkeeper_email: str,
    current_admin_user: Optional[Any],
    access_token: Optional[str] = None,
    service_role_key: Optional[str] = None,
    admin_client: Optional[Any] = None,
    db_client: Optional[Any] = None,
) -> Tuple[bool, Optional[str]]:
    """Assigns an existing shop to a shopkeeper by resolving their email to User UUID internally.

    Never requires the administrator to type, copy, or know a raw UUID.

    Returns:
        (success, error_message)
    """
    from core.auth import admin_get_user_by_email, is_admin_user

    if not is_admin_user(current_admin_user):
        return False, "Admin authorization required to assign shops."

    clean_sid = str(shop_id or "").strip()
    clean_email = str(shopkeeper_email or "").strip().lower()
    if not clean_sid:
        return False, "Shop is required."
    if not clean_email or "@" not in clean_email:
        return False, "A valid shopkeeper email address is required."

    user_info = admin_get_user_by_email(
        email=clean_email,
        admin_client=admin_client,
        service_role_key=service_role_key,
    )
    if not user_info or not user_info.get("id"):
        return (
            False,
            f"Shopkeeper account with email '{clean_email}' was not found. Please verify the email or create a new account above.",
        )

    target_uuid = user_info["id"]
    return assign_shop_owner(
        shop_id=clean_sid,
        new_owner_id=target_uuid,
        access_token=access_token,
        client=db_client,
    )





def check_batch_exists(
    shop_id: str,
    batch_id: str,
    client: Optional[Any] = None,
    access_token: Optional[str] = None,
) -> Tuple[bool, int]:
    """Checks whether a batch has already been persisted for the specified shop.

    Returns:
        (exists, count)
    """
    if not shop_id or not batch_id:
        return False, 0

    client = _resolve_client(client=client, access_token=access_token)
    if client is None:
        return False, 0

    try:
        response = (
            client.table("ledger_entries")
            .select("id")
            .eq("shop_id", shop_id)
            .eq("batch_id", batch_id)
            .execute()
        )
        count = len(response.data) if response.data else 0
        return (count > 0, count)
    except Exception:
        return False, 0


def fetch_confirmed_ledger_entries(
    shop_id: str,
    client: Optional[Any] = None,
    access_token: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Retrieves all confirmed ledger transactions for a specified shop from Supabase PostgreSQL.

    Security & Isolation:
    - If access_token is provided, queries through the user-authenticated Supabase client so
      Row Level Security (RLS) automatically enforces multi-tenant shopkeeper isolation.
    - Filters strictly on shop_id and status='confirmed'.
    - Returns records ordered by entry_date DESC.
    - Gracefully returns empty list if unconfigured, offline, or on query exception.
    """
    clean_sid = str(shop_id or "").strip()
    if not clean_sid:
        return []

    resolved_client = _resolve_client(client=client, access_token=access_token)
    if resolved_client is None:
        return []

    try:
        response = (
            resolved_client.table("ledger_entries")
            .select(
                "id, shop_id, customer_name, customer_phone, item_name, "
                "standardized_item_name, quantity, unit, unit_price, total_amount, "
                "paid_amount, due_amount, batch_id, row_index, transaction_type, "
                "status, confidence_score, raw_ocr_text, entry_date, created_at"
            )
            .eq("shop_id", clean_sid)
            .eq("status", "confirmed")
            .order("entry_date", desc=True)
            .execute()
        )
        return response.data or []
    except Exception:
        return []


def save_confirmed_entries(
    entries: List[Any],
    shop_id: str,
    batch_id: str,
    allow_duplicate: bool = False,
    client: Optional[Any] = None,
    access_token: Optional[str] = None,
) -> Tuple[bool, str, int]:
    """Persists confirmed ledger transactions into Supabase PostgreSQL.

    Guarantees:
    1. Rejects unconfirmed rows.
    2. Preserves missing/blank optional fields (customer_name, paid_amount, due_amount) as SQL NULL.
    3. Enforces database idempotency using shop_id + batch_id + row_index unless explicitly overridden.
    4. Detects unapplied migrations and returns actionable instructions.

    Returns:
        (success, message, inserted_count)
    """
    if not shop_id or not str(shop_id).strip():
        return False, "Missing active shop selection. Please select or register a shop first.", 0

    from core.models import EntryStatus

    # Safeguard 1: Filter and save strictly confirmed entries
    confirmed_entries = []
    for e in entries:
        st = getattr(e, "status", None)
        if st == EntryStatus.CONFIRMED or st == "confirmed":
            confirmed_entries.append(e)

    if not confirmed_entries:
        return False, "No confirmed ledger entries to save. Please review and confirm rows first.", 0

    if client is None:
        client = _resolve_client(client=client, access_token=access_token)
    if client is None:
        return False, "Database client is not connected or configured in .env.", 0

    # Safeguard 2: Verify shop exists before attempting insert
    if not verify_shop_exists(shop_id, client=client, access_token=access_token):
        return (
            False,
            f"Shop ID '{shop_id}' was not found in the database. Please select or register a valid shop.",
            0,
        )

    # Safeguard 3: Batch idempotency check
    if not allow_duplicate and batch_id:
        is_dup, existing_count = check_batch_exists(
            shop_id, batch_id, client=client, access_token=access_token
        )
        if is_dup:
            return (
                False,
                f"DUPLICATE_BATCH: Processed batch '{batch_id}' ({existing_count} records) already exists in database.",
                existing_count,
            )

    # Safeguard 4: Build payload ensuring strict NULL mapping for optional values
    records: List[Dict[str, Any]] = []
    for idx, entry in enumerate(confirmed_entries, start=1):
        cust = getattr(entry, "customer_name", None)
        if cust is None:
            cust_str = None
        else:
            cust_str = str(cust).strip()
            if not cust_str or cust_str.lower() in ("none", "null", "nan"):
                cust_str = None

        phone = getattr(entry, "customer_phone", None)
        if phone is None:
            phone_str = None
        else:
            phone_str = str(phone).strip()
            if not phone_str or phone_str.lower() in ("none", "null", "nan"):
                phone_str = None

        paid = getattr(entry, "paid_amount", None)
        due = getattr(entry, "due_amount", None)

        tx_type = getattr(entry, "transaction_type", "credit")
        if hasattr(tx_type, "value"):
            tx_type = tx_type.value

        rec = {
            "shop_id": shop_id,
            "customer_name": cust_str,
            "customer_phone": phone_str,
            "item_name": entry.item_name,
            "standardized_item_name": getattr(entry, "standardized_item_name", None),
            "quantity": float(entry.quantity) if getattr(entry, "quantity", None) is not None else 1.0,
            "unit": getattr(entry, "unit", "unit") or "unit",
            "unit_price": float(entry.unit_price) if getattr(entry, "unit_price", None) is not None else 0.0,
            "total_amount": float(entry.total_amount) if getattr(entry, "total_amount", None) is not None else 0.0,
            "paid_amount": float(paid) if paid is not None else None,
            "due_amount": float(due) if due is not None else None,
            "batch_id": batch_id,
            "row_index": getattr(entry, "row_index", idx) or idx,
            "transaction_type": str(tx_type),
            "status": "confirmed",
            "confidence_score": (
                float(entry.confidence_score)
                if getattr(entry, "confidence_score", None) is not None
                else None
            ),
            "raw_ocr_text": getattr(entry, "raw_ocr_text", None) or None,
            "entry_date": getattr(entry, "entry_date", None) or None,
        }
        records.append(rec)

    # Perform persistence with defensive error handling
    try:
        response = client.table("ledger_entries").insert(records).execute()
        saved_count = len(response.data) if response.data else len(records)
        return (
            True,
            f"Successfully saved {saved_count} confirmed transactions to Supabase.",
            saved_count,
        )
    except Exception as exc:
        err_msg = str(exc)
        if "42501" in err_msg or "row-level security" in err_msg.lower():
            return (
                False,
                "RLS_ERROR_42501: Row Level Security policy violated for table 'ledger_entries'. "
                "Please verify that you are logged in and authorized for this shop, and that migration '002_add_auth_and_rls.sql' is applied.",
                0,
            )
        # Check if database is missing migration columns
        if (
            "paid_amount" in err_msg
            or "due_amount" in err_msg
            or "batch_id" in err_msg
            or ("column" in err_msg.lower() and "does not exist" in err_msg.lower())
        ):
            return (
                False,
                "MIGRATION_REQUIRED: Supabase database is missing required columns (paid_amount, due_amount, or batch_id). "
                "Please review and run 'sql/migrations/001_add_paid_due_and_idempotency.sql' in the Supabase SQL editor.",
                0,
            )
        if (
            "unique" in err_msg.lower()
            or "duplicate" in err_msg.lower()
            or "idx_ledger_entries_shop_batch_row" in err_msg
        ):
            return (
                False,
                f"DUPLICATE_BATCH: Database unique index prevented duplicate save for batch '{batch_id}'.",
                0,
            )
        return False, f"Database error while saving confirmed records: {err_msg}", 0


_GLOBAL_DB: Optional[SupabaseConnectionManager] = None


def get_db_manager() -> SupabaseConnectionManager:
    """Returns singleton SupabaseConnectionManager instance."""
    global _GLOBAL_DB
    if _GLOBAL_DB is None:
        _GLOBAL_DB = SupabaseConnectionManager()
    return _GLOBAL_DB
