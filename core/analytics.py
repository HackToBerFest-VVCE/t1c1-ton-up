"""Business analytics and intelligence calculation engine for Kirana Ledger Digitizer.

Provides pure, decimal-safe financial aggregations, product performance metrics,
customer credit balances, and multilingual business insights from confirmed
ledger entries.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple, Union

from core.i18n import t
from core.validator import to_decimal


def _safe_decimal(val: Any, default: str = "0.00") -> Decimal:
    """Helper to convert numerical or string input to Decimal, defaulting safely."""
    d = to_decimal(val)
    if d is not None:
        return d
    return Decimal(default)


def _get_field(entry: Any, *field_names: str) -> Any:
    """Helper to extract a field from either a dict or an object using alternative names."""
    for name in field_names:
        if isinstance(entry, dict):
            if name in entry and entry[name] is not None:
                return entry[name]
        else:
            val = getattr(entry, name, None)
            if val is not None:
                return val
    return None


def calculate_sales_summary(
    entries: List[Union[Dict[str, Any], Any]],
) -> Dict[str, Any]:
    """Calculates sales totals, collections, outstanding dues, and transaction volume.

    All financial totals use decimal-safe arithmetic to avoid floating point precision loss.

    Returns:
        {
            "total_sales": float,
            "total_collected": float,
            "total_due": float,
            "cash_sales": float,
            "credit_sales": float,
            "transaction_count": int,
            "average_order_value": float,
        }
    """
    if not entries:
        return {
            "total_sales": 0.0,
            "total_collected": 0.0,
            "total_due": 0.0,
            "cash_sales": 0.0,
            "credit_sales": 0.0,
            "transaction_count": 0,
            "average_order_value": 0.0,
        }

    total_sales_dec = Decimal("0.00")
    total_paid_dec = Decimal("0.00")
    total_due_dec = Decimal("0.00")
    cash_sales_dec = Decimal("0.00")
    credit_sales_dec = Decimal("0.00")
    valid_count = 0

    for entry in entries:
        amt = _safe_decimal(_get_field(entry, "total_amount", "amount"))
        paid_raw = _get_field(entry, "paid_amount", "paid")
        due_raw = _get_field(entry, "due_amount", "due")
        tx_type = str(_get_field(entry, "transaction_type") or "credit").lower()

        # Handle explicit or missing paid/due values defensively
        paid_dec = to_decimal(paid_raw)
        due_dec = to_decimal(due_raw)

        if paid_dec is not None and due_dec is not None:
            paid = paid_dec
            due = due_dec
        elif paid_dec is not None and due_dec is None:
            paid = paid_dec
            due = max(Decimal("0.00"), amt - paid) if tx_type == "credit" else Decimal("0.00")
        elif due_dec is not None and paid_dec is None:
            due = due_dec
            paid = max(Decimal("0.00"), amt - due)
        else:
            # Both paid and due are None/missing
            if tx_type == "cash":
                paid = amt
                due = Decimal("0.00")
            elif tx_type == "credit":
                paid = Decimal("0.00")
                due = amt
            else:
                paid = Decimal("0.00")
                due = Decimal("0.00")

        total_sales_dec += amt
        total_paid_dec += paid
        total_due_dec += due
        valid_count += 1

        if due > Decimal("0.00") or tx_type == "credit":
            credit_sales_dec += amt
        else:
            cash_sales_dec += amt

    avg_order = (
        (total_sales_dec / valid_count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if valid_count > 0
        else Decimal("0.00")
    )

    return {
        "total_sales": float(total_sales_dec.quantize(Decimal("0.01"))),
        "total_collected": float(total_paid_dec.quantize(Decimal("0.01"))),
        "total_due": float(total_due_dec.quantize(Decimal("0.01"))),
        "cash_sales": float(cash_sales_dec.quantize(Decimal("0.01"))),
        "credit_sales": float(credit_sales_dec.quantize(Decimal("0.01"))),
        "transaction_count": valid_count,
        "average_order_value": float(avg_order),
    }


def calculate_top_products(
    entries: List[Union[Dict[str, Any], Any]],
    top_n: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """Calculates top-selling products ranked by revenue and quantity sold.

    Groups entries by canonical standardized item name when present, falling
    back to verbatim item name. Returns top N items for both metrics.

    Returns:
        {
            "by_revenue": List[Dict[str, Any]],
            "by_quantity": List[Dict[str, Any]],
        }
    """
    if not entries or top_n <= 0:
        return {"by_revenue": [], "by_quantity": []}

    grouped: Dict[str, Dict[str, Any]] = {}

    for entry in entries:
        std_name = _get_field(entry, "standardized_item_name", "standardized_item")
        raw_name = _get_field(entry, "item_name", "item")

        # Pick display product name
        prod_name = str(std_name or raw_name or "").strip()
        if not prod_name:
            prod_name = "Unknown Item"

        qty = _safe_decimal(_get_field(entry, "quantity"), default="1.000")
        amt = _safe_decimal(_get_field(entry, "total_amount", "amount"), default="0.00")
        unit = str(_get_field(entry, "unit") or "unit").strip() or "unit"

        if prod_name not in grouped:
            grouped[prod_name] = {
                "product_name": prod_name,
                "total_quantity": Decimal("0.000"),
                "total_revenue": Decimal("0.00"),
                "unit": unit,
                "transaction_count": 0,
            }

        grouped[prod_name]["total_quantity"] += qty
        grouped[prod_name]["total_revenue"] += amt
        grouped[prod_name]["transaction_count"] += 1
        # Update unit if default was generic 'unit'
        if grouped[prod_name]["unit"] == "unit" and unit != "unit":
            grouped[prod_name]["unit"] = unit

    items_list = list(grouped.values())

    # Sort by revenue descending
    sorted_by_revenue = sorted(
        items_list,
        key=lambda x: (x["total_revenue"], x["total_quantity"]),
        reverse=True,
    )
    # Sort by quantity descending
    sorted_by_quantity = sorted(
        items_list,
        key=lambda x: (x["total_quantity"], x["total_revenue"]),
        reverse=True,
    )

    def _format_items(item_dicts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "product_name": item["product_name"],
                "total_quantity": float(item["total_quantity"].quantize(Decimal("0.001"))),
                "total_revenue": float(item["total_revenue"].quantize(Decimal("0.01"))),
                "unit": item["unit"],
                "transaction_count": item["transaction_count"],
            }
            for item in item_dicts[:top_n]
        ]

    return {
        "by_revenue": _format_items(sorted_by_revenue),
        "by_quantity": _format_items(sorted_by_quantity),
    }


def get_top_products_by_revenue(
    entries: List[Union[Dict[str, Any], Any]], top_n: int = 5
) -> List[Dict[str, Any]]:
    """Convenience selector for top products by total revenue."""
    return calculate_top_products(entries, top_n=top_n)["by_revenue"]


def get_top_products_by_quantity(
    entries: List[Union[Dict[str, Any], Any]], top_n: int = 5
) -> List[Dict[str, Any]]:
    """Convenience selector for top products by total quantity sold."""
    return calculate_top_products(entries, top_n=top_n)["by_quantity"]


def calculate_customer_balances(
    entries: List[Union[Dict[str, Any], Any]],
) -> List[Dict[str, Any]]:
    """Calculates individual customer outstanding dues and transaction history.

    Strict Data Rule: Uses only customer fields that actually exist in the schema
    (customer_name, customer_phone, total_amount, paid_amount, due_amount, entry_date).
    Does NOT fabricate customer IDs or profiles for anonymous walk-in transactions.

    Returns:
        List[Dict[str, Any]] sorted by current_due DESC, total_billed DESC.
    """
    if not entries:
        return []

    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for entry in entries:
        cust_name_raw = _get_field(entry, "customer_name", "customer")
        if cust_name_raw is None:
            continue
        cust_name = str(cust_name_raw).strip()
        if not cust_name or cust_name.lower() in ("none", "null", "nan", "unknown"):
            continue

        phone_raw = _get_field(entry, "customer_phone", "customer_phone")
        phone = str(phone_raw).strip() if phone_raw else None
        if phone and phone.lower() in ("none", "null", "nan"):
            phone = None

        amt = _safe_decimal(_get_field(entry, "total_amount", "amount"))
        paid_raw = _get_field(entry, "paid_amount", "paid")
        due_raw = _get_field(entry, "due_amount", "due")
        tx_type = str(_get_field(entry, "transaction_type") or "credit").lower()
        date_str = str(_get_field(entry, "entry_date", "date") or "").strip()

        paid_dec = to_decimal(paid_raw)
        due_dec = to_decimal(due_raw)

        if paid_dec is not None and due_dec is not None:
            paid = paid_dec
            due = due_dec
        elif paid_dec is not None and due_dec is None:
            paid = paid_dec
            due = max(Decimal("0.00"), amt - paid) if tx_type == "credit" else Decimal("0.00")
        elif due_dec is not None and paid_dec is None:
            due = due_dec
            paid = max(Decimal("0.00"), amt - due)
        else:
            if tx_type == "cash":
                paid = amt
                due = Decimal("0.00")
            elif tx_type == "credit":
                paid = Decimal("0.00")
                due = amt
            else:
                paid = Decimal("0.00")
                due = Decimal("0.00")

        # Key by lowercased name + phone
        key = (cust_name.lower(), phone or "")

        if key not in grouped:
            grouped[key] = {
                "customer_name": cust_name,
                "customer_phone": phone,
                "total_billed": Decimal("0.00"),
                "total_paid": Decimal("0.00"),
                "current_due": Decimal("0.00"),
                "transaction_count": 0,
                "last_transaction_date": date_str,
            }

        grouped[key]["total_billed"] += amt
        grouped[key]["total_paid"] += paid
        grouped[key]["current_due"] += due
        grouped[key]["transaction_count"] += 1
        if phone and not grouped[key]["customer_phone"]:
            grouped[key]["customer_phone"] = phone
        if date_str and (not grouped[key]["last_transaction_date"] or date_str > grouped[key]["last_transaction_date"]):
            grouped[key]["last_transaction_date"] = date_str

    results = []
    for item in grouped.values():
        due_f = float(item["current_due"].quantize(Decimal("0.01")))
        results.append({
            "customer_name": item["customer_name"],
            "customer_phone": item["customer_phone"],
            "total_billed": float(item["total_billed"].quantize(Decimal("0.01"))),
            "total_paid": float(item["total_paid"].quantize(Decimal("0.01"))),
            "current_due": due_f,
            "transaction_count": item["transaction_count"],
            "last_transaction_date": item["last_transaction_date"] or None,
            "status": "pending_due" if due_f > 0 else "cleared",
        })

    # Sort customers with highest pending credit balances first
    return sorted(
        results,
        key=lambda x: (x["current_due"], x["total_billed"]),
        reverse=True,
    )


def generate_business_insights(
    entries: List[Union[Dict[str, Any], Any]],
    lang: str = "en",
) -> List[Dict[str, Any]]:
    """Generates localized business insights using established translation keys.

    Utilizes:
    - `insights.top_product`: '{item} with {qty} {unit} sold'
    - `insights.credit_alert`: '{count} customer accounts have pending dues'

    Returns:
        List of structured insight cards with id, type, title, message, and metrics.
    """
    if not entries:
        return []

    insights: List[Dict[str, Any]] = []

    # 1. Top Selling Product Insight
    top_products = calculate_top_products(entries, top_n=1)["by_quantity"]
    if top_products:
        top_item = top_products[0]
        # Format quantity cleanly (strip trailing zeroes if integer)
        qty_num = top_item["total_quantity"]
        qty_display = int(qty_num) if qty_num.is_integer() else qty_num

        msg = t(
            "insights.top_product",
            lang=lang,
            item=top_item["product_name"],
            qty=qty_display,
            unit=top_item["unit"],
            default=f"Top selling item is {top_item['product_name']} with {qty_display} {top_item['unit']} sold.",
        )
        insights.append({
            "id": "top_product",
            "type": "success",
            "title": t("dashboard.fast_moving_items", lang=lang, default="Fast-Moving Products"),
            "message": msg,
            "data": top_item,
        })

    # 2. Credit Exposure / Outstanding Balances Insight
    customer_balances = calculate_customer_balances(entries)
    pending_customers = [c for c in customer_balances if c["current_due"] > 0]
    pending_count = len(pending_customers)

    if pending_count > 0:
        msg = t(
            "insights.credit_alert",
            lang=lang,
            count=pending_count,
            default=f"Credit exposure alert: {pending_count} customer accounts have pending dues.",
        )
        total_pending_due = sum(c["current_due"] for c in pending_customers)
        insights.append({
            "id": "credit_alert",
            "type": "warning",
            "title": t("dashboard.outstanding_dues", lang=lang, default="Customer Outstanding Balances"),
            "message": msg,
            "data": {
                "pending_customer_count": pending_count,
                "total_outstanding_due": round(total_pending_due, 2),
            },
        })

    return insights


def calculate_temporal_summary(
    entries: List[Union[Dict[str, Any], Any]],
) -> Dict[str, Any]:
    """Calculates temporal sales aggregates, date ranges, and daily comparisons.

    Uses decimal-safe arithmetic and handles missing or varied date formats.

    Returns:
        {
            "earliest_date": Optional[str],
            "latest_date": Optional[str],
            "distinct_days_count": int,
            "daily_sales": Dict[str, Dict[str, Any]],  # date -> {sales, collected, due, count}
            "latest_day_sales": float,
            "previous_days_sales": float,
            "trend": str,  # 'up', 'down', 'stable', or 'single_day'
        }
    """
    if not entries:
        return {
            "earliest_date": None,
            "latest_date": None,
            "distinct_days_count": 0,
            "daily_sales": {},
            "latest_day_sales": 0.0,
            "previous_days_sales": 0.0,
            "trend": "no_data",
        }

    daily_map: Dict[str, Dict[str, Decimal]] = defaultdict(
        lambda: {
            "sales": Decimal("0.00"),
            "collected": Decimal("0.00"),
            "due": Decimal("0.00"),
            "count": Decimal("0"),
        }
    )

    valid_dates: List[str] = []

    for entry in entries:
        raw_date = str(_get_field(entry, "entry_date", "date") or "").strip()
        date_key = raw_date[:10] if len(raw_date) >= 10 else raw_date
        if not date_key or date_key.lower() in ("none", "null", "nan"):
            date_key = "undated"
        else:
            valid_dates.append(date_key)

        amt = _safe_decimal(_get_field(entry, "total_amount", "amount"))
        paid_raw = _get_field(entry, "paid_amount", "paid")
        due_raw = _get_field(entry, "due_amount", "due")
        tx_type = str(_get_field(entry, "transaction_type") or "credit").lower()

        paid_dec = to_decimal(paid_raw)
        due_dec = to_decimal(due_raw)

        if paid_dec is not None and due_dec is not None:
            paid = paid_dec
            due = due_dec
        elif paid_dec is not None and due_dec is None:
            paid = paid_dec
            due = max(Decimal("0.00"), amt - paid) if tx_type == "credit" else Decimal("0.00")
        elif due_dec is not None and paid_dec is None:
            due = due_dec
            paid = max(Decimal("0.00"), amt - due)
        else:
            if tx_type == "cash":
                paid = amt
                due = Decimal("0.00")
            elif tx_type == "credit":
                paid = Decimal("0.00")
                due = amt
            else:
                paid = Decimal("0.00")
                due = Decimal("0.00")

        daily_map[date_key]["sales"] += amt
        daily_map[date_key]["collected"] += paid
        daily_map[date_key]["due"] += due
        daily_map[date_key]["count"] += Decimal("1")

    sorted_dates = sorted([d for d in set(valid_dates) if d != "undated"])
    earliest_date = sorted_dates[0] if sorted_dates else None
    latest_date = sorted_dates[-1] if sorted_dates else None

    # Format daily_sales dict for clean consumption
    formatted_daily: Dict[str, Dict[str, Any]] = {}
    for d, stats in sorted(daily_map.items()):
        formatted_daily[d] = {
            "sales": float(stats["sales"].quantize(Decimal("0.01"))),
            "collected": float(stats["collected"].quantize(Decimal("0.01"))),
            "due": float(stats["due"].quantize(Decimal("0.01"))),
            "count": int(stats["count"]),
        }

    latest_day_sales = 0.0
    prev_days_sales = 0.0
    trend = "single_day"

    if len(sorted_dates) >= 2:
        latest_day_sales = formatted_daily[latest_date]["sales"]
        prev_date = sorted_dates[-2]
        prev_days_sales = formatted_daily[prev_date]["sales"]
        if latest_day_sales > prev_days_sales:
            trend = "up"
        elif latest_day_sales < prev_days_sales:
            trend = "down"
        else:
            trend = "stable"
    elif len(sorted_dates) == 1:
        latest_day_sales = formatted_daily[latest_date]["sales"]
        trend = "single_day"

    return {
        "earliest_date": earliest_date,
        "latest_date": latest_date,
        "distinct_days_count": len(sorted_dates),
        "daily_sales": formatted_daily,
        "latest_day_sales": latest_day_sales,
        "previous_days_sales": prev_days_sales,
        "trend": trend,
    }


def compute_dashboard_metrics(
    entries: List[Union[Dict[str, Any], Any]],
    lang: str = "en",
) -> Dict[str, Any]:
    """Computes complete analytical dashboard dataset for a kirana store."""
    summary = calculate_sales_summary(entries)
    top_products = calculate_top_products(entries, top_n=5)
    customer_balances = calculate_customer_balances(entries)
    insights = generate_business_insights(entries, lang=lang)
    temporal = calculate_temporal_summary(entries)

    return {
        "summary": summary,
        "top_products": top_products,
        "customer_balances": customer_balances,
        "insights": insights,
        "temporal": temporal,
    }
