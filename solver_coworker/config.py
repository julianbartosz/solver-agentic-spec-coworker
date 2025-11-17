"""
Configuration module for solver_coworker.

Loads environment variables and provides configuration settings.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env file if present
load_dotenv()


class Settings:
    """Configuration settings for the solver coworker."""

    def __init__(self) -> None:
        """Initialize settings from environment variables."""
        self.openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
        self.anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")

        # Base paths
        self.base_dir: Path = Path(__file__).parent.parent
        self.data_dir: Path = self.base_dir / "data"
        self.raw_data_dir: Path = self.data_dir / "raw"
        self.api_specs_dir: Path = self.raw_data_dir / "api_specs"
        self.processed_data_dir: Path = self.data_dir / "processed"
        self.models_dir: Path = self.data_dir / "models"

    @property
    def has_openai_key(self) -> bool:
        """Check if OpenAI API key is configured."""
        return bool(self.openai_api_key)

    @property
    def has_anthropic_key(self) -> bool:
        """Check if Anthropic API key is configured."""
        return bool(self.anthropic_api_key)


# Global settings instance
settings = Settings()


def get_settings() -> Settings:
    """
    Get the global settings instance.

    Returns:
        Settings object with configuration values
    """
    return settings
