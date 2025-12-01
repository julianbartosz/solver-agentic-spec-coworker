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

# Cache for loaded config
_CONFIG_CACHE: Optional[Dict[str, Any]] = None

# Cache for loaded archetypes
_ARCHETYPE_CACHE: Dict[str, Dict[str, Any]] = {}


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

    # Use mock LLM for tests
    use_mock: bool = field(default_factory=lambda: os.getenv("USE_MOCK_LLM", "").lower() == "true")

    # Embedding model (used by KG and embeddings)
    _embedding_model: str = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"))

    @property
    def is_configured(self) -> bool:
        """Check if LLM is properly configured for real calls."""
        return bool(self.api_key) and not self.use_mock

    @property
    def embedding_model(self) -> str:
        """Get the embedding model name (for KG and embedding nodes)."""
        return self._embedding_model


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

    # HTTP client settings
    http_timeout: int = field(default_factory=lambda: int(os.getenv("HTTP_TIMEOUT", "30")))
    http_max_retries: int = field(default_factory=lambda: int(os.getenv("HTTP_MAX_RETRIES", "3")))
    http_retry_backoff: float = field(default_factory=lambda: float(os.getenv("HTTP_RETRY_BACKOFF", "1.0")))

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


def reset_settings() -> None:
    """Reset settings (for testing)."""
    global _settings, _CONFIG_CACHE
    _settings = None
    _CONFIG_CACHE = None


def _load_config() -> Dict[str, Any]:
    """Load configuration from models.yaml."""
    global _CONFIG_CACHE

    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    # Find models.yaml relative to this file
    config_dir = Path(__file__).parent
    config_file = config_dir / "models.yaml"

    if config_file.exists():
        with open(config_file, 'r') as f:
            _CONFIG_CACHE = yaml.safe_load(f) or {}
    else:
        _CONFIG_CACHE = {}

    return _CONFIG_CACHE


def get_llm_config(task_type: str = "default") -> Dict[str, Any]:
    """
    Get LLM configuration for a specific task type.
    
    Args:
        task_type: Type of task (e.g., "planning", "extraction", "codegen")
    
    Returns:
        Configuration dictionary with model, temperature, etc.
    """
    config = _load_config()
    llm_config = config.get("llm", {})
    settings = get_settings()

    # Get task-specific config or fall back to defaults
    if task_type in llm_config:
        task_config = llm_config[task_type].copy()
    else:
        # Default configuration
        task_config = {
            "model": settings.llm.default_model,
            "temperature": 0.7,
            "max_tokens": 2000,
        }

    # Add provider settings
    task_config["api_key"] = settings.llm.api_key
    if settings.llm.base_url:
        task_config["base_url"] = settings.llm.base_url
    task_config["use_mock"] = settings.llm.use_mock

    # Allow environment variable overrides for model
    if os.getenv("LLM_MODEL"):
        task_config["model"] = os.getenv("LLM_MODEL")

    return task_config


def get_embedding_config() -> Dict[str, Any]:
    """
    Get embedding configuration.
    
    Returns:
        Configuration for embedding model (dimensions must match pgvector VECTOR(1536))
    """
    config = _load_config()
    settings = get_settings()

    embedding_config = config.get("embeddings", {
        "model": settings.embedding.model,
        "dimensions": settings.embedding.dimensions,
        "batch_size": settings.embedding.batch_size,
    })

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
    - LLM_PROVIDER: Override model.provider
    - LLM_MODEL: Override model.name (only for matching provider - won't apply OpenAI model to Anthropic)
    - USE_MOCK_LLM: Force mock mode
    
    Note: LLM_MODEL is only applied if it matches the archetype's provider:
    - gpt-* models only apply to openai provider
    - claude-* models only apply to anthropic provider
    This prevents accidentally using wrong model with wrong provider.
    """
    result = config.copy()

    # Model overrides
    if "model" in result:
        model_config = result["model"].copy()
        current_provider = model_config.get("provider", "openai")

        if os.getenv("LLM_PROVIDER"):
            model_config["provider"] = os.getenv("LLM_PROVIDER")
            current_provider = model_config["provider"]

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
