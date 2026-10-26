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

        return row

    def validate_batch(self, rows: List[ExtractedLedgerRow]) -> List[ExtractedLedgerRow]:
        """Validates all rows in a batch."""
        return [self.validate_row(r) for r in rows]


_GLOBAL_VALIDATOR: Optional[LedgerValidator] = None


def get_validator() -> LedgerValidator:
    """Returns singleton LedgerValidator instance."""
    global _GLOBAL_VALIDATOR
    if _GLOBAL_VALIDATOR is None:
        _GLOBAL_VALIDATOR = LedgerValidator()
    return _GLOBAL_VALIDATOR
