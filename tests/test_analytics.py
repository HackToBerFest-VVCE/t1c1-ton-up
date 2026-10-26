"""Unit tests for business analytics engine (Milestone 5 Phase 1).

Verifies sales summaries, product rankings, customer balances, decimal safety,
null handling, and multilingual insights without connecting to live databases.
"""

from decimal import Decimal
import unittest

from core.analytics import (
    calculate_customer_balances,
    calculate_sales_summary,
    calculate_top_products,
    compute_dashboard_metrics,
    generate_business_insights,
    get_top_products_by_quantity,
    get_top_products_by_revenue,
)
from core.models import EntryStatus, LedgerEntry, TransactionType


class TestBusinessAnalytics(unittest.TestCase):
    """Verifies pure financial and operational calculations on ledger records."""

    def setUp(self) -> None:
        self.sample_entries = [
            {
                "id": "e-1",
                "customer_name": "Ramesh Kumar",
                "customer_phone": "9876543210",
                "item_name": "Toor Dal",
                "standardized_item_name": "Toor Dal",
                "quantity": 2.0,
                "unit": "kg",
                "unit_price": 140.0,
                "total_amount": 280.0,
                "paid_amount": 100.0,
                "due_amount": 180.0,
                "transaction_type": "credit",
                "status": "confirmed",
                "entry_date": "2026-10-20",
            },
            {
                "id": "e-2",
                "customer_name": "Ramesh Kumar",
                "customer_phone": "9876543210",
                "item_name": "Sugar M",
                "standardized_item_name": "Sugar",
                "quantity": 3.0,
                "unit": "kg",
                "unit_price": 42.0,
                "total_amount": 126.0,
                "paid_amount": 0.0,
                "due_amount": 126.0,
                "transaction_type": "credit",
                "status": "confirmed",
                "entry_date": "2026-10-22",
            },
            {
                "id": "e-3",
                "customer_name": "Suresh Gowda",
                "customer_phone": "9900112233",
                "item_name": "Sunflower Oil",
                "standardized_item_name": "Cooking Oil",
                "quantity": 1.0,
                "unit": "litre",
                "unit_price": 150.0,
                "total_amount": 150.0,
                "paid_amount": 150.0,
                "due_amount": 0.0,
                "transaction_type": "cash",
                "status": "confirmed",
                "entry_date": "2026-10-23",
            },
            {
                "id": "e-4",
                "customer_name": None,  # Walk-in cash customer
                "customer_phone": None,
                "item_name": "Colgate Toothpaste",
                "standardized_item_name": "Toothpaste",
                "quantity": 1.0,
                "unit": "unit",
                "unit_price": 65.0,
                "total_amount": 65.0,
                "paid_amount": 65.0,
                "due_amount": 0.0,
                "transaction_type": "cash",
                "status": "confirmed",
                "entry_date": "2026-10-24",
            },
        ]

    def test_calculate_sales_summary_standard(self) -> None:
        """Verifies sales, collections, dues, and transaction count arithmetic."""
        summary = calculate_sales_summary(self.sample_entries)

        # Expected:
        # total_sales = 280 + 126 + 150 + 65 = 621.00
        # total_collected = 100 + 0 + 150 + 65 = 315.00
        # total_due = 180 + 126 + 0 + 0 = 306.00
        self.assertEqual(summary["total_sales"], 621.00)
        self.assertEqual(summary["total_collected"], 315.00)
        self.assertEqual(summary["total_due"], 306.00)
        self.assertEqual(summary["transaction_count"], 4)
        self.assertEqual(summary["average_order_value"], 155.25)
        self.assertEqual(summary["cash_sales"], 215.00)  # 150 + 65
        self.assertEqual(summary["credit_sales"], 406.00)  # 280 + 126

    def test_calculate_sales_summary_empty(self) -> None:
        """Empty entries list returns clean zeroes without raising ZeroDivisionError."""
        summary = calculate_sales_summary([])
        self.assertEqual(summary["total_sales"], 0.0)
        self.assertEqual(summary["total_collected"], 0.0)
        self.assertEqual(summary["total_due"], 0.0)
        self.assertEqual(summary["transaction_count"], 0)
        self.assertEqual(summary["average_order_value"], 0.0)

    def test_calculate_sales_summary_missing_and_null_values(self) -> None:
        """Handles entries with None or missing numeric fields defensively."""
        entries = [
            {"total_amount": None, "paid_amount": None, "due_amount": None},
            {"total_amount": 100.0, "paid_amount": "none", "due_amount": "null", "transaction_type": "credit"},
            {"total_amount": 50.0, "paid_amount": None, "due_amount": None, "transaction_type": "cash"},
        ]
        summary = calculate_sales_summary(entries)
        # Entry 1: 0, 0, 0
        # Entry 2 (credit, no paid/due): 100, 0, 100
        # Entry 3 (cash, no paid/due): 50, 50, 0
        self.assertEqual(summary["total_sales"], 150.00)
        self.assertEqual(summary["total_collected"], 50.00)
        self.assertEqual(summary["total_due"], 100.00)
        self.assertEqual(summary["transaction_count"], 3)

    def test_calculate_top_products_ranking(self) -> None:
        """Calculates top products ranked by revenue and quantity sold."""
        top_data = calculate_top_products(self.sample_entries, top_n=5)

        by_rev = top_data["by_revenue"]
        by_qty = top_data["by_quantity"]

        # Top product by revenue should be Toor Dal (₹280)
        self.assertEqual(by_rev[0]["product_name"], "Toor Dal")
        self.assertEqual(by_rev[0]["total_revenue"], 280.00)
        self.assertEqual(by_rev[0]["total_quantity"], 2.0)

        # Top product by quantity should be Sugar (3.0 kg)
        self.assertEqual(by_qty[0]["product_name"], "Sugar")
        self.assertEqual(by_qty[0]["total_quantity"], 3.0)
        self.assertEqual(by_qty[0]["total_revenue"], 126.00)

        # Convenience functions
        self.assertEqual(len(get_top_products_by_revenue(self.sample_entries, top_n=2)), 2)
        self.assertEqual(len(get_top_products_by_quantity(self.sample_entries, top_n=2)), 2)

    def test_calculate_top_products_empty(self) -> None:
        """Empty entries returns empty rankings."""
        res = calculate_top_products([])
        self.assertEqual(res["by_revenue"], [])
        self.assertEqual(res["by_quantity"], [])

    def test_calculate_top_products_standardized_name_priority(self) -> None:
        """Prefers standardized canonical name over handwritten abbreviations."""
        entries = [
            {"item_name": "T.Dal Super", "standardized_item_name": "Toor Dal", "quantity": 1, "total_amount": 140},
            {"item_name": "Toor Daal", "standardized_item_name": "Toor Dal", "quantity": 2, "total_amount": 280},
        ]
        top_p = calculate_top_products(entries)["by_revenue"]
        self.assertEqual(len(top_p), 1)
        self.assertEqual(top_p[0]["product_name"], "Toor Dal")
        self.assertEqual(top_p[0]["total_quantity"], 3.0)
        self.assertEqual(top_p[0]["total_revenue"], 420.00)

    def test_calculate_customer_balances_grouping(self) -> None:
        """Groups entries by customer name and calculates cumulative dues."""
        balances = calculate_customer_balances(self.sample_entries)

        # There are 2 identifiable customers: Ramesh Kumar and Suresh Gowda
        # Walk-in customer with None name should NOT be fabricated as a customer
        self.assertEqual(len(balances), 2)

        ramesh = balances[0]
        self.assertEqual(ramesh["customer_name"], "Ramesh Kumar")
        self.assertEqual(ramesh["customer_phone"], "9876543210")
        self.assertEqual(ramesh["total_billed"], 406.00)  # 280 + 126
        self.assertEqual(ramesh["total_paid"], 100.00)
        self.assertEqual(ramesh["current_due"], 306.00)   # 180 + 126
        self.assertEqual(ramesh["transaction_count"], 2)
        self.assertEqual(ramesh["last_transaction_date"], "2026-10-22")
        self.assertEqual(ramesh["status"], "pending_due")

        suresh = balances[1]
        self.assertEqual(suresh["customer_name"], "Suresh Gowda")
        self.assertEqual(suresh["total_billed"], 150.00)
        self.assertEqual(suresh["total_paid"], 150.00)
        self.assertEqual(suresh["current_due"], 0.00)
        self.assertEqual(suresh["status"], "cleared")

    def test_calculate_customer_balances_anonymous_excluded(self) -> None:
        """Entries without customer_name are not fabricated as customers."""
        anonymous_entries = [
            {"customer_name": None, "total_amount": 100.0, "paid_amount": 100.0},
            {"customer_name": "", "total_amount": 50.0, "paid_amount": 50.0},
            {"customer_name": "  ", "total_amount": 25.0, "paid_amount": 25.0},
            {"customer_name": "None", "total_amount": 10.0, "paid_amount": 10.0},
        ]
        balances = calculate_customer_balances(anonymous_entries)
        self.assertEqual(balances, [])

    def test_calculate_customer_balances_empty(self) -> None:
        """Empty entries returns empty balance list."""
        self.assertEqual(calculate_customer_balances([]), [])

    def test_generate_business_insights_multilingual(self) -> None:
        """Verifies insights generation across all 5 supported languages."""
        for lang in ["en", "kn", "hi", "te", "ta"]:
            insights = generate_business_insights(self.sample_entries, lang=lang)
            self.assertTrue(len(insights) >= 2)

            # Insight 1: Top product
            top_p_insight = next((i for i in insights if i["id"] == "top_product"), None)
            self.assertIsNotNone(top_p_insight)
            self.assertIn("Sugar", top_p_insight["message"])

            # Insight 2: Credit alert
            credit_insight = next((i for i in insights if i["id"] == "credit_alert"), None)
            self.assertIsNotNone(credit_insight)
            self.assertIn("1", credit_insight["message"])  # 1 customer with pending due

    def test_generate_business_insights_empty(self) -> None:
        """Empty entries returns empty list of insights."""
        self.assertEqual(generate_business_insights([]), [])

    def test_generate_business_insights_no_dues(self) -> None:
        """When no customers have dues, credit_alert is omitted."""
        cash_entries = [
            {
                "customer_name": "Pooja",
                "item_name": "Rice",
                "quantity": 5.0,
                "unit": "kg",
                "total_amount": 250.0,
                "paid_amount": 250.0,
                "due_amount": 0.0,
                "transaction_type": "cash",
            }
        ]
        insights = generate_business_insights(cash_entries)
        self.assertEqual(len(insights), 1)
        self.assertEqual(insights[0]["id"], "top_product")

    def test_compute_dashboard_metrics_combined(self) -> None:
        """Full dashboard metrics suite combines summary, products, balances, and insights."""
        dashboard = compute_dashboard_metrics(self.sample_entries, lang="en")
        self.assertIn("summary", dashboard)
        self.assertIn("top_products", dashboard)
        self.assertIn("customer_balances", dashboard)
        self.assertIn("insights", dashboard)

        self.assertEqual(dashboard["summary"]["transaction_count"], 4)
        self.assertEqual(len(dashboard["customer_balances"]), 2)

    def test_analytics_with_model_objects(self) -> None:
        """Verifies calculations work identically when passed LedgerEntry model objects."""
        entries = [
            LedgerEntry(
                item_name="Wheat Flour",
                quantity=10.0,
                unit="kg",
                total_amount=400.0,
                paid_amount=400.0,
                due_amount=0.0,
                transaction_type=TransactionType.CASH,
                status=EntryStatus.CONFIRMED,
            ),
            LedgerEntry(
                item_name="Tea Powder",
                quantity=2.0,
                unit="packet",
                total_amount=160.0,
                paid_amount=0.0,
                due_amount=160.0,
                customer_name="Mahesh",
                transaction_type=TransactionType.CREDIT,
                status=EntryStatus.CONFIRMED,
            ),
        ]
        summary = calculate_sales_summary(entries)
        self.assertEqual(summary["total_sales"], 560.00)
        self.assertEqual(summary["total_collected"], 400.00)
        self.assertEqual(summary["total_due"], 160.00)

        top_p = get_top_products_by_revenue(entries)
        self.assertEqual(top_p[0]["product_name"], "Wheat Flour")

        balances = calculate_customer_balances(entries)
        self.assertEqual(len(balances), 1)
        self.assertEqual(balances[0]["customer_name"], "Mahesh")
        self.assertEqual(balances[0]["current_due"], 160.00)


if __name__ == "__main__":
    unittest.main()
