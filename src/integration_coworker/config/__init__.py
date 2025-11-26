"""
Configuration module for the integration coworker.

Provides configuration for LLMs, embeddings, database, and other runtime settings.
Reads from models.yaml per Appendix E.2.

Per design doc Section 4.3, configuration supports:
- DATABASE_URL for Postgres connection (primary path)
- LLM provider settings (API key, base URL, model)
- Embedding settings for pgvector (1536 dimensions)

Environment Variables:
- DATABASE_URL: Postgres connection string (e.g., postgresql://user:pass@host:5432/db)
- USE_SQLITE: Set to "true" to use SQLite for tests (fallback)
- OPENAI_API_KEY: API key for OpenAI/Azure OpenAI
- LLM_BASE_URL: Base URL for LLM provider (optional, for Azure or custom endpoints)
- LLM_MODEL: Override default model
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
    
    @property
    def is_configured(self) -> bool:
        """Check if LLM is properly configured for real calls."""
        return bool(self.api_key) and not self.use_mock


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
