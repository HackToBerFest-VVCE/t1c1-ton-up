"""Excel workbook export generator for Kirana Ledger Digitizer.

Produces structured multi-sheet .xlsx workbooks containing confirmed transactions,
customer dues summaries, and product performance analytics using pandas and openpyxl.
"""

from __future__ import annotations

import io
from typing import Any, Dict, List, Optional
import pandas as pd

from core.analytics import calculate_customer_balances, calculate_top_products


def export_ledger_to_excel(
    entries: List[Dict[str, Any]],
    shop_name: str = "Kirana Store",
    lang: str = "en",
) -> bytes:
    """Generates a multi-sheet Excel (.xlsx) workbook in-memory.

    Sheets:
    1. 'Transactions': All confirmed ledger rows with monetary and product details.
    2. 'Customer Dues': Outstanding credit balances grouped by customer.
    3. 'Product Performance': Aggregated sales volume and revenue per item.
    """
    output = io.BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # Sheet 1: Transactions
        tx_rows = []
        for e in entries:
            paid_val = e.get("paid_amount")
            due_val = e.get("due_amount")
            tx_rows.append({
                "Date": e.get("entry_date") or "",
                "Customer Name": e.get("customer_name") or "",
                "Customer Phone": e.get("customer_phone") or "",
                "Item Name": e.get("item_name") or "",
                "Standardized Item": e.get("standardized_item_name") or "",
                "Quantity": e.get("quantity", 1.0),
                "Unit": e.get("unit") or "unit",
                "Unit Price (INR)": e.get("unit_price", 0.0),
                "Total Amount (INR)": e.get("total_amount", 0.0),
                "Paid Amount (INR)": paid_val if paid_val is not None else 0.0,
                "Due Amount (INR)": due_val if due_val is not None else 0.0,
                "Transaction Type": e.get("transaction_type") or "credit",
                "Status": e.get("status") or "confirmed",
                "Batch ID": e.get("batch_id") or "",
            })
        df_tx = pd.DataFrame(tx_rows if tx_rows else [{"Status": "No confirmed transactions found"}])
        df_tx.to_excel(writer, sheet_name="Transactions", index=False)

        # Sheet 2: Customer Dues
        cust_balances = calculate_customer_balances(entries)
        cust_rows = []
        for c in cust_balances:
            cust_rows.append({
                "Customer Name": c.get("customer_name") or "",
                "Customer Phone": c.get("customer_phone") or "",
                "Total Billed (INR)": c.get("total_billed", 0.0),
                "Total Paid (INR)": c.get("total_paid", 0.0),
                "Current Due (INR)": c.get("current_due", 0.0),
                "Transaction Count": c.get("transaction_count", 0),
                "Last Transaction Date": c.get("last_transaction_date") or "",
                "Status": "Pending Due" if c.get("current_due", 0) > 0 else "Cleared",
            })
        df_cust = pd.DataFrame(cust_rows if cust_rows else [{"Customer Name": "No customer dues records"}])
        df_cust.to_excel(writer, sheet_name="Customer Dues", index=False)

        # Sheet 3: Product Performance
        top_products = calculate_top_products(entries, top_n=1000)
        prod_rows = []
        for p in top_products.get("by_revenue", []):
            prod_rows.append({
                "Product Name": p.get("product_name") or "",
                "Quantity Sold": p.get("total_quantity", 0.0),
                "Unit": p.get("unit") or "unit",
                "Total Revenue (INR)": p.get("total_revenue", 0.0),
                "Transaction Count": p.get("transaction_count", 0),
            })
        df_prod = pd.DataFrame(prod_rows if prod_rows else [{"Product Name": "No product records"}])
        df_prod.to_excel(writer, sheet_name="Product Performance", index=False)

    return output.getvalue()
