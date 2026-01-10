"""
Configuration module for the integration coworker.

Provides configuration for LLMs, embeddings, database, and other runtime settings.
Reads from models.yaml per Appendix E.2.
Reads node archetypes from archetypes/*.archetype.yaml per Section 5.5.

Per design doc Section 4.3, configuration supports:
- DATABASE_URL for Postgres connection (primary path)
- LLM provider settings (API key, base URL, model)
- Embedding settings for pgvector (1536 dimensions)
- Multi-provider support (OpenAI, Anthropic)

Environment Variables:
- DATABASE_URL: Postgres connection string (e.g., postgresql://user:pass@host:5432/db)
- USE_SQLITE: Set to "true" to use SQLite for tests (fallback)
- OPENAI_API_KEY: API key for OpenAI/Azure OpenAI
- ANTHROPIC_API_KEY: API key for Anthropic Claude models
- LLM_BASE_URL: Base URL for LLM provider (optional, for Azure or custom endpoints)
- LLM_MODEL: Override default model
- LLM_PROVIDER: Default provider (openai, anthropic)
- USE_MOCK_LLM: Set to "true" to use mock LLM for tests
"""
import os
import yaml
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Any, Optional, Literal
from urllib.parse import urlparse

from integration_coworker.config.llm_mode import LLMMode, get_llm_mode, reset_llm_mode

# H-4: Bounded archetype cache using @lru_cache
# Removed manual _ARCHETYPE_CACHE in favor of lru_cache(maxsize=100)
# This bounds memory usage and provides automatic eviction


def _get_pattern_learning_default() -> bool:
    """
    Get default value for pattern_learning_enabled based on profile.
    
    Priority:
    1. Explicit PATTERN_LEARNING_ENABLED env var (always wins)
    2. CODEGEN_PROFILE-based default (production=true, development=false)
    3. Fallback to false
    
    Per ADR-0005: Pattern learning defaults to OFF. Use CODEGEN_PROFILE=production
    or explicit env var to enable.
    """
    # Explicit env var takes precedence
    explicit = os.getenv("PATTERN_LEARNING_ENABLED")
    if explicit is not None:
        return explicit.lower() in ("true", "1", "yes")
    
    # Profile-based default
    profile_name = os.getenv("CODEGEN_PROFILE", "development").lower().strip()
    if profile_name == "production":
        return True
    
    # Default: disabled
    return False


# =============================================================================
# V2: Scoring Weight Configuration (per Section 3.4)
# =============================================================================

# Default scoring weights for hybrid GraphRAG scoring
DEFAULT_SCORING_WEIGHTS: Dict[str, float] = {
    "graph": 0.4,
    "embedding": 0.4,
    "exact_match": 0.2,
}

# Provider-specific overrides (learned/tuned over time)
#
# NOTE: These overrides are intentionally empty today. The scoring logic and
# unit tests assume provider-agnostic base weights, with provider influence
# expressed via explicit match bonuses (e.g., graph_score), not by changing
# the weights. Reintroduce overrides only with corresponding test updates.
PROVIDER_SCORING_WEIGHTS: Dict[str, Dict[str, float]] = {}


def get_scoring_weights(provider_code: Optional[str] = None) -> Dict[str, float]:
    """
    Get scoring weights for hybrid GraphRAG scoring.
    
    V2: Per-provider configurable weights for graph, embedding, and exact-match scoring.
    
    Args:
        provider_code: Optional provider code for provider-specific weights
        
    Returns:
        Dict with "graph", "embedding", and "exact_match" weights (sum to 1.0)
    """
    if provider_code and provider_code in PROVIDER_SCORING_WEIGHTS:
        return PROVIDER_SCORING_WEIGHTS[provider_code]
    return DEFAULT_SCORING_WEIGHTS.copy()


@dataclass
class DatabaseConfig:
    """Database configuration with Postgres as primary, SQLite for tests."""

    # Postgres connection URL (primary path per design doc)
    url: str = field(default_factory=lambda: os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/integration_coworker"
    ))

    # SQLite fallback for tests
    use_sqlite: bool = field(default_factory=lambda: os.getenv("USE_SQLITE", "").lower() == "true")
    sqlite_path: str = field(default_factory=lambda: os.getenv(
        "SQLITE_PATH",
        str(Path(__file__).parent.parent.parent.parent / ".data" / "integration_coworker.sqlite3")
    ))

    @property
    def is_postgres(self) -> bool:
        """Check if using Postgres (primary path)."""
        return not self.use_sqlite and self.url.startswith("postgresql")

    @property
    def engine_type(self) -> Literal["postgres", "sqlite"]:
        """Get the database engine type."""
        return "sqlite" if self.use_sqlite else "postgres"


@dataclass
class LLMConfig:
    """LLM provider configuration."""

    # API key for the LLM provider
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))

    # Base URL (for Azure OpenAI or custom endpoints)
    base_url: Optional[str] = field(default_factory=lambda: os.getenv("LLM_BASE_URL"))

    # Default model (can be overridden per task type)
    default_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4"))

    # LLM Mode (replaces use_mock boolean) - see LLM-003
    mode: LLMMode = field(default_factory=get_llm_mode)

    # Embedding model (used by KG and embeddings)
    _embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))

    # ==========================================================================
    # Concurrency Control (Parallelization V2)
    # 
    # Controls how many LLM API requests can run concurrently to prevent
    # rate limit bursts when using parallel workflows.
    #
    # Environment Variables:
    #   LLM_MAX_CONCURRENT: Maximum concurrent LLM requests (default: 5)
    #   LLM_ACQUIRE_TIMEOUT: Timeout for slot acquisition in seconds (default: 30)
    # ==========================================================================
    
    max_concurrent: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_CONCURRENT", "5"))
    )
    
    acquire_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("LLM_ACQUIRE_TIMEOUT", "30.0"))
    )

    @property
    def use_mock(self) -> bool:
        """Legacy compatibility: Check if in mock mode."""
        return self.mode.is_mock

    @property
    def is_configured(self) -> bool:
        """Check if LLM is properly configured for real calls."""
        return bool(self.api_key) and self.mode.is_real

    @property
    def embedding_model(self) -> str:
        """Get the embedding model name (for KG and embedding nodes)."""
        return self._embedding_model


@dataclass
class CacheConfig:
    """
    Redis LLM cache configuration (Plan 7).
    
    Environment Variables:
        REDIS_URL: Redis connection URL (default: redis://localhost:6379/0)
        LLM_CACHE_ENABLED: Enable/disable caching (default: true)
        LLM_CACHE_TTL: Cache TTL in seconds (default: 86400 = 24h)
    """
    
    # Redis connection URL
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    
    # Enable/disable caching
    enabled: bool = field(
        default_factory=lambda: os.getenv("LLM_CACHE_ENABLED", "true").lower() in ("true", "1", "yes", "on")
    )
    
    # Cache TTL in seconds (default 24 hours)
    ttl: int = field(default_factory=lambda: int(os.getenv("LLM_CACHE_TTL", "86400")))
    
    @property
    def is_enabled(self) -> bool:
        """Check if caching is enabled."""
        return self.enabled


@dataclass
class EmbeddingConfig:
    """Embedding configuration for pgvector."""

    model: str = "text-embedding-3-small"
    dimensions: int = 1536  # Must match pgvector VECTOR(1536)
    batch_size: int = 100
    max_parallel: int = 4


@dataclass
class Settings:
    """
    Central settings container per design doc Section 4.3.
    
    Aggregates all configuration with sensible defaults and env var overrides.
    """

    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    # KG / embeddings
    kg_compute_field_embeddings: bool = field(
        default_factory=lambda: os.getenv("KG_COMPUTE_FIELD_EMBEDDINGS", "false").lower() in ("true", "1", "yes", "on")
    )
    cache: CacheConfig = field(default_factory=CacheConfig)

    # HTTP client settings
    http_timeout: int = field(default_factory=lambda: int(os.getenv("HTTP_TIMEOUT", "30")))
    http_max_retries: int = field(default_factory=lambda: int(os.getenv("HTTP_MAX_RETRIES", "3")))
    http_retry_backoff: float = field(default_factory=lambda: float(os.getenv("HTTP_RETRY_BACKOFF", "1.0")))

    # V3 Streaming Persistence (reduces memory from 200MB+ to <20MB for large specs)
    # When enabled:
    # - ingest_spec streams chunks to DB immediately, clears state.doc_chunks
    # - embed_spec_chunks lazy loads chunks, streams embeddings to DB
    # - WorkflowState holds only IDs and counts, not full content
    # 
    # Modes:
    # - "auto" (default): Automatically enable for large specs (>500KB or >500 chunks)
    # - "true"/"on": Always enable streaming
    # - "false"/"off": Always disable streaming (legacy mode)
    streaming_persistence: str = field(
        default_factory=lambda: os.getenv("STREAMING_PERSISTENCE", "auto").lower()
    )
    
    # Thresholds for auto-streaming (when streaming_persistence="auto")
    streaming_threshold_bytes: int = field(
        default_factory=lambda: int(os.getenv("STREAMING_THRESHOLD_BYTES", "500000"))  # 500KB
    )
    streaming_threshold_chunks: int = field(
        default_factory=lambda: int(os.getenv("STREAMING_THRESHOLD_CHUNKS", "500"))  # 500 chunks
    )
    
    # ==========================================================================
    # Remote Spec Fetch Configuration (V4: Production Hardening)
    # Per docs/INGESTION_PROD_HARDENING_PLAN.md
    #
    # Controls HTTP fetch behavior for remote specs (retries, size limits, 
    # content-type validation) to ensure production reliability.
    # ==========================================================================
    
    # Maximum spec size in bytes (prevents OOM from malicious/corrupt specs)
    fetch_max_bytes: int = field(
        default_factory=lambda: int(os.getenv("FETCH_MAX_BYTES", "52428800"))  # 50MB
    )
    
    # Threshold for streaming fetch (below this, use single GET; above, stream)
    fetch_stream_threshold: int = field(
        default_factory=lambda: int(os.getenv("FETCH_STREAM_THRESHOLD", "1048576"))  # 1MB
    )
    
    # HTTP status codes that trigger retry (comma-separated)
    fetch_retryable_statuses: str = field(
        default_factory=lambda: os.getenv("FETCH_RETRYABLE_STATUSES", "429,500,502,503,504")
    )
    
    # Allowed content types for spec files (comma-separated)
    # Includes standard formats + OpenAPI media types:
    #   - Canonical: application/openapi+json, application/openapi+yaml
    #     (per IETF HTTPAPI draft: draft-ietf-httpapi-rest-api-mediatypes)
    #   - Deprecated aliases (widely used but not IANA registered):
    #     application/vnd.oai.openapi, application/vnd.oai.openapi+json
    fetch_allowed_content_types: str = field(
        default_factory=lambda: os.getenv(
            "FETCH_ALLOWED_CONTENT_TYPES",
            "application/json,application/yaml,text/yaml,text/plain,application/xml,text/xml,application/x-yaml,application/openapi+json,application/openapi+yaml,application/vnd.oai.openapi,application/vnd.oai.openapi+json"
        )
    )

    # ==========================================================================
    # Dynamic Pattern Learning (PL-001)
    # Per docs/PATTERN_LEARNING_DESIGN.md
    # 
    # ADR-0005 COMPLIANCE: Pattern learning default reverted to OFF.
    # Use CODEGEN_PROFILE=production to enable pattern learning, or set
    # PATTERN_LEARNING_ENABLED=true explicitly.
    # 
    # The profile-aware default:
    # - development (default): pattern_learning_enabled=false
    # - production: pattern_learning_enabled=true
    # 
    # Explicit env var always takes precedence over profile.
    # ==========================================================================
    
    # Master switch - enables/disables all pattern learning features
    # Default: false (use CODEGEN_PROFILE=production or explicit env var to enable)
    # Note: AUTO_PROMOTE remains false - patterns require manual promotion
    pattern_learning_enabled: bool = field(
        default_factory=lambda: _get_pattern_learning_default()
    )
    
    # Granular control: capture workflow events to kg_run_events
    # Requires pattern_learning_enabled=true
    pattern_capture_events: bool = field(
        default_factory=lambda: os.getenv("PATTERN_CAPTURE_EVENTS", "true").lower() in ("true", "1", "yes")
    )
    
    # Granular control: run pattern discovery on event logs
    # Requires pattern_learning_enabled=true
    pattern_discover_candidates: bool = field(
        default_factory=lambda: os.getenv("PATTERN_DISCOVER_CANDIDATES", "true").lower() in ("true", "1", "yes")
    )
    
    # Granular control: auto-promote candidates to learned patterns
    # Requires pattern_learning_enabled=true AND pattern_discover_candidates=true
    pattern_auto_promote: bool = field(
        default_factory=lambda: os.getenv("PATTERN_AUTO_PROMOTE", "false").lower() in ("true", "1", "yes")
    )
    
    # Granular control: use learned patterns during alignment
    # Requires pattern_learning_enabled=true
    pattern_match_learned: bool = field(
        default_factory=lambda: os.getenv("PATTERN_MATCH_LEARNED", "true").lower() in ("true", "1", "yes")
    )
    
    # Minimum support count for auto-promoting a pattern candidate
    pattern_promotion_threshold: int = field(
        default_factory=lambda: int(os.getenv("PATTERN_PROMOTION_THRESHOLD", "3"))
    )
    
    # Minimum average feedback score for promotion (0.0-1.0)
    pattern_min_feedback_score: float = field(
        default_factory=lambda: float(os.getenv("PATTERN_MIN_FEEDBACK_SCORE", "0.6"))
    )
    
    # Decay factor for unused pattern confidence (applied per discovery cycle)
    pattern_confidence_decay: float = field(
        default_factory=lambda: float(os.getenv("PATTERN_CONFIDENCE_DECAY", "0.95"))
    )
    
    # ==========================================================================
    # Artifact Storage Configuration
    # Per Agent Harness Alignment Plan
    # 
    # ARTIFACT_ROOT: Base path for artifact storage (large checkpoint fields).
    # - Development: Defaults to project-local .artifacts/ directory
    # - Service mode: MUST point to a durable filesystem (mounted volume)
    # - Cloud portability: Keep this as filesystem path; object storage can be
    #   added as a backend implementation behind the stable ArtifactStore interface
    # ==========================================================================
    
    artifact_root: str = field(
        default_factory=lambda: os.getenv(
            "ARTIFACT_ROOT",
            str(Path(__file__).parent.parent.parent.parent / ".artifacts")
        )
    )
    
    # Enable artifact compression (gzip)
    artifact_compress: bool = field(
        default_factory=lambda: os.getenv("ARTIFACT_COMPRESS", "true").lower() in ("true", "1", "yes")
    )

    # ==========================================================================
    # Multi-Language Sandbox Configuration
    # Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.4
    #
    # Controls execution tier for TypeScript/Go validation:
    # - MULTILANG_TIER: "exp" (default) or "prod"
    #   - exp: Host toolchain execution (non-deterministic, for development)
    #   - prod: Docker 2-phase execution (deterministic, for CI/production)
    # - MULTILANG_USE_DOCKER: "true" or "false"
    #   - Must be "true" when tier="prod"
    # - MULTILANG_NETWORK_NONE: "true" (default) - validate phase uses --network none
    #
    # INVARIANTS:
    # - Tier is NEVER auto-selected from Docker availability
    # - tier=prod + use_docker=false = startup error
    # - tier=prod + Docker unavailable = runtime error (no silent downgrade)
    # ==========================================================================
    
    # Sandbox execution tier: "exp" (host) or "prod" (Docker)
    multilang_tier: str = field(
        default_factory=lambda: os.getenv("MULTILANG_TIER", "exp").lower()
    )
    
    # Enable Docker execution (must be True for tier=prod)
    multilang_use_docker: bool = field(
        default_factory=lambda: os.getenv("MULTILANG_USE_DOCKER", "false").lower() in ("true", "1", "yes")
    )
    
    # Use --network none for validate phase (isolation)
    multilang_network_none: bool = field(
        default_factory=lambda: os.getenv("MULTILANG_NETWORK_NONE", "true").lower() in ("true", "1", "yes")
    )

    # ==========================================================================
    # Production E2E LLM Controls
    # 
    # Hard caps to prevent runaway LLM costs and unbounded execution time.
    # When caps are exceeded, fail fast with clear error.
    # ==========================================================================
    
    # Enable real LLM calls in e2e tests (default: False for CI safety)
    prod_e2e_real_llm: bool = field(
        default_factory=lambda: os.getenv("PROD_E2E_REAL_LLM", "false").lower() in ("true", "1", "yes")
    )
    
    # Maximum LLM calls per workflow run (default: 100)
    # Exceeding this raises LLMBudgetExceededError
    prod_e2e_max_llm_calls: int = field(
        default_factory=lambda: int(os.getenv("PROD_E2E_MAX_LLM_CALLS", "100"))
    )
    
    # Per-spec processing timeout in seconds (default: 120)
    prod_e2e_spec_timeout: int = field(
        default_factory=lambda: int(os.getenv("PROD_E2E_SPEC_TIMEOUT", "120"))
    )
    
    # Total wall-clock timeout for e2e run in seconds (default: 600)
    prod_e2e_total_timeout: int = field(
        default_factory=lambda: int(os.getenv("PROD_E2E_TOTAL_TIMEOUT", "600"))
    )

    # ==========================================================================
    # Spec Auto-Discovery Configuration (Slice 1)
    # Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md
    #
    # Enables natural language task descriptions to auto-resolve specs via
    # APIs.guru registry lookup. Feature flag controlled.
    # ==========================================================================
    
    # Master switch for spec discovery (default: False in Slice 1)
    discovery_enabled: bool = field(
        default_factory=lambda: os.getenv("DISCOVERY_ENABLED", "false").lower() in ("true", "1", "yes")
    )
    
    # Confidence threshold for HITL confirmation (below triggers user prompt)
    discovery_confirmation_threshold: float = field(
        default_factory=lambda: float(os.getenv("DISCOVERY_CONFIRMATION_THRESHOLD", "0.85"))
    )
    
    # Maximum time for discovery phase (seconds)
    discovery_timeout_seconds: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "30"))
    )
    
    # Cache TTL for APIs.guru directory (hours)
    discovery_cache_ttl_hours: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_CACHE_TTL_HOURS", "24"))
    )
    
    # Maximum candidates to evaluate
    discovery_max_candidates: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_MAX_CANDIDATES", "5"))
    )
    
    # pgvector HNSW search accuracy vs latency tradeoff
    # Higher values = better recall, slower queries. Default 40 is pgvector's default.
    # For production catalog (~10k entries): 40-100 is good balance.
    # See: https://github.com/pgvector/pgvector#hnsw
    discovery_hnsw_ef_search: int = field(
        default_factory=lambda: int(os.getenv("DISCOVERY_HNSW_EF_SEARCH", "40"))
    )
    
    # Allow weak validation when openapi-spec-validator is missing
    # If False and validator is missing, discovery fails fast with actionable error
    discovery_allow_weak_validation: bool = field(
        default_factory=lambda: os.getenv("DISCOVERY_ALLOW_WEAK_VALIDATION", "false").lower() in ("true", "1", "yes")
    )
    
    # ==========================================================================
    # Slice 3: LLM-Enhanced Intent Extraction
    # Per docs/plans/SPEC_AUTO_DISCOVERY_PLAN.md Section Slice 3
    # ==========================================================================
    
    # Enable LLM-based intent extraction (more accurate than heuristics)
    # LLM output is ADVISORY ONLY - never trust URLs from LLM
    discovery_llm_intent_enabled: bool = field(
        default_factory=lambda: os.getenv("DISCOVERY_LLM_INTENT_ENABLED", "false").lower() in ("true", "1", "yes")
    )

    @classmethod
    def from_env(cls) -> "Settings":
        """Create settings from environment variables."""
        return cls()

    def validate(self) -> list[str]:
        """
        Validate settings and return list of warnings/errors.
        
        Returns empty list if all settings are valid for production use.
        """
        warnings = []

        if not self.database.is_postgres:
            warnings.append("Using SQLite instead of Postgres (not recommended for production)")

        if not self.llm.is_configured:
            if self.llm.use_mock:
                warnings.append("Using mock LLM (set OPENAI_API_KEY for real LLM)")
            else:
                warnings.append("OPENAI_API_KEY not set - LLM calls will fail")

        # Validate multilang tier configuration
        valid_tiers = ("exp", "prod")
        if self.multilang_tier not in valid_tiers:
            warnings.append(f"MULTILANG_TIER must be one of {valid_tiers}, got '{self.multilang_tier}'")
        
        # tier=prod requires use_docker=True
        if self.multilang_tier == "prod" and not self.multilang_use_docker:
            warnings.append(
                "MULTILANG_TIER=prod requires MULTILANG_USE_DOCKER=true. "
                "Set MULTILANG_USE_DOCKER=true or use MULTILANG_TIER=exp."
            )
        
        # Validate LLM budget caps
        if self.prod_e2e_max_llm_calls <= 0:
            warnings.append("PROD_E2E_MAX_LLM_CALLS must be positive")
        if self.prod_e2e_spec_timeout <= 0:
            warnings.append("PROD_E2E_SPEC_TIMEOUT must be positive")
        if self.prod_e2e_total_timeout <= 0:
            warnings.append("PROD_E2E_TOTAL_TIMEOUT must be positive")

        return warnings


# Global settings instance (lazy-loaded)
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def is_streaming_persistence_enabled() -> bool:
    """
    Check if streaming persistence is explicitly forced ON.
    
    V3 Feature: When enabled, large data (chunks, embeddings) is streamed to DB
    immediately instead of accumulated in WorkflowState. This reduces memory
    from 200MB+ to <20MB for large specs.
    
    Returns True only if STREAMING_PERSISTENCE=true/on (forced mode).
    For automatic mode, use should_use_streaming_for_spec() instead.
    
    Returns:
        True if streaming persistence is explicitly enabled
    """
    mode = get_settings().streaming_persistence
    return mode in ("true", "on", "1", "yes")


def is_streaming_persistence_disabled() -> bool:
    """
    Check if streaming persistence is explicitly forced OFF.
    
    Returns:
        True if streaming persistence is explicitly disabled
    """
    mode = get_settings().streaming_persistence
    return mode in ("false", "off", "0", "no")


def should_use_streaming_for_spec(total_content_bytes: int, estimated_chunks: int = 0) -> bool:
    """
    Determine if streaming should be used for a spec based on size.
    
    V3 Adaptive Streaming: Automatically enables streaming for large specs
    to prevent memory issues, while using faster in-memory mode for small specs.
    
    Args:
        total_content_bytes: Total size of spec content in bytes
        estimated_chunks: Estimated number of chunks (optional, for early decision)
    
    Returns:
        True if streaming should be used for this spec
    
    Decision logic:
    - STREAMING_PERSISTENCE=true/on → always stream
    - STREAMING_PERSISTENCE=false/off → never stream
    - STREAMING_PERSISTENCE=auto (default) → stream if spec exceeds thresholds
    """
    settings = get_settings()
    mode = settings.streaming_persistence
    
    # Explicit modes override auto-detection
    if mode in ("true", "on", "1", "yes"):
        return True
    if mode in ("false", "off", "0", "no"):
        return False
    
    # Auto mode: check thresholds
    if total_content_bytes >= settings.streaming_threshold_bytes:
        return True
    if estimated_chunks >= settings.streaming_threshold_chunks:
        return True
    
    return False


def reset_settings() -> None:
    """Reset settings (for testing)."""
    global _settings
    _settings = None
    reset_llm_mode()


def get_embedding_config() -> Dict[str, Any]:
    """
    Get embedding configuration.
    
    Returns:
        Configuration for embedding model (dimensions must match pgvector VECTOR(1536))
    """
    settings = get_settings()

    embedding_config = {
        "model": settings.embedding.model,
        "dimensions": settings.embedding.dimensions,
        "batch_size": settings.embedding.batch_size,
    }

    # Add API key for embedding calls
    embedding_config["api_key"] = settings.llm.api_key
    if settings.llm.base_url:
        embedding_config["base_url"] = settings.llm.base_url

    # Allow environment variable overrides
    if os.getenv("EMBEDDING_MODEL"):
        embedding_config["model"] = os.getenv("EMBEDDING_MODEL")

    return embedding_config


def get_db_config() -> Dict[str, Any]:
    """
    Get database configuration.
    
    Returns:
        Database connection parameters including URL for Postgres
    """
    settings = get_settings()

    if settings.database.use_sqlite:
        return {
            "engine": "sqlite",
            "path": settings.database.sqlite_path,
        }

    # Parse Postgres URL for backwards compatibility
    parsed = urlparse(settings.database.url)
    return {
        "engine": "postgres",
        "url": settings.database.url,
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/") if parsed.path else "integration_coworker",
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
    }


def get_http_client_config() -> Dict[str, Any]:
    """
    Get HTTP client configuration.
    
    Returns:
        Configuration for the integration HTTP client
    """
    settings = get_settings()
    return {
        "timeout": settings.http_timeout,
        "max_retries": settings.http_max_retries,
        "retry_backoff": settings.http_retry_backoff,
    }


# =============================================================================
# Remote Spec Fetch Configuration (V4: Production Hardening)
# =============================================================================

@dataclass
class FetchConfig:
    """
    Configuration for remote spec fetching with production hardening.
    
    Per docs/INGESTION_PROD_HARDENING_PLAN.md, this enables:
    - HTTP retry with exponential backoff on transient errors
    - Streaming fetch with size limits to prevent OOM
    - Content-type validation to reject non-spec responses
    
    All values are configurable via environment variables.
    """
    max_bytes: int = 52428800  # 50MB - maximum spec size
    stream_threshold: int = 1048576  # 1MB - threshold for streaming
    timeout: float = 30.0  # HTTP timeout in seconds
    max_retries: int = 3  # Number of retry attempts
    retry_backoff: float = 1.0  # Base backoff in seconds
    retryable_statuses: frozenset = field(default_factory=lambda: frozenset({429, 500, 502, 503, 504}))
    allowed_content_types: frozenset = field(default_factory=lambda: frozenset({
        # Standard formats
        "application/json", "application/yaml", "text/yaml",
        "text/plain", "application/xml", "text/xml", "application/x-yaml",
        # OpenAPI media types:
        #   - Canonical (per IETF HTTPAPI draft): application/openapi+json, +yaml
        #   - Deprecated aliases (widely used, not IANA registered): vnd.oai.openapi
        "application/openapi+json", "application/openapi+yaml",
        "application/vnd.oai.openapi", "application/vnd.oai.openapi+json",
    }))


def get_fetch_config() -> FetchConfig:
    """
    Get fetch configuration from settings.
    
    Creates a FetchConfig from current Settings, parsing comma-separated
    values for status codes and content types.
    
    Returns:
        FetchConfig with current settings
    """
    settings = get_settings()
    
    # Parse comma-separated retryable status codes
    retryable = frozenset(
        int(s.strip()) 
        for s in settings.fetch_retryable_statuses.split(",") 
        if s.strip()
    )
    
    # Parse comma-separated allowed content types
    allowed_ct = frozenset(
        ct.strip().lower() 
        for ct in settings.fetch_allowed_content_types.split(",") 
        if ct.strip()
    )
    
    return FetchConfig(
        max_bytes=settings.fetch_max_bytes,
        stream_threshold=settings.fetch_stream_threshold,
        timeout=float(settings.http_timeout),
        max_retries=settings.http_max_retries,
        retry_backoff=settings.http_retry_backoff,
        retryable_statuses=retryable,
        allowed_content_types=allowed_ct,
    )


def get_cache_config() -> Dict[str, Any]:
    """
    Get LLM cache configuration (Plan 7).
    
    Returns:
        Configuration for Redis LLM cache including:
        - redis_url: Connection URL
        - enabled: Whether caching is enabled
        - ttl: Cache TTL in seconds
    """
    settings = get_settings()
    return {
        "redis_url": settings.cache.redis_url,
        "enabled": settings.cache.enabled,
        "ttl": settings.cache.ttl,
    }


# =============================================================================
# Archetype Configuration (per design doc Section 5.5)
# =============================================================================

def _get_archetypes_dir() -> Path:
    """Get the path to the archetypes directory."""
    return Path(__file__).parent / "archetypes"


def _load_base_archetype() -> Dict[str, Any]:
    """Load the base archetype configuration."""
    base_file = _get_archetypes_dir() / "_base.archetype.yaml"
    if base_file.exists():
        with open(base_file, 'r') as f:
            return yaml.safe_load(f) or {}
    return {}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep merge two dictionaries, with override taking precedence.
    
    Args:
        base: Base dictionary
        override: Override dictionary (takes precedence)
        
    Returns:
        Merged dictionary
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@lru_cache(maxsize=100)
def _load_archetype_cached(node_name: str) -> tuple:
    """
    Internal cached archetype loader (H-4).
    
    Returns a tuple (for hashability) that gets converted back to dict.
    
    Note: This caches based on node_name only. Environment variable overrides
    (LLM_PROVIDER, LLM_MODEL, USE_MOCK_LLM) are applied after caching.
    To pick up env var changes, call reset_archetype_cache().
    """
    # Load base archetype
    base_config = _load_base_archetype()

    # Load node-specific archetype
    archetype_file = _get_archetypes_dir() / f"{node_name}.archetype.yaml"
    if archetype_file.exists():
        with open(archetype_file, 'r') as f:
            node_config = yaml.safe_load(f) or {}
    else:
        # No node-specific archetype, use base only
        node_config = {}

    # Merge base and node-specific config (without env overrides)
    merged = _deep_merge(base_config, node_config)
    
    # Return as frozen structure for cacheability
    import json
    return (json.dumps(merged, sort_keys=True),)


def load_archetype(node_name: str) -> Dict[str, Any]:
    """
    Load archetype configuration for a specific node.
    
    H-4: Now uses bounded @lru_cache(maxsize=100) for memory safety.
    
    ⚠️  IMPORTANT: Returns a DEEP COPY to prevent mutation of cached data.
    Callers can safely modify the returned dict without affecting other callers.
    
    Archetypes define:
    - Model configuration (provider, model name, temperature, max_tokens)
    - Prompting strategy (chain_of_thought, extraction, generation)
    - Retrieval settings (top_k, graph_radius, token_budget)
    - Input/output schemas in TOON format
    - Parallelism settings (num_samples, max_parallel)
    
    Args:
        node_name: Name of the node (e.g., "understand_task", "generate_code_and_tests")
        
    Returns:
        Merged archetype configuration (base + node-specific + env overrides).
        This is a FRESH deep copy - safe to mutate.
    """
    import copy
    import json
    
    # Get cached config (without env overrides)
    (json_str,) = _load_archetype_cached(node_name)
    merged = json.loads(json_str)

    # Apply environment variable overrides (not cached - allows runtime changes)
    merged = _apply_env_overrides(merged)

    # Return a DEEP COPY to prevent mutation of cached/shared data
    # This is critical for production safety - multiple callers may modify the result
    return copy.deepcopy(merged)


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply environment variable overrides to archetype config.
    
    Supports:
    - LLM_PROVIDER: Override model.provider (and reset model name if incompatible)
    - LLM_MODEL: Override model.name (only for matching provider - won't apply OpenAI model to Anthropic)
    - USE_MOCK_LLM: Force mock mode
    
    Note: LLM_MODEL is only applied if it matches the archetype's provider:
    - gpt-* models only apply to openai provider
    - claude-* models only apply to anthropic provider
    This prevents accidentally using wrong model with wrong provider.
    
    Bug #26 Fix: When LLM_PROVIDER changes the provider, the model name is reset
    to the new provider's default if the archetype's model is incompatible.
    """
    result = config.copy()
    
    # Default models for each provider
    PROVIDER_DEFAULT_MODELS = {
        "openai": "gpt-4o",
        "anthropic": "claude-sonnet-4-5-20250929",
        "google": "gemini-2.5-flash",
        "mock": "mock-model",
    }

    # Model overrides
    if "model" in result:
        model_config = result["model"].copy()
        original_provider = model_config.get("provider", "openai")
        current_provider = original_provider
        current_model = model_config.get("name", "")

        if os.getenv("LLM_PROVIDER"):
            new_provider = os.getenv("LLM_PROVIDER")
            model_config["provider"] = new_provider
            
            # Bug #26 Fix: Check if the current model is incompatible with the new provider
            # If so, reset to the new provider's default model
            if new_provider != original_provider:
                is_openai_model = current_model.startswith(("gpt-", "o1-", "text-"))
                is_anthropic_model = current_model.startswith("claude-")
                is_google_model = current_model.startswith("gemini-")
                
                # Determine if model is compatible with new provider
                model_compatible = (
                    (new_provider == "openai" and is_openai_model) or
                    (new_provider == "anthropic" and is_anthropic_model) or
                    (new_provider == "google" and is_google_model) or
                    (new_provider == "mock")  # Mock accepts any model
                )
                
                if not model_compatible:
                    # Reset to new provider's default model
                    default_model = PROVIDER_DEFAULT_MODELS.get(new_provider, "gpt-4o")
                    model_config["name"] = default_model
            
            current_provider = new_provider

        # Only apply LLM_MODEL if it matches the provider
        env_model = os.getenv("LLM_MODEL")
        if env_model:
            is_openai_model = env_model.startswith(("gpt-", "o1-", "text-"))
            is_anthropic_model = env_model.startswith("claude-")

            if current_provider == "openai" and is_openai_model:
                model_config["name"] = env_model
            elif current_provider == "anthropic" and is_anthropic_model:
                model_config["name"] = env_model
            elif current_provider == "mock":
                # Allow any model name for mock provider
                model_config["name"] = env_model
            # Otherwise, keep the archetype's model (don't cross-apply models)

        if os.getenv("USE_MOCK_LLM", "").lower() == "true":
            model_config["provider"] = "mock"

        result["model"] = model_config

    return result


def get_archetype_model_config(node_name: str) -> Dict[str, Any]:
    """
    Get model configuration from archetype for a specific node.
    
    Convenience function that extracts the model section from the archetype
    and adds API keys from environment.
    
    Args:
        node_name: Name of the node
        
    Returns:
        Model configuration dict with provider, model, temperature, max_tokens, api_key
    """
    archetype = load_archetype(node_name)
    model_config = archetype.get("model", {}).copy()

    # Add API keys based on provider
    provider = model_config.get("provider", "openai")

    if provider == "openai":
        model_config["api_key"] = os.getenv("OPENAI_API_KEY", "")
        base_url = os.getenv("LLM_BASE_URL")
        if base_url:
            model_config["base_url"] = base_url
    elif provider == "anthropic":
        model_config["api_key"] = os.getenv("ANTHROPIC_API_KEY", "")

    # Check for mock mode
    if os.getenv("USE_MOCK_LLM", "").lower() == "true":
        model_config["use_mock"] = True

    return model_config


def get_archetype_prompt_config(node_name: str) -> Dict[str, Any]:
    """
    Get prompting configuration from archetype for a specific node.
    
    Args:
        node_name: Name of the node
        
    Returns:
        Prompting configuration dict with strategy, system_template, etc.
    """
    archetype = load_archetype(node_name)
    return archetype.get("prompting", {})


def get_archetype_retrieval_config(node_name: str) -> Dict[str, Any]:
    """
    Get retrieval configuration from archetype for a specific node.
    
    Args:
        node_name: Name of the node
        
    Returns:
        Retrieval configuration dict with top_k, graph_radius, token_budget, sources
    """
    archetype = load_archetype(node_name)
    return archetype.get("retrieval", {})


def get_archetype_parallelism_config(node_name: str) -> Dict[str, Any]:
    """
    Get parallelism configuration from archetype for a specific node.
    
    Args:
        node_name: Name of the node
        
    Returns:
        Parallelism configuration dict with num_samples, max_parallel_samples, etc.
    """
    archetype = load_archetype(node_name)
    return archetype.get("parallelism", {})


def list_available_archetypes() -> list[str]:
    """
    List all available node archetypes.
    
    Returns:
        List of node names that have archetype files
    """
    archetypes_dir = _get_archetypes_dir()
    if not archetypes_dir.exists():
        return []

    archetypes = []
    for f in archetypes_dir.glob("*.archetype.yaml"):
        name = f.stem.replace(".archetype", "")
        if not name.startswith("_"):  # Skip _base
            archetypes.append(name)

    return sorted(archetypes)


def reset_archetype_cache() -> None:
    """
    Reset the archetype cache (for testing).
    
    H-4: Now clears the lru_cache instead of a manual dict.
    """
    _load_archetype_cached.cache_clear()
