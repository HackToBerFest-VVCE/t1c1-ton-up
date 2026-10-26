-- ==============================================================================
-- Kirana Ledger Digitizer - Migration 002
-- File: sql/migrations/002_add_auth_and_rls.sql
-- Status: PROPOSED FOR REVIEW ONLY - DO NOT EXECUTE AUTOMATICALLY
-- Purpose: Adds shopkeeper ownership (owner_id -> auth.users) and enforces
--          Row Level Security (RLS) across all tables to resolve error 42501.
--          Supports admin oversight and per-shopkeeper tenant isolation.
-- ==============================================================================

-- -----------------------------------------------------------------------------
-- 1. ADD owner_id TO SHOPS TABLE
-- -----------------------------------------------------------------------------
-- Adds owner_id referencing Supabase auth.users(id).
-- Nullable with ON DELETE SET NULL to ensure deleting an Auth user does not delete
-- the shop or its financial ledger history.
ALTER TABLE shops
ADD COLUMN IF NOT EXISTS owner_id UUID REFERENCES auth.users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_shops_owner_id ON shops(owner_id);

-- In case owner_id was already created in an earlier draft with ON DELETE CASCADE,
-- safely migrate the foreign key constraint to ON DELETE SET NULL:
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN (
        SELECT tc.constraint_name
        FROM information_schema.table_constraints AS tc
        JOIN information_schema.key_column_usage AS kcu
          ON tc.constraint_name = kcu.constraint_name
          AND tc.table_schema = kcu.table_schema
        JOIN information_schema.referential_constraints AS rc
          ON tc.constraint_name = rc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema = 'public'
          AND tc.table_name = 'shops'
          AND kcu.column_name = 'owner_id'
          AND rc.delete_rule != 'SET NULL'
    ) LOOP
        EXECUTE 'ALTER TABLE public.shops DROP CONSTRAINT ' || quote_ident(r.constraint_name);
        EXECUTE 'ALTER TABLE public.shops ADD CONSTRAINT shops_owner_id_fkey FOREIGN KEY (owner_id) REFERENCES auth.users(id) ON DELETE SET NULL';
    END LOOP;
END $$;

-- -----------------------------------------------------------------------------
-- Existing records safety:
-- Existing shops without an owner_id remain unlinked and inaccessible to
-- regular shopkeepers until explicitly assigned by an administrator.
-- To assign an existing shop to a specific shopkeeper:
--   UPDATE shops SET owner_id = '<SHOPKEEPER_USER_UUID>' WHERE id = '<SHOP_UUID>';
-- (Do NOT bulk-assign existing shops to an arbitrary user).
-- -----------------------------------------------------------------------------


-- -----------------------------------------------------------------------------
-- 2. SECURE ADMIN ROLE PROVISIONING IN SUPABASE
-- In Supabase Auth, app_metadata cannot be modified by users through client SDKs.
-- Client-facing calls (e.g. supabase.auth.update_user) can only modify user_metadata,
-- while app_metadata is strictly reserved for service_role and database administrators.
--
-- To provision a verified platform administrator, run this SQL in the Supabase SQL Editor:
--   UPDATE auth.users
--   SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb) || '{"role": "admin"}'::jsonb
--   WHERE email = '<YOUR_ADMIN_EMAIL>';
-- -----------------------------------------------------------------------------


-- -----------------------------------------------------------------------------
-- 3. ENABLE ROW LEVEL SECURITY (RLS) ON ALL TABLES
-- -----------------------------------------------------------------------------
ALTER TABLE shops ENABLE ROW LEVEL SECURITY;
ALTER TABLE ledger_entries ENABLE ROW LEVEL SECURITY;
ALTER TABLE item_aliases ENABLE ROW LEVEL SECURITY;
ALTER TABLE corrections ENABLE ROW LEVEL SECURITY;
ALTER TABLE reports ENABLE ROW LEVEL SECURITY;


-- -----------------------------------------------------------------------------
-- 4. RLS POLICIES FOR SHOPS
-- Shop creation and deletion are strictly ADMIN-ONLY.
-- Shopkeepers access only their assigned shop(s) and cannot reassign ownership.
-- -----------------------------------------------------------------------------
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
        -- Regular shopkeepers cannot reassign ownership; only admins can reassign
        (owner_id = auth.uid())
        OR ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
    );

DROP POLICY IF EXISTS "shops_owner_delete" ON shops;
CREATE POLICY "shops_owner_delete" ON shops
    FOR DELETE TO authenticated
    USING (
        ((auth.jwt() -> 'app_metadata' ->> 'role') = 'admin')
    );


-- -----------------------------------------------------------------------------
-- 5. RLS POLICIES FOR LEDGER ENTRIES
-- Users can only view, insert, and modify entries belonging to their own shops.
-- Administrators can access entries across shops for auditing.
-- -----------------------------------------------------------------------------
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


-- -----------------------------------------------------------------------------
-- 5. RLS POLICIES FOR ITEM ALIASES
-- Global aliases (shop_id IS NULL) are readable by all authenticated users.
-- Shop-specific aliases are restricted to the owner of that shop or admin.
-- -----------------------------------------------------------------------------
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


-- -----------------------------------------------------------------------------
-- 6. RLS POLICIES FOR CORRECTIONS & REPORTS
-- -----------------------------------------------------------------------------
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
