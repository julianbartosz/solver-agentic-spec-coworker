"""
Configuration module for the integration coworker.

Provides configuration for LLMs, embeddings, and other runtime settings.
Reads from models.yaml per Appendix E.2.
"""
import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional

# Cache for loaded config
_CONFIG_CACHE: Optional[Dict[str, Any]] = None


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
    
    # Get task-specific config or fall back to defaults
    if task_type in llm_config:
        task_config = llm_config[task_type].copy()
    else:
        # Default configuration
        task_config = {
            "model": os.getenv("LLM_MODEL", "gpt-4"),
            "temperature": 0.7,
            "max_tokens": 2000,
        }
    
    # Allow environment variable overrides
    if os.getenv("LLM_MODEL"):
        task_config["model"] = os.getenv("LLM_MODEL")
    
    return task_config


def get_embedding_config() -> Dict[str, Any]:
    """
    Get embedding configuration.
    
    Returns:
        Configuration for embedding model (dimensions must match pgvector)
    """
    config = _load_config()
    embedding_config = config.get("embeddings", {
        "model": "text-embedding-3-small",
        "dimensions": 1536,
        "batch_size": 100,
    })
    
    # Allow environment variable overrides
    if os.getenv("EMBEDDING_MODEL"):
        embedding_config["model"] = os.getenv("EMBEDDING_MODEL")
    
    return embedding_config


def get_db_config() -> Dict[str, Any]:
    """
    Get database configuration.
    
    Returns:
        Database connection parameters
    """
    return {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "database": os.getenv("DB_NAME", "integration_coworker"),
        "user": os.getenv("DB_USER", "postgres"),
        "password": os.getenv("DB_PASSWORD", ""),
    }


def get_http_client_config() -> Dict[str, Any]:
    """
    Get HTTP client configuration.
    
    Returns:
        Configuration for the integration HTTP client
    """
    return {
        "timeout": int(os.getenv("HTTP_TIMEOUT", "30")),
        "max_retries": int(os.getenv("HTTP_MAX_RETRIES", "3")),
        "retry_backoff": float(os.getenv("HTTP_RETRY_BACKOFF", "1.0")),
    }
