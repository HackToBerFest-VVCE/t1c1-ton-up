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

    def test_milestone3_columns_and_migration(self) -> None:
        self.assertIn("paid_amount NUMERIC(10, 2)", self.sql_text)
        self.assertIn("due_amount NUMERIC(10, 2)", self.sql_text)
        self.assertIn("batch_id VARCHAR(64)", self.sql_text)
        self.assertIn("row_index INT", self.sql_text)
        self.assertIn("idx_ledger_entries_shop_batch_row", self.sql_text)

        migration_path = self.schema_path.parent / "migrations" / "001_add_paid_due_and_idempotency.sql"
        self.assertTrue(migration_path.exists(), "Migration 001 must exist")
        mig_text = migration_path.read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS paid_amount", mig_text)
        self.assertIn("ADD COLUMN IF NOT EXISTS due_amount", mig_text)
        self.assertIn("ADD COLUMN IF NOT EXISTS batch_id", mig_text)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_entries_shop_batch_row", mig_text)

    def test_milestone4_owner_id_and_rls(self) -> None:
        """Verifies owner_id column, indexes, and RLS policies in schema.sql and migration 002."""
        # Schema checks
        self.assertIn("owner_id UUID REFERENCES auth.users(id)", self.sql_text)
        self.assertIn("owner_id UUID REFERENCES auth.users(id) ON DELETE SET NULL", self.sql_text)
        self.assertNotIn("owner_id UUID REFERENCES auth.users(id) ON DELETE CASCADE", self.sql_text)
        self.assertIn("idx_shops_owner_id", self.sql_text)

        tables = ["shops", "ledger_entries", "item_aliases", "corrections", "reports"]
        for tbl in tables:
            self.assertIn(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;", self.sql_text)

        self.assertIn("CREATE POLICY \"shops_owner_select\"", self.sql_text)
        self.assertIn("CREATE POLICY \"shops_owner_insert\"", self.sql_text)
        self.assertIn("CREATE POLICY \"ledger_entries_owner_select\"", self.sql_text)
        self.assertIn("CREATE POLICY \"ledger_entries_owner_insert\"", self.sql_text)

        # Migration 002 checks
        mig002_path = self.schema_path.parent / "migrations" / "002_add_auth_and_rls.sql"
        self.assertTrue(mig002_path.exists(), "Migration 002 must exist")
        mig002_text = mig002_path.read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES auth.users(id)", mig002_text)
        self.assertIn("owner_id UUID REFERENCES auth.users(id) ON DELETE SET NULL", mig002_text)
        self.assertNotIn("owner_id UUID REFERENCES auth.users(id) ON DELETE CASCADE", mig002_text)
        self.assertIn("CREATE INDEX IF NOT EXISTS idx_shops_owner_id", mig002_text)
        for tbl in tables:
            self.assertIn(f"ALTER TABLE {tbl} ENABLE ROW LEVEL SECURITY;", mig002_text)
        self.assertIn("CREATE POLICY \"shops_owner_select\"", mig002_text)
        self.assertIn("CREATE POLICY \"shops_owner_insert\"", mig002_text)
        self.assertIn("CREATE POLICY \"ledger_entries_owner_select\"", mig002_text)
        self.assertIn("CREATE POLICY \"ledger_entries_owner_insert\"", mig002_text)

        # Security check: shops_owner_insert must strictly check admin role and NOT allow shopkeepers to self-create shops
        admin_check_sql = "((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')"
        self.assertIn(admin_check_sql, self.sql_text)
        self.assertIn(admin_check_sql, mig002_text)
        self.assertNotIn("shops_owner_insert\" ON shops\n    FOR INSERT TO authenticated\n    WITH CHECK (\n        owner_id = auth.uid()", self.sql_text)
        self.assertNotIn("shops_owner_insert\" ON shops\n    FOR INSERT TO authenticated\n    WITH CHECK (\n        owner_id = auth.uid()", mig002_text)




if __name__ == "__main__":
    unittest.main()
