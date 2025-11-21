"""
Configuration module for the integration coworker.

Provides configuration for LLMs, embeddings, and other runtime settings.
"""
import os
from typing import Dict, Any, Optional


def get_llm_config(task_type: str = "default") -> Dict[str, Any]:
    """
    Get LLM configuration for a specific task type.
    
    Args:
        task_type: Type of task (e.g., "extraction", "generation", "analysis")
    
    Returns:
        Configuration dictionary with model, temperature, etc.
    """
    # Default configuration
    config = {
        "model": os.getenv("LLM_MODEL", "gpt-4"),
        "temperature": 0.7,
        "max_tokens": 2000,
    }
    
    # Task-specific overrides
    if task_type == "extraction":
        config["temperature"] = 0.3  # Lower temperature for extraction
        config["max_tokens"] = 1500
    elif task_type == "generation":
        config["temperature"] = 0.7
        config["max_tokens"] = 3000
    elif task_type == "analysis":
        config["temperature"] = 0.5
        config["max_tokens"] = 2000
    
    return config


def get_embedding_config() -> Dict[str, Any]:
    """
    Get embedding configuration.
    
    Returns:
        Configuration for embedding model (OpenAI, etc.)
    """
    return {
        "model": os.getenv("EMBEDDING_MODEL", "text-embedding-ada-002"),
        "dimensions": 1536,
        "batch_size": 100,
    }


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
