"""Decimal-safe data validation and integrity checking for extracted ledger records.

Validates quantities, monetary arithmetic (paid + due == amount), date sanity,
and assigns review-required flags for human verification.
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Tuple

from core.models import ExtractedLedgerRow

# Validation Flag Constants
FLAG_MATH_MISMATCH = "MATH_MISMATCH"
FLAG_INFERRED_YEAR = "INFERRED_YEAR"
FLAG_AMBIGUOUS_DATE = "AMBIGUOUS_DATE"
FLAG_INVALID_DATE = "INVALID_DATE"
FLAG_NEGATIVE_VALUE = "NEGATIVE_VALUE"
FLAG_INVALID_QUANTITY = "INVALID_QUANTITY"
FLAG_LOW_CONFIDENCE = "LOW_CONFIDENCE"
FLAG_CANDIDATE_READINGS = "CANDIDATE_READINGS"
FLAG_MISSING_ITEM = "MISSING_ITEM"
FLAG_MISSING_AMOUNT = "MISSING_AMOUNT"

# Interactive Payment Status Constants
STATUS_FULLY_PAID = "✅ Fully Paid"
STATUS_HAS_DUE = "❌ Has Due"


def to_decimal(val: Any) -> Optional[Decimal]:
    """Safely converts numerical or string input to Decimal, avoiding float precision traps."""
    if val is None:
        return None
    if isinstance(val, (int, str)):
        try:
            cleaned = str(val).strip().replace(",", "")
            if not cleaned or cleaned.lower() in ("none", "null", "nan"):
                return None
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    if isinstance(val, float):
        try:
            return Decimal(str(val))
        except InvalidOperation:
            return None
    if isinstance(val, Decimal):
        return val
    return None


def parse_and_validate_date(date_str: Optional[str]) -> Tuple[Optional[str], List[str], List[str]]:
    """Validates date string, detects ambiguous formats, and flags inferred years.

    Returns:
        (canonical_date_str, flags, reasons)
    """
    flags: List[str] = []
    reasons: List[str] = []

    if not date_str or not date_str.strip():
        return None, flags, reasons

    raw = date_str.strip()
    current_year = datetime.now().year

    # Case 1: Standard ISO format YYYY-MM-DD
    iso_match = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", raw)
    if iso_match:
        y, m, d = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        try:
            dt = datetime(y, m, d)
            return dt.strftime("%Y-%m-%d"), flags, reasons
        except ValueError:
            flags.append(FLAG_INVALID_DATE)
            reasons.append(f"Invalid date value in ISO format: '{raw}'")
            return None, flags, reasons

    # Case 2: Full date DD/MM/YYYY or DD-MM-YYYY
    full_match = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})$", raw)
    if full_match:
        part1, part2, y = int(full_match.group(1)), int(full_match.group(2)), int(full_match.group(3))
        # Check for day-month ambiguity (e.g. 05/06/2026)
        if part1 <= 12 and part2 <= 12 and part1 != part2:
            flags.append(FLAG_AMBIGUOUS_DATE)
            reasons.append(f"Ambiguous day/month ordering in '{raw}' (interpreted as DD/MM/YYYY)")

        # In Indian retail ledgers, DD/MM/YYYY is the overwhelming standard
        d, m = part1, part2
        try:
            dt = datetime(y, m, d)
            return dt.strftime("%Y-%m-%d"), flags, reasons
        except ValueError:
            # Fallback test for MM/DD/YYYY if DD > 12 was in part2
            try:
                dt = datetime(y, part1, part2)
                return dt.strftime("%Y-%m-%d"), flags, reasons
            except ValueError:
                flags.append(FLAG_INVALID_DATE)
                reasons.append(f"Invalid calendar date: '{raw}'")
                return None, flags, reasons

    # Case 3: Partial date without year DD/MM or DD-MM (very common in daily shop ledgers)
    partial_match = re.match(r"^(\d{1,2})[-/.](\d{1,2})$", raw)
    if partial_match:
        d, m = int(partial_match.group(1)), int(partial_match.group(2))
        try:
            dt = datetime(current_year, m, d)
            canonical = dt.strftime("%Y-%m-%d")
            flags.append(FLAG_INFERRED_YEAR)
            reasons.append(f"Year missing in ledger ('{raw}'); inferred current year {current_year}")
            return canonical, flags, reasons
        except ValueError:
            flags.append(FLAG_INVALID_DATE)
            reasons.append(f"Invalid day/month pair in '{raw}'")
            return None, flags, reasons

    # Case 4: Unparseable date
    flags.append(FLAG_INVALID_DATE)
    reasons.append(f"Could not parse date '{raw}' into a recognized format")
    return None, flags, reasons


class LedgerValidator:
    """Performs financial integrity and field confidence validations."""

    def __init__(self, min_confidence_threshold: float = 0.70) -> None:
        self.min_confidence_threshold = min_confidence_threshold

    def validate_row(self, row: ExtractedLedgerRow) -> ExtractedLedgerRow:
        """Validates a single extracted ledger row and populates review flags."""
        flags: List[str] = list(row.validation_flags)
        reasons: List[str] = list(row.review_reasons)

        # 1. Mandatory Item Check
        if not row.item or not row.item.strip():
            flags.append(FLAG_MISSING_ITEM)
            reasons.append("Item name was not detected in this line.")

        # 2. Date Validation & Canonicalization
        if row.date:
            canonical_date, date_flags, date_reasons = parse_and_validate_date(row.date)
            for f in date_flags:
                if f not in flags:
                    flags.append(f)
            reasons.extend(date_reasons)
            if canonical_date:
                row.date = canonical_date

        # 3. Quantity Validation
        if row.quantity is not None:
            q_dec = to_decimal(row.quantity)
            if q_dec is not None and q_dec <= 0:
                flags.append(FLAG_INVALID_QUANTITY)
                reasons.append(f"Quantity must be greater than zero (found {row.quantity}).")

        # 4. Monetary Checks (Decimal-safe)
        amount_dec = to_decimal(row.amount)
        paid_dec = to_decimal(row.paid)
        due_dec = to_decimal(row.due)

        if amount_dec is None:
            flags.append(FLAG_MISSING_AMOUNT)
            reasons.append("Total transaction amount is missing or invalid.")
        else:
            if amount_dec < 0:
                flags.append(FLAG_NEGATIVE_VALUE)
                reasons.append(f"Total amount cannot be negative (₹{amount_dec}).")

            if paid_dec is not None and paid_dec < 0:
                flags.append(FLAG_NEGATIVE_VALUE)
                reasons.append(f"Paid amount cannot be negative (₹{paid_dec}).")

            if due_dec is not None and due_dec < 0:
                flags.append(FLAG_NEGATIVE_VALUE)
                reasons.append(f"Due amount cannot be negative (₹{due_dec}).")

            # 5. Core Kirana Arithmetic: Paid + Due must equal Amount
            if paid_dec is not None and due_dec is not None:
                expected_sum = (paid_dec + due_dec).quantize(Decimal("0.01"))
                actual_amount = amount_dec.quantize(Decimal("0.01"))
                discrepancy = abs(expected_sum - actual_amount)
                if discrepancy > Decimal("0.01"):
                    flags.append(FLAG_MATH_MISMATCH)
                    reasons.append(
                        f"Financial discrepancy: Paid (₹{paid_dec}) + Due (₹{due_dec}) = "
                        f"₹{expected_sum}, which does not match Total Amount (₹{actual_amount})."
                    )

        # 6. Field Confidence Check
        if row.field_confidence:
            for field_name, score in row.field_confidence.items():
                if score < self.min_confidence_threshold:
                    flags.append(FLAG_LOW_CONFIDENCE)
                    reasons.append(
                        f"Low AI confidence on '{field_name}' ({score:.0%}). Needs human verification."
                    )
                    break

        # 7. Candidate Readings Check
        if row.candidate_readings:
            for field_name, candidates in row.candidate_readings.items():
                if candidates and len(candidates) > 1:
                    flags.append(FLAG_CANDIDATE_READINGS)
                    cand_str = ", ".join(f"'{c}'" for c in candidates[:3])
                    reasons.append(
                        f"Multiple candidate readings for '{field_name}': {cand_str}."
                    )
                    break

        # Deduplicate flags and update row status
        unique_flags = list(dict.fromkeys(flags))
        row.validation_flags = unique_flags
        row.review_reasons = reasons
        row.needs_review = len(unique_flags) > 0

        if getattr(row, "payment_status", None) is None:
            if row.due is not None and row.due > 0:
                row.payment_status = STATUS_HAS_DUE
            else:
                row.payment_status = STATUS_FULLY_PAID

        # Capture snapshot of validated fields for diffing during subsequent user edits
        if row.original_data is None:
            row.original_data = {
                "date": row.date,
                "customer": row.customer,
                "customer_phone": row.customer_phone,
                "item": row.item,
                "quantity": row.quantity,
                "unit": row.unit,
                "unit_price": row.unit_price,
                "amount": row.amount,
                "paid": row.paid,
                "due": row.due,
            }

        return row

    def validate_batch(self, rows: List[ExtractedLedgerRow]) -> List[ExtractedLedgerRow]:
        """Validates all rows in a batch."""
        return [self.validate_row(r) for r in rows]

    def revalidate_row(self, row: ExtractedLedgerRow) -> ExtractedLedgerRow:
        """Revalidates an edited row while preserving unrelated uncertainty flags.

        Compares current fields against row.original_data. Only flags associated
        with fields that the user modified are cleared or re-evaluated.
        """
        orig = row.original_data or {}

        # Build existing flag/reason mappings
        flags: List[str] = list(row.validation_flags)
        reasons: List[str] = list(row.review_reasons)

        # 1. Null/Blank Sanitization for Optional Fields
        if row.customer is not None:
            c_str = str(row.customer).strip()
            if not c_str or c_str.lower() in ("none", "null", "nan"):
                row.customer = None
            else:
                row.customer = c_str

        if row.customer_phone is not None:
            p_str = str(row.customer_phone).strip()
            if not p_str or p_str.lower() in ("none", "null", "nan"):
                row.customer_phone = None
            else:
                row.customer_phone = p_str

        if row.paid is not None:
            p_dec = to_decimal(row.paid)
            row.paid = float(p_dec) if p_dec is not None else None

        if row.due is not None:
            d_dec = to_decimal(row.due)
            row.due = float(d_dec) if d_dec is not None else None

        # 2. Date Field-Aware Revalidation
        date_changed = (row.date != orig.get("date")) or (not orig and row.date)
        if date_changed:
            date_flag_types = {FLAG_INFERRED_YEAR, FLAG_AMBIGUOUS_DATE, FLAG_INVALID_DATE}
            flags = [f for f in flags if f not in date_flag_types]
            reasons = [
                r for r in reasons
                if not any(kw in r for kw in ("date", "Date", "Year missing", "calendar date"))
            ]

            if row.date:
                canonical_date, date_flags, date_reasons = parse_and_validate_date(row.date)
                for f in date_flags:
                    if f not in flags:
                        flags.append(f)
                reasons.extend(date_reasons)
                if canonical_date:
                    row.date = canonical_date

        # 3. Item Field-Aware Revalidation
        item_changed = (row.item != orig.get("item")) or (not orig and row.item)
        if item_changed:
            flags = [
                f for f in flags
                if f not in (FLAG_MISSING_ITEM, FLAG_LOW_CONFIDENCE, FLAG_CANDIDATE_READINGS)
            ]
            reasons = [
                r for r in reasons
                if not any(kw in r for kw in ("Item name", "confidence on 'item'", "candidate readings for 'item'"))
            ]
            if not row.item or not str(row.item).strip():
                flags.append(FLAG_MISSING_ITEM)
                reasons.append("Item name was not detected in this line.")
            else:
                try:
                    from core.catalog import get_catalog_manager
                    match = get_catalog_manager().find_match(row.item, threshold=75.0)
                    if match:
                        row.standardized_item = match[0].name_en
                except Exception:
                    pass

        # 4. Quantity Field-Aware Revalidation
        qty_changed = (row.quantity != orig.get("quantity")) or (not orig and row.quantity is not None)
        if qty_changed:
            flags = [f for f in flags if f != FLAG_INVALID_QUANTITY]
            reasons = [r for r in reasons if "Quantity must be greater" not in r]
            if row.quantity is not None:
                q_dec = to_decimal(row.quantity)
                if q_dec is not None and q_dec <= 0:
                    flags.append(FLAG_INVALID_QUANTITY)
                    reasons.append(f"Quantity must be greater than zero (found {row.quantity}).")

        # 5. Financial & Math Discrepancy Field-Aware Revalidation
        money_changed = (
            (row.amount != orig.get("amount"))
            or (row.paid != orig.get("paid"))
            or (row.due != orig.get("due"))
            or not orig
        )
        if money_changed:
            flags = [
                f for f in flags
                if f not in (FLAG_MISSING_AMOUNT, FLAG_NEGATIVE_VALUE, FLAG_MATH_MISMATCH)
            ]
            reasons = [
                r for r in reasons
                if not any(kw in r for kw in ("Total transaction amount", "cannot be negative", "Financial discrepancy"))
            ]

            amount_dec = to_decimal(row.amount)
            paid_dec = to_decimal(row.paid)
            due_dec = to_decimal(row.due)

            if amount_dec is None:
                flags.append(FLAG_MISSING_AMOUNT)
                reasons.append("Total transaction amount is missing or invalid.")
            else:
                if amount_dec < 0:
                    flags.append(FLAG_NEGATIVE_VALUE)
                    reasons.append(f"Total amount cannot be negative (₹{amount_dec}).")
                if paid_dec is not None and paid_dec < 0:
                    flags.append(FLAG_NEGATIVE_VALUE)
                    reasons.append(f"Paid amount cannot be negative (₹{paid_dec}).")
                if due_dec is not None and due_dec < 0:
                    flags.append(FLAG_NEGATIVE_VALUE)
                    reasons.append(f"Due amount cannot be negative (₹{due_dec}).")

                # Core Kirana arithmetic: paid + due == amount
                if paid_dec is not None and due_dec is not None:
                    expected_sum = (paid_dec + due_dec).quantize(Decimal("0.01"))
                    actual_amount = amount_dec.quantize(Decimal("0.01"))
                    if abs(expected_sum - actual_amount) > Decimal("0.01"):
                        flags.append(FLAG_MATH_MISMATCH)
                        reasons.append(
                            f"Financial discrepancy: Paid (₹{paid_dec}) + Due (₹{due_dec}) = "
                            f"₹{expected_sum}, which does not match Total Amount (₹{actual_amount})."
                        )

        # Deduplicate and finalize
        unique_flags = list(dict.fromkeys(flags))
        row.validation_flags = unique_flags
        row.review_reasons = list(dict.fromkeys(reasons))
        row.needs_review = len(unique_flags) > 0

        if getattr(row, "payment_status", None) is None:
            if row.due is not None and row.due > 0:
                row.payment_status = STATUS_HAS_DUE
            else:
                row.payment_status = STATUS_FULLY_PAID

        return row

    def revalidate_batch(self, rows: List[ExtractedLedgerRow]) -> List[ExtractedLedgerRow]:
        """Revalidates all rows in an edited batch."""
        return [self.revalidate_row(r) for r in rows]


_GLOBAL_VALIDATOR: Optional[LedgerValidator] = None


def get_validator() -> LedgerValidator:
    """Returns singleton LedgerValidator instance."""
    global _GLOBAL_VALIDATOR
    if _GLOBAL_VALIDATOR is None:
        _GLOBAL_VALIDATOR = LedgerValidator()
    return _GLOBAL_VALIDATOR


def revalidate_row(row: ExtractedLedgerRow) -> ExtractedLedgerRow:
    """Convenience function for field-aware row revalidation."""
    return get_validator().revalidate_row(row)


def calculate_payment_split(
    total_amount: Optional[Any],
    payment_status: Optional[str] = None,
    due_amount: Optional[Any] = None,
) -> Tuple[str, float, float]:
    """Calculates valid (payment_status, paid, due) adhering to Kirana financial rules.

    Guarantees:
    - Total amount is non-negative (defaulting to 0.00).
    - 0.0 <= Due <= Total.
    - Paid = Total - Due (Paid is never negative).
    - If payment_status is '✅ Fully Paid' (or indicates fully paid):
        Due = 0.0, Paid = Total, status = '✅ Fully Paid'.
    - If payment_status is '❌ Has Due' (or indicates due):
        - If due_amount is provided: Due = clamp(due_amount, 0.0, Total).
        - If due_amount is None: Due = Total.
        - If Due == 0.0: treated as fully paid -> status = '✅ Fully Paid', Paid = Total, Due = 0.0.
        - Else: status = '❌ Has Due', Paid = Total - Due.
    - If payment_status is not specified (None or blank):
        - If due_amount is provided and > 0:
            Due = clamp(due_amount, 0.0, Total)
            if Due == Decimal("0.00"):
                status = STATUS_FULLY_PAID
                paid_dec = total_dec
            else:
                status = STATUS_HAS_DUE
                paid_dec = (total_dec - due_dec).quantize(Decimal("0.01"))
        - Else:
            status = STATUS_FULLY_PAID
            due_dec = Decimal("0.00")
            paid_dec = total_dec

    Returns:
        (payment_status, paid_amount, due_amount)
    """
    total_dec = to_decimal(total_amount)
    if total_dec is None or total_dec < Decimal("0.00"):
        total_dec = Decimal("0.00")
    else:
        total_dec = total_dec.quantize(Decimal("0.01"))

    norm_status = str(payment_status or "").strip()
    is_fully_paid = ("✅" in norm_status) or ("fully paid" in norm_status.lower())
    is_has_due = ("❌" in norm_status) or ("has due" in norm_status.lower()) or (norm_status.lower() == "due")

    if is_fully_paid:
        due_dec = Decimal("0.00")
        paid_dec = total_dec
        final_status = STATUS_FULLY_PAID
    elif is_has_due:
        raw_due_dec = to_decimal(due_amount)
        if raw_due_dec is None:
            # Default to full bill on credit if user selected Has Due without specifying amount
            due_dec = total_dec
        else:
            # Enforce 0.0 <= due <= total
            due_dec = max(Decimal("0.00"), min(raw_due_dec, total_dec)).quantize(Decimal("0.01"))

        if due_dec == Decimal("0.00"):
            # Rule: If Due = 0, treat as fully paid
            paid_dec = total_dec
            final_status = STATUS_FULLY_PAID
        else:
            paid_dec = (total_dec - due_dec).quantize(Decimal("0.01"))
            final_status = STATUS_HAS_DUE
    else:
        # Status not explicitly specified; infer from due_amount
        raw_due_dec = to_decimal(due_amount)
        if raw_due_dec is not None and raw_due_dec > Decimal("0.00"):
            due_dec = max(Decimal("0.00"), min(raw_due_dec, total_dec)).quantize(Decimal("0.01"))
            if due_dec == Decimal("0.00"):
                paid_dec = total_dec
                final_status = STATUS_FULLY_PAID
            else:
                paid_dec = (total_dec - due_dec).quantize(Decimal("0.01"))
                final_status = STATUS_HAS_DUE
        else:
            due_dec = Decimal("0.00")
            paid_dec = total_dec
            final_status = STATUS_FULLY_PAID

    return final_status, float(paid_dec), float(due_dec)


def update_row_payment(
    row: ExtractedLedgerRow,
    is_fully_paid: Optional[bool] = None,
    payment_status: Optional[str] = None,
    due_amount: Optional[Any] = None,
) -> ExtractedLedgerRow:
    """Updates the payment status and amounts for a row and revalidates financial integrity."""
    status_input = payment_status
    if is_fully_paid is True:
        status_input = STATUS_FULLY_PAID
    elif is_fully_paid is False:
        status_input = STATUS_HAS_DUE

    final_status, paid, due = calculate_payment_split(
        total_amount=row.amount,
        payment_status=status_input,
        due_amount=due_amount if due_amount is not None else row.due,
    )
    row.payment_status = final_status
    row.paid = paid
    row.due = due
    return revalidate_row(row)


def sync_editor_rows_with_changes(
    rows: List[ExtractedLedgerRow],
    editor_diff: Optional[Dict[str, Any]] = None,
) -> List[ExtractedLedgerRow]:
    """Synchronizes a list of ExtractedLedgerRow objects with pending st.data_editor changes.

    Ensures every edited row immediately and independently recalculates Paid = Total - Due,
    preserving user-entered Due amounts (0 <= Due <= Total) and handling status transitions cleanly.
    """
    if not rows:
        return []

    updated_rows = [r for r in rows]

    if not editor_diff or not isinstance(editor_diff, dict):
        for r in updated_rows:
            amt = float(r.amount or 0.0)
            st_lbl, pd_val, due_val = calculate_payment_split(
                total_amount=amt,
                payment_status=getattr(r, "payment_status", None),
                due_amount=r.due,
            )
            r.payment_status = st_lbl
            r.paid = pd_val
            r.due = due_val
        return updated_rows

    # 1. Handle deleted rows
    deleted_indices = editor_diff.get("deleted_rows")
    if deleted_indices and isinstance(deleted_indices, list):
        del_set = set(int(i) for i in deleted_indices if str(i).isdigit() or isinstance(i, int))
        updated_rows = [r for idx, r in enumerate(updated_rows) if idx not in del_set]

    # 2. Handle edited rows
    edited_rows_map = editor_diff.get("edited_rows")
    if edited_rows_map and isinstance(edited_rows_map, dict):
        for row_idx_str, changes in edited_rows_map.items():
            try:
                idx = int(row_idx_str)
            except (ValueError, TypeError):
                continue
            if 0 <= idx < len(updated_rows):
                r = updated_rows[idx]
                if not isinstance(changes, dict):
                    continue

                if "item" in changes:
                    r.item = str(changes["item"] or "").strip()
                if "date" in changes:
                    d_val = str(changes["date"] or "").strip()
                    r.date = None if not d_val or d_val.lower() in ("none", "null", "nan") else d_val
                if "customer" in changes:
                    c_val = str(changes["customer"] or "").strip()
                    r.customer = None if not c_val or c_val.lower() in ("none", "null", "nan") else c_val
                if "customer_phone" in changes:
                    cp_val = str(changes["customer_phone"] or "").strip()
                    r.customer_phone = None if not cp_val or cp_val.lower() in ("none", "null", "nan") else cp_val
                if "quantity" in changes:
                    try:
                        r.quantity = float(changes["quantity"]) if changes["quantity"] is not None else None
                    except (ValueError, TypeError):
                        pass
                if "unit" in changes:
                    r.unit = str(changes["unit"] or "").strip() or "unit"
                if "unit_price" in changes:
                    try:
                        r.unit_price = float(changes["unit_price"]) if changes["unit_price"] is not None else None
                    except (ValueError, TypeError):
                        pass
                if "amount" in changes:
                    try:
                        r.amount = float(changes["amount"]) if changes["amount"] is not None else 0.0
                    except (ValueError, TypeError):
                        pass
                if "status" in changes:
                    r.needs_review = (str(changes["status"]).strip() != "confirmed")

                amt_val = float(r.amount or 0.0)
                prev_status = getattr(r, "payment_status", None) or STATUS_FULLY_PAID
                prev_due = r.due

                has_payment_edit = "payment" in changes
                has_due_edit = "due" in changes

                user_payment = str(changes["payment"]).strip() if has_payment_edit else prev_status
                raw_user_due = changes["due"] if has_due_edit else prev_due

                if "fully paid" in user_payment.lower() or "✅" in user_payment:
                    status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_FULLY_PAID, 0.0)
                elif "has due" in user_payment.lower() or "❌" in user_payment:
                    if has_due_edit:
                        try:
                            due_clean = float(raw_user_due) if raw_user_due is not None else 0.0
                        except (ValueError, TypeError):
                            due_clean = 0.0
                        due_clean = max(0.0, min(due_clean, amt_val))
                        status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_HAS_DUE, due_clean)
                    elif has_payment_edit:
                        due_input = prev_due if (prev_due is not None and prev_due > 0) else amt_val
                        status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_HAS_DUE, due_input)
                    else:
                        try:
                            due_clean = float(raw_user_due) if raw_user_due is not None else amt_val
                        except (ValueError, TypeError):
                            due_clean = amt_val
                        due_clean = max(0.0, min(due_clean, amt_val))
                        status_label, calc_paid, calc_due = calculate_payment_split(amt_val, STATUS_HAS_DUE, due_clean)
                else:
                    status_label, calc_paid, calc_due = calculate_payment_split(amt_val, user_payment, raw_user_due)

                r.payment_status = status_label
                r.paid = calc_paid
                r.due = calc_due
                revalidate_row(r)

    # 3. Handle added rows
    added_rows_list = editor_diff.get("added_rows")
    if added_rows_list and isinstance(added_rows_list, list):
        max_id = max((r.row_id for r in updated_rows), default=0)
        for i, new_dict in enumerate(added_rows_list):
            if not isinstance(new_dict, dict):
                continue
            amt_val = float(new_dict.get("amount") or 0.0)
            pay_status = str(new_dict.get("payment") or STATUS_FULLY_PAID)
            due_val = new_dict.get("due")
            st_lbl, calc_pd, calc_d = calculate_payment_split(amt_val, pay_status, due_val)
            new_row = ExtractedLedgerRow(
                row_id=max_id + i + 1,
                date=new_dict.get("date"),
                customer=new_dict.get("customer"),
                customer_phone=new_dict.get("customer_phone"),
                item=str(new_dict.get("item") or "").strip(),
                quantity=float(new_dict.get("quantity")) if new_dict.get("quantity") is not None else 1.0,
                unit=new_dict.get("unit") or "unit",
                unit_price=float(new_dict.get("unit_price")) if new_dict.get("unit_price") is not None else 0.0,
                amount=amt_val,
                payment_status=st_lbl,
                paid=calc_pd,
                due=calc_d,
            )
            revalidate_row(new_row)
            updated_rows.append(new_row)

    return updated_rows
