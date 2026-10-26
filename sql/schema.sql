-- ==============================================================================
-- Kirana Ledger Digitizer - Initial PostgreSQL Schema (Supabase)
-- Milestone 1: Core Tables, Relations, Constraints, and Indexes
-- ==============================================================================

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- -----------------------------------------------------------------------------
-- 1. SHOPS TABLE
-- Represents individual kirana stores using the digitizer platform.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shops (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID REFERENCES auth.users(id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    owner_name TEXT,
    phone VARCHAR(20),
    address TEXT,
    default_language VARCHAR(10) NOT NULL DEFAULT 'kn',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Indexes for shops table
CREATE INDEX IF NOT EXISTS idx_shops_owner_id ON shops(owner_id);
CREATE INDEX IF NOT EXISTS idx_shops_phone ON shops(phone);
CREATE INDEX IF NOT EXISTS idx_shops_name ON shops(name);
CREATE INDEX IF NOT EXISTS idx_shops_created_at ON shops(created_at DESC);

-- -----------------------------------------------------------------------------
-- 2. LEDGER ENTRIES TABLE
-- Stores individual transaction lines extracted from ledger photographs
-- and confirmed by the shopkeeper.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ledger_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id UUID NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    customer_name TEXT,
    customer_phone VARCHAR(20),
    item_name TEXT NOT NULL,
    standardized_item_name TEXT,
    quantity NUMERIC(10, 3) NOT NULL DEFAULT 1.000,
    unit VARCHAR(30) NOT NULL DEFAULT 'unit',
    unit_price NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    total_amount NUMERIC(10, 2) NOT NULL DEFAULT 0.00,
    paid_amount NUMERIC(10, 2),
    due_amount NUMERIC(10, 2),
    batch_id VARCHAR(64),
    row_index INT,
    transaction_type VARCHAR(20) NOT NULL DEFAULT 'credit'
        CHECK (transaction_type IN ('credit', 'debit', 'cash')),
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'confirmed', 'rejected')),
    confidence_score NUMERIC(5, 4),
    raw_ocr_text TEXT,
    image_url TEXT,
    entry_date DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Indexes for ledger_entries table
CREATE INDEX IF NOT EXISTS idx_ledger_entries_shop_id ON ledger_entries(shop_id);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_customer_name ON ledger_entries(customer_name);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_entry_date ON ledger_entries(entry_date DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_status ON ledger_entries(status);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_transaction_type ON ledger_entries(transaction_type);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_standardized_item ON ledger_entries(standardized_item_name);
CREATE INDEX IF NOT EXISTS idx_ledger_entries_shop_date ON ledger_entries(shop_id, entry_date DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_entries_shop_batch_row ON ledger_entries(shop_id, batch_id, row_index) WHERE batch_id IS NOT NULL;

-- -----------------------------------------------------------------------------
-- 3. ITEM ALIASES TABLE
-- Maps handwritten vernacular abbreviations and alternate spellings
-- to canonical kirana product names for consistent analytics.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS item_aliases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id UUID REFERENCES shops(id) ON DELETE CASCADE, -- NULL indicates global alias
    alias_name TEXT NOT NULL,
    standard_name TEXT NOT NULL,
    language VARCHAR(10) NOT NULL DEFAULT 'en',
    confidence NUMERIC(5, 4) NOT NULL DEFAULT 1.0000,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Indexes for item_aliases table
CREATE INDEX IF NOT EXISTS idx_item_aliases_alias_name ON item_aliases(alias_name);
CREATE INDEX IF NOT EXISTS idx_item_aliases_standard_name ON item_aliases(standard_name);
CREATE INDEX IF NOT EXISTS idx_item_aliases_shop_id ON item_aliases(shop_id);

-- -----------------------------------------------------------------------------
-- 4. CORRECTIONS TABLE
-- Captures human edits made to AI-extracted records. Essential for auditing,
-- financial accountability, and active learning refinement.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS corrections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ledger_entry_id UUID REFERENCES ledger_entries(id) ON DELETE CASCADE,
    shop_id UUID NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    field_name VARCHAR(100) NOT NULL,
    original_value TEXT,
    corrected_value TEXT,
    corrected_by VARCHAR(100) NOT NULL DEFAULT 'owner',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Indexes for corrections table
CREATE INDEX IF NOT EXISTS idx_corrections_ledger_entry_id ON corrections(ledger_entry_id);
CREATE INDEX IF NOT EXISTS idx_corrections_shop_id ON corrections(shop_id);
CREATE INDEX IF NOT EXISTS idx_corrections_created_at ON corrections(created_at DESC);

-- -----------------------------------------------------------------------------
-- 5. REPORTS TABLE
-- Stores precomputed business metrics, outstanding dues lists, and
-- multilingual business intelligence generated for shopkeepers.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    shop_id UUID NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    report_type VARCHAR(50) NOT NULL
        CHECK (report_type IN ('daily_summary', 'outstanding_dues', 'product_trends', 'custom')),
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    insights_multilingual JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now())
);

-- Indexes for reports table
CREATE INDEX IF NOT EXISTS idx_reports_shop_id ON reports(shop_id);
CREATE INDEX IF NOT EXISTS idx_reports_report_type ON reports(report_type);
CREATE INDEX IF NOT EXISTS idx_reports_period ON reports(shop_id, period_start, period_end);

-- -----------------------------------------------------------------------------
-- 6. AUTOMATIC TIMESTAMP TRIGGER
-- Updates 'updated_at' column whenever a row is modified.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = timezone('utc'::text, now());
    RETURN NEW;
END;
$$ language 'plpgsql';

DROP TRIGGER IF EXISTS trigger_shops_updated_at ON shops;
CREATE TRIGGER trigger_shops_updated_at
    BEFORE UPDATE ON shops
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trigger_ledger_entries_updated_at ON ledger_entries;
CREATE TRIGGER trigger_ledger_entries_updated_at
    BEFORE UPDATE ON ledger_entries
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- -----------------------------------------------------------------------------
-- 7. ROW LEVEL SECURITY (RLS) POLICIES
-- Enforces multi-tenant data isolation per authenticated shopkeeper.
-- -----------------------------------------------------------------------------
ALTER TABLE shops ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE item_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE reports ENABLE ROW LEVEL SECURITY;

-- Shops policies
DROP POLICY IF EXISTS "shops_owner_select" ON shops;
CREATE POLICY "shops_owner_select" ON shops
    FOR SELECT TO authenticated
    USING (
        owner_id = auth.uid()
        OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
    );

DROP POLICY IF EXISTS "shops_owner_insert" ON shops;
CREATE POLICY "shops_owner_insert" ON shops
    FOR INSERT TO authenticated
    WITH CHECK (
        ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
    );

DROP POLICY IF EXISTS "shops_owner_update" ON shops;
CREATE POLICY "shops_owner_update" ON shops
    FOR UPDATE TO authenticated
    USING (
        owner_id = auth.uid()
        OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
    )
    WITH CHECK (
        (owner_id = auth.uid())
        OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
    );

DROP POLICY IF EXISTS "shops_owner_delete" ON shops;
CREATE POLICY "shops_owner_delete" ON shops
    FOR DELETE TO authenticated
    USING (
        ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
    );

-- Ledger entries policies
DROP POLICY IF EXISTS "ledger_entries_owner_select" ON ledger_entries;
CREATE POLICY "ledger_entries_owner_select" ON ledger_entries
    FOR SELECT TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = ledger_entries.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

DROP POLICY IF EXISTS "ledger_entries_owner_insert" ON ledger_entries;
CREATE POLICY "ledger_entries_owner_insert" ON ledger_entries
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = ledger_entries.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

DROP POLICY IF EXISTS "ledger_entries_owner_update" ON ledger_entries;
CREATE POLICY "ledger_entries_owner_update" ON ledger_entries
    FOR UPDATE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = ledger_entries.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = ledger_entries.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

DROP POLICY IF EXISTS "ledger_entries_owner_delete" ON ledger_entries;
CREATE POLICY "ledger_entries_owner_delete" ON ledger_entries
    FOR DELETE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = ledger_entries.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

-- Item aliases policies
DROP POLICY IF EXISTS "item_aliases_select" ON item_aliases;
CREATE POLICY "item_aliases_select" ON item_aliases
    FOR SELECT TO authenticated
    USING (
        shop_id IS NULL
        OR EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = item_aliases.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

DROP POLICY IF EXISTS "item_aliases_owner_insert" ON item_aliases;
CREATE POLICY "item_aliases_owner_insert" ON item_aliases
    FOR INSERT TO authenticated
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = item_aliases.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

DROP POLICY IF EXISTS "item_aliases_owner_update" ON item_aliases;
CREATE POLICY "item_aliases_owner_update" ON item_aliases
    FOR UPDATE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = item_aliases.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = item_aliases.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

DROP POLICY IF EXISTS "item_aliases_owner_delete" ON item_aliases;
CREATE POLICY "item_aliases_owner_delete" ON item_aliases
    FOR DELETE TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = item_aliases.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

-- Corrections & Reports policies
DROP POLICY IF EXISTS "corrections_owner_all" ON corrections;
CREATE POLICY "corrections_owner_all" ON corrections
    FOR ALL TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = corrections.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = corrections.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

DROP POLICY IF EXISTS "reports_owner_all" ON reports;
CREATE POLICY "reports_owner_all" ON reports
    FOR ALL TO authenticated
    USING (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = reports.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    )
    WITH CHECK (
        EXISTS (
            SELECT 1 FROM shops
            WHERE shops.id = reports.shop_id
              AND (shops.owner_id = auth.uid() OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin'))
        )
    );

