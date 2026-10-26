"""Tests verifying catalog data, product counts, and fuzzy-matching capability."""

import csv
import unittest
from pathlib import Path

from core.catalog import CatalogManager, get_catalog_manager


class TestCatalogData(unittest.TestCase):
    """Verifies that data/items.csv has ~50 common products and valid fields."""

    @classmethod
    def setUpClass(cls) -> None:
        root_dir = Path(__file__).resolve().parent.parent
        cls.csv_path = root_dir / "data" / "items.csv"
        cls.assertTrue(
            cls, cls.csv_path.exists(), "data/items.csv file must exist"
        )
        cls.mgr = CatalogManager(cls.csv_path)

    def test_item_count_around_fifty(self) -> None:
        """Requirement: data/items.csv with around 50 common kirana products."""
        items = self.mgr.get_all_items()
        self.assertGreaterEqual(len(items), 45, "Catalog should have at least 45 items")
        self.assertLessEqual(len(items), 55, "Catalog should not exceed 55 items")

    def test_csv_structure_and_types(self) -> None:
        with open(self.csv_path, mode="r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            required_cols = {
                "item_id",
                "name_en",
                "name_kn",
                "name_hi",
                "category",
                "default_unit",
                "standard_price_inr",
            }
            self.assertTrue(required_cols.issubset(set(reader.fieldnames or [])))

            ids = set()
            for row in reader:
                item_id = int(row["item_id"])
                self.assertNotIn(item_id, ids, f"Duplicate item_id found: {item_id}")
                ids.add(item_id)

                self.assertTrue(row["name_en"].strip())
                self.assertTrue(row["name_kn"].strip())
                self.assertTrue(row["name_hi"].strip())
                price = float(row["standard_price_inr"])
                self.assertGreater(price, 0.0)

    def test_catalog_retrieval_and_matching(self) -> None:
        # Retrieve Toor Dal (item_id 1)
        item1 = self.mgr.get_by_id(1)
        self.assertIsNotNone(item1)
        self.assertEqual(item1.name_en, "Toor Dal")
        self.assertIn("ತೊಗರಿ", item1.name_kn)

        # Search matching
        match = self.mgr.find_match("toor dal")
        self.assertIsNotNone(match)
        matched_item, score = match
        self.assertEqual(matched_item.item_id, 1)


if __name__ == "__main__":
    unittest.main()
