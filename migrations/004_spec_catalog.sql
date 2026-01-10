-- Migration 004: Spec Catalog for Local-First Discovery
-- Applied: (auto-recorded on execution)
-- Description: Adds discovery catalog tables to spec_silver schema for local API spec caching.
--   Enables offline discovery and faster resolution by:
--   1. Caching APIs.guru directory data locally
--   2. Using pgvector HNSW for semantic search over API metadata
--   3. Supporting curated "golden" entries for high-quality matches
--
-- Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md:
--   - Local catalog eliminates dependency on APIs.guru availability
--   - P50 resolution target: <500ms on warm DB
--   - HNSW index for best speed/recall tradeoff at catalog size (~1000-20000 entries)
--
-- Schema: spec_silver (aligns with existing medallion architecture)
-- Tables: discovery_providers, discovery_specs, discovery_categories, discovery_aliases

-- =============================================================================
-- Ensure pgvector extension (should already exist from baseline migration)
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- =============================================================================
-- spec_silver.discovery_providers: Known API providers (top-level aggregation)
-- =============================================================================

CREATE TABLE IF NOT EXISTS spec_silver.discovery_providers (
    id                  BIGSERIAL PRIMARY KEY,
    -- Provider identification
    provider_key        TEXT NOT NULL UNIQUE,       -- e.g., "stripe.com:stripe"
    domain              TEXT NOT NULL,              -- e.g., "stripe.com"
    slug                TEXT NOT NULL,              -- e.g., "stripe" (APIs.guru key)
    
    -- Display metadata
    display_name        TEXT NOT NULL,              -- e.g., "Stripe API"
    description         TEXT,
    logo_url            TEXT,
    homepage_url        TEXT,
    
    -- Quality signals
    is_curated          BOOLEAN NOT NULL DEFAULT FALSE,  -- True for hand-picked entries
    quality_tier        TEXT NOT NULL DEFAULT 'standard', -- 'premium', 'standard', 'untrusted'
    popularity_rank     INT,                        -- Relative popularity (lower = more popular)
    
    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_refreshed_at   TIMESTAMPTZ,                -- When data was last synced from upstream
    
    -- Indexable fields
    search_text         TEXT,                       -- Full-text search content
    embedding           VECTOR(1536)                -- Semantic search embedding (text-embedding-3-small)
);

-- Indexes for provider lookup
CREATE INDEX IF NOT EXISTS idx_discovery_providers_domain 
ON spec_silver.discovery_providers(domain);

CREATE INDEX IF NOT EXISTS idx_discovery_providers_slug 
ON spec_silver.discovery_providers(slug);

CREATE INDEX IF NOT EXISTS idx_discovery_providers_curated 
ON spec_silver.discovery_providers(is_curated) 
WHERE is_curated = TRUE;

CREATE INDEX IF NOT EXISTS idx_discovery_providers_tier 
ON spec_silver.discovery_providers(quality_tier);

-- Full-text search index (GIN for performance)
CREATE INDEX IF NOT EXISTS idx_discovery_providers_search_gin 
ON spec_silver.discovery_providers 
USING gin(to_tsvector('english', COALESCE(search_text, '')));

-- HNSW index for vector similarity (better speed/recall than IVFFlat at catalog size)
-- m=16, ef_construction=64 are good defaults for ~10k-50k vectors
-- See: https://www.crunchydata.com/blog/hnsw-indexes-with-postgres-and-pgvector
CREATE INDEX IF NOT EXISTS idx_discovery_providers_embedding_hnsw 
ON spec_silver.discovery_providers 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- spec_silver.discovery_specs: Individual API spec versions
-- =============================================================================

CREATE TABLE IF NOT EXISTS spec_silver.discovery_specs (
    id                  BIGSERIAL PRIMARY KEY,
    provider_id         BIGINT NOT NULL REFERENCES spec_silver.discovery_providers(id) ON DELETE CASCADE,
    
    -- Version identification
    version             TEXT NOT NULL,              -- e.g., "v1", "2024-01-01"
    is_preferred        BOOLEAN NOT NULL DEFAULT FALSE, -- True for recommended version
    
    -- Spec location
    spec_url            TEXT NOT NULL,              -- Primary fetch URL
    spec_url_backup     TEXT,                       -- Backup URL (e.g., raw GitHub)
    
    -- Spec metadata
    spec_format         TEXT NOT NULL,              -- 'openapi_3.0', 'openapi_3.1', 'swagger_2'
    title               TEXT,
    api_version         TEXT,                       -- Version from spec info
    
    -- Validation status
    last_validated_at   TIMESTAMPTZ,
    validation_status   TEXT DEFAULT 'pending',     -- 'valid', 'invalid', 'pending', 'error'
    validation_error    TEXT,
    
    -- Content hashing for change detection
    content_sha256      TEXT,
    content_size_bytes  INT,
    
    -- Timestamps
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    -- Prevent duplicate versions per provider
    UNIQUE (provider_id, version)
);

-- Index for finding preferred specs
CREATE INDEX IF NOT EXISTS idx_discovery_specs_preferred 
ON spec_silver.discovery_specs(provider_id, is_preferred) 
WHERE is_preferred = TRUE;

-- Index for validation status
CREATE INDEX IF NOT EXISTS idx_discovery_specs_validation 
ON spec_silver.discovery_specs(validation_status);

-- =============================================================================
-- spec_silver.discovery_categories: API categorization (many-to-many)
-- =============================================================================

CREATE TABLE IF NOT EXISTS spec_silver.discovery_categories (
    id                  BIGSERIAL PRIMARY KEY,
    name                TEXT NOT NULL UNIQUE,       -- e.g., "payments", "messaging", "storage"
    description         TEXT,
    parent_category_id  BIGINT REFERENCES spec_silver.discovery_categories(id),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS spec_silver.discovery_provider_categories (
    provider_id         BIGINT NOT NULL REFERENCES spec_silver.discovery_providers(id) ON DELETE CASCADE,
    category_id         BIGINT NOT NULL REFERENCES spec_silver.discovery_categories(id) ON DELETE CASCADE,
    PRIMARY KEY (provider_id, category_id)
);

-- =============================================================================
-- spec_silver.discovery_aliases: Alternative names and common misspellings
-- =============================================================================

CREATE TABLE IF NOT EXISTS spec_silver.discovery_aliases (
    id                  BIGSERIAL PRIMARY KEY,
    provider_id         BIGINT NOT NULL REFERENCES spec_silver.discovery_providers(id) ON DELETE CASCADE,
    alias               TEXT NOT NULL,              -- e.g., "twilio", "sendgrid" for same provider
    alias_type          TEXT NOT NULL DEFAULT 'name', -- 'name', 'domain', 'abbreviation', 'misspelling'
    priority            INT NOT NULL DEFAULT 0,     -- Higher = more relevant
    UNIQUE (provider_id, alias)
);

CREATE INDEX IF NOT EXISTS idx_discovery_aliases_lookup 
ON spec_silver.discovery_aliases(LOWER(alias));

-- =============================================================================
-- spec_silver.discovery_refresh_log: Track sync operations
-- =============================================================================

CREATE TABLE IF NOT EXISTS spec_silver.discovery_refresh_log (
    id                  BIGSERIAL PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    source              TEXT NOT NULL,              -- 'apis_guru', 'curated_json', 'manual'
    status              TEXT NOT NULL DEFAULT 'running', -- 'running', 'completed', 'failed'
    providers_added     INT DEFAULT 0,
    providers_updated   INT DEFAULT 0,
    specs_added         INT DEFAULT 0,
    specs_updated       INT DEFAULT 0,
    error_message       TEXT,
    metadata            JSONB NOT NULL DEFAULT '{}'
);

-- =============================================================================
-- Helper function: Update search_text on provider insert/update
-- =============================================================================

CREATE OR REPLACE FUNCTION spec_silver.update_discovery_provider_search_text()
RETURNS TRIGGER AS $$
BEGIN
    NEW.search_text := COALESCE(NEW.display_name, '') || ' ' ||
                       COALESCE(NEW.domain, '') || ' ' ||
                       COALESCE(NEW.slug, '') || ' ' ||
                       COALESCE(NEW.description, '');
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Attach trigger to providers table
DROP TRIGGER IF EXISTS trg_discovery_provider_search_text ON spec_silver.discovery_providers;
CREATE TRIGGER trg_discovery_provider_search_text
BEFORE INSERT OR UPDATE ON spec_silver.discovery_providers
FOR EACH ROW
EXECUTE FUNCTION spec_silver.update_discovery_provider_search_text();

-- =============================================================================
-- Seed data: Common categories
-- =============================================================================

INSERT INTO spec_silver.discovery_categories (name, description) VALUES
    ('payments', 'Payment processing and financial transactions'),
    ('messaging', 'SMS, email, and push notifications'),
    ('storage', 'File storage and CDN'),
    ('authentication', 'Identity and access management'),
    ('analytics', 'Data analytics and tracking'),
    ('social', 'Social media integration'),
    ('ecommerce', 'E-commerce and retail'),
    ('communication', 'Real-time communication (voice, video)'),
    ('crm', 'Customer relationship management'),
    ('infrastructure', 'Cloud infrastructure and DevOps'),
    ('ai', 'Artificial intelligence and ML'),
    ('search', 'Search engines and indexing'),
    ('mapping', 'Maps and geolocation'),
    ('weather', 'Weather data and forecasts'),
    ('news', 'News and content aggregation')
ON CONFLICT (name) DO NOTHING;

-- =============================================================================
-- Migration cleanup: Drop old spec_catalog schema if it exists
-- This handles the transition from the initial implementation
-- =============================================================================

-- Drop tables in dependency order
DROP TABLE IF EXISTS spec_catalog.provider_categories CASCADE;
DROP TABLE IF EXISTS spec_catalog.aliases CASCADE;
DROP TABLE IF EXISTS spec_catalog.specs CASCADE;
DROP TABLE IF EXISTS spec_catalog.categories CASCADE;
DROP TABLE IF EXISTS spec_catalog.catalog_refresh_log CASCADE;
DROP TABLE IF EXISTS spec_catalog.providers CASCADE;
DROP FUNCTION IF EXISTS spec_catalog.update_provider_search_text() CASCADE;
DROP SCHEMA IF EXISTS spec_catalog CASCADE;

-- =============================================================================
-- Comments for documentation
-- =============================================================================

COMMENT ON TABLE spec_silver.discovery_providers IS 'API providers with semantic search support for auto-discovery';
COMMENT ON TABLE spec_silver.discovery_specs IS 'Individual API spec versions and validation status';
COMMENT ON TABLE spec_silver.discovery_categories IS 'API categorization for faceted search';
COMMENT ON TABLE spec_silver.discovery_aliases IS 'Alternative names and common misspellings for providers';
COMMENT ON COLUMN spec_silver.discovery_providers.embedding IS 'pgvector embedding for semantic similarity search (1536 dimensions for text-embedding-3-small)';
COMMENT ON COLUMN spec_silver.discovery_providers.quality_tier IS 'Quality tier: premium (curated), standard (auto-imported), untrusted (user-added)';

-- =============================================================================
-- Post-migration: Run ANALYZE for planner statistics
-- =============================================================================

ANALYZE spec_silver.discovery_providers;
ANALYZE spec_silver.discovery_specs;
ANALYZE spec_silver.discovery_categories;
ANALYZE spec_silver.discovery_aliases;
