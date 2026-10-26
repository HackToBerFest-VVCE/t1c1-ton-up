-- ==============================================================================
-- Kirana Ledger Digitizer - Migration 001
-- File: sql/migrations/001_add_paid_due_and_idempotency.sql
-- Status: PROPOSED FOR REVIEW ONLY - DO NOT EXECUTE AUTOMATICALLY
-- Purpose: Adds optional payment tracking (paid_amount, due_amount) and
--          database-level duplicate protection (batch_id, row_index, unique index).
-- ==============================================================================

-- 1. Add optional monetary columns to ledger_entries
-- Preserves missing/unpaid values as NULL rather than 0.00
ALTER TABLE ledger_entries
ADD COLUMN IF NOT EXISTS paid_amount NUMERIC(10, 2),
ADD COLUMN IF NOT EXISTS due_amount NUMERIC(10, 2);

-- 2. Add batch identifier and row index for idempotent persistence
ALTER TABLE ledger_entries
ADD COLUMN IF NOT EXISTS batch_id VARCHAR(64),
ADD COLUMN IF NOT EXISTS row_index INT;

-- 3. Create partial unique index enforcing database-level duplicate protection
-- Prevents repeated saves of the same extraction batch for a given shop
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_entries_shop_batch_row
ON ledger_entries (shop_id, batch_id, row_index)
WHERE batch_id IS NOT NULL;
