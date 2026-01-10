-- Migration: 002_add_repo_root_composite_key.sql
-- Purpose: Add repo_root column and create composite unique (repo_root, uri)
-- 
-- Problem: The UNIQUE(uri) constraint prevents the same spec from being used
--          across multiple repositories or demo runs, even with --fresh flag.
--          Simply dropping UNIQUE(uri) would lose data integrity.
--
-- Solution: Add repo_root column and create composite unique (repo_root, uri).
--          This allows the same URI to be used in different repo contexts while
--          maintaining uniqueness within a repo.
--
-- Data Model: (source_system_id, sha256) for content dedup
--             (repo_root, uri) for per-repo spec uniqueness
--
-- Rollback: 
--   ALTER TABLE spec_silver.spec_documents DROP COLUMN repo_root;
--   ALTER TABLE spec_silver.spec_documents ADD CONSTRAINT spec_documents_uri_key UNIQUE (uri);

-- Step 1: Add repo_root column (nullable initially for backfill)
ALTER TABLE spec_silver.spec_documents 
ADD COLUMN IF NOT EXISTS repo_root TEXT;

-- Step 2: Backfill existing rows with a default value
-- Use the current working directory placeholder for existing data
UPDATE spec_silver.spec_documents 
SET repo_root = '__legacy__' 
WHERE repo_root IS NULL;

-- Step 3: Make repo_root NOT NULL after backfill
ALTER TABLE spec_silver.spec_documents 
ALTER COLUMN repo_root SET NOT NULL;

-- Step 4: Drop the old UNIQUE(uri) constraint
ALTER TABLE spec_silver.spec_documents 
DROP CONSTRAINT IF EXISTS spec_documents_uri_key;

-- Step 5: Create new composite unique constraint (repo_root, uri)
-- This allows the same spec URI to be used in different repo contexts
ALTER TABLE spec_silver.spec_documents 
ADD CONSTRAINT spec_documents_repo_uri_key UNIQUE (repo_root, uri);

-- Step 6: Create index for repo_root lookups
CREATE INDEX IF NOT EXISTS idx_spec_documents_repo_root 
ON spec_silver.spec_documents(repo_root);

-- Update table comment
COMMENT ON TABLE spec_silver.spec_documents IS 
'Spec documents table. Deduplication is by (source_system_id, sha256) for content, 
and (repo_root, uri) for per-repo uniqueness. Same spec can exist in different repos.';
