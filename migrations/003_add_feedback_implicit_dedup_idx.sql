-- Migration: 003_add_feedback_implicit_dedup_idx.sql
-- Purpose: Add unique index for implicit signal deduplication (idempotency)
-- 
-- Problem: Implicit signals (langsmith_feedback_id IS NULL) had no unique
--          constraint, allowing duplicates on retry. This corrupts confidence
--          calculations when the same signal is recorded multiple times.
--
-- Solution: Add partial unique index on (run_id, template_key, feedback_type, source)
--          for records where langsmith_feedback_id IS NULL.
--
-- This pairs with the existing kg_feedback_run_ls_idx for LangSmith feedback
-- to ensure ALL feedback records are idempotent under retries.
--
-- Rollback: 
--   DROP INDEX IF EXISTS kg.kg_feedback_implicit_dedup_idx;

-- Step 1: Deduplicate existing data (keep most recent per unique key)
-- This is safe because it keeps the highest-ID record (most recent)
DELETE FROM kg.feedback_records a
USING (
    SELECT run_id, template_key, feedback_type, source, MAX(id) as max_id
    FROM kg.feedback_records
    WHERE langsmith_feedback_id IS NULL
    GROUP BY run_id, template_key, feedback_type, source
    HAVING COUNT(*) > 1
) b
WHERE a.run_id = b.run_id 
  AND a.template_key = b.template_key 
  AND a.feedback_type = b.feedback_type 
  AND a.source = b.source
  AND a.langsmith_feedback_id IS NULL
  AND a.id != b.max_id;

-- Step 2: Create unique index for implicit signal deduplication
-- Only applies to records without langsmith_feedback_id (implicit signals)
CREATE UNIQUE INDEX IF NOT EXISTS kg_feedback_implicit_dedup_idx 
ON kg.feedback_records(run_id, template_key, feedback_type, source) 
WHERE langsmith_feedback_id IS NULL;

-- Update table comment
COMMENT ON TABLE kg.feedback_records IS 
'Feedback records table. Deduplication is by:
- (run_id, langsmith_feedback_id) for LangSmith feedback (kg_feedback_run_ls_idx)
- (run_id, template_key, feedback_type, source) for implicit signals (kg_feedback_implicit_dedup_idx)
All inserts use ON CONFLICT DO UPDATE for idempotency under retries.';
