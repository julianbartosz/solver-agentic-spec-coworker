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
from pathlib import Path
from typing import Dict, Any, Optional, Literal
from urllib.parse import urlparse

from integration_coworker.config.llm_mode import LLMMode, get_llm_mode, reset_llm_mode

# Cache for loaded archetypes
_ARCHETYPE_CACHE: Dict[str, Dict[str, Any]] = {}


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
PROVIDER_SCORING_WEIGHTS: Dict[str, Dict[str, float]] = {
    "stripe": {"graph": 0.5, "embedding": 0.3, "exact_match": 0.2},
    "github": {"graph": 0.3, "embedding": 0.5, "exact_match": 0.2},
    # Default applies to unknown providers
}


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
    # Dynamic Pattern Learning (PL-001)
    # Per docs/PATTERN_LEARNING_DESIGN.md
    # 
    # PRODUCTION NOTE: Pattern learning is OFF by default to avoid unexpected
    # storage growth and behavior changes. Enable granularly as needed.
    # ==========================================================================
    
    # Master switch - enables/disables all pattern learning features
    # Default: false (safe for production)
    pattern_learning_enabled: bool = field(
        default_factory=lambda: os.getenv("PATTERN_LEARNING_ENABLED", "false").lower() in ("true", "1", "yes")
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


def load_archetype(node_name: str) -> Dict[str, Any]:
    """
    Load archetype configuration for a specific node.
    
    Archetypes define:
    - Model configuration (provider, model name, temperature, max_tokens)
    - Prompting strategy (chain_of_thought, extraction, generation)
    - Retrieval settings (top_k, graph_radius, token_budget)
    - Input/output schemas in TOON format
    - Parallelism settings (num_samples, max_parallel)
    
    Args:
        node_name: Name of the node (e.g., "understand_task", "generate_code_and_tests")
        
    Returns:
        Merged archetype configuration (base + node-specific)
    """
    global _ARCHETYPE_CACHE

    if node_name in _ARCHETYPE_CACHE:
        return _ARCHETYPE_CACHE[node_name]

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

    # Merge base and node-specific config
    merged = _deep_merge(base_config, node_config)

    # Apply environment variable overrides
    merged = _apply_env_overrides(merged)

    _ARCHETYPE_CACHE[node_name] = merged
    return merged


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
    """Reset the archetype cache (for testing)."""
    global _ARCHETYPE_CACHE
    _ARCHETYPE_CACHE = {}
