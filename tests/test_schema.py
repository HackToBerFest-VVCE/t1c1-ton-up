"""Tests verifying database schema expectations, tables, constraints, and indexes."""

import re
import unittest
from pathlib import Path


class TestSchemaExpectations(unittest.TestCase):
    """Verifies that sql/schema.sql satisfies all Milestone 1 database requirements."""

    @classmethod
    def setUpClass(cls) -> None:
        root_dir = Path(__file__).resolve().parent.parent
        cls.schema_path = root_dir / "sql" / "schema.sql"
        cls.assertTrue(
            cls, cls.schema_path.exists(), "sql/schema.sql file must exist"
        )
        cls.sql_text = cls.schema_path.read_text(encoding="utf-8")

    def test_schema_file_is_not_empty(self) -> None:
        self.assertGreater(len(self.sql_text.strip()), 100)

    def test_required_tables_exist(self) -> None:
        """Requirement 3: shops, ledger_entries, item_aliases, corrections, and reports."""
        required_tables = [
            "shops",
            "ledger_entries",
            "item_aliases",
            "corrections",
            "reports",
        ]
        for table in required_tables:
            pattern = rf"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?{table}\s*\("
            match = re.search(pattern, self.sql_text, re.IGNORECASE)
            self.assertIsNotNone(
                match, f"Table '{table}' creation statement was not found in sql/schema.sql"
            )

    def test_primary_keys_defined(self) -> None:
        # Check that UUID primary keys are defined
        self.assertIn("id UUID PRIMARY KEY", self.sql_text)

    def test_foreign_keys_defined(self) -> None:
        self.assertIn("REFERENCES shops(id)", self.sql_text)
        self.assertIn("REFERENCES ledger_entries(id)", self.sql_text)

    def test_indexes_defined(self) -> None:
        expected_indexes = [
            "idx_shops_phone",
            "idx_ledger_entries_shop_id",
            "idx_ledger_entries_entry_date",
            "idx_ledger_entries_status",
            "idx_item_aliases_alias_name",
            "idx_corrections_ledger_entry_id",
            "idx_reports_shop_id",
        ]
        for idx in expected_indexes:
            self.assertIn(idx, self.sql_text, f"Index '{idx}' was not found in sql/schema.sql")

    def test_transaction_type_constraint(self) -> None:
        self.assertIn("transaction_type IN ('credit', 'debit', 'cash')", self.sql_text)

    def test_entry_status_constraint(self) -> None:
        self.assertIn("status IN ('pending', 'confirmed', 'rejected')", self.sql_text)


if __name__ == "__main__":
    unittest.main()
