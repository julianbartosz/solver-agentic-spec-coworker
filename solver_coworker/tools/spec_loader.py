"""
Spec Loader Tool

Utility functions to read spec files from various formats.
"""

import json
from pathlib import Path

import yaml

from solver_coworker.logging import get_logger

logger = get_logger(__name__)


def load_specs_from_dir(path: str) -> list[str]:
    """
    Load API specification files from a directory.

    Supports multiple formats: JSON, YAML, and Markdown.

    Args:
        path: Path to the directory containing spec files

    Returns:
        List of raw text contents from all spec files
    """
    specs: list[str] = []
    dir_path = Path(path)

    if not dir_path.exists():
        logger.warning(f"Directory does not exist: {path}")
        return specs

    if not dir_path.is_dir():
        logger.error(f"Path is not a directory: {path}")
        return specs

    # Supported file extensions
    supported_extensions = {".json", ".yaml", ".yml", ".md", ".txt"}

    for file_path in dir_path.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in supported_extensions:
            content = load_spec_file(file_path)
            if content:
                specs.append(content)

    logger.info(f"Loaded {len(specs)} spec files from {path}")
    return specs


def load_spec_file(file_path: Path) -> str | None:
    """
    Load a single spec file.

    Args:
        file_path: Path to the spec file

    Returns:
        Raw text content of the file, or None if loading failed
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        logger.debug(f"Loaded spec file: {file_path.name}")
        return content
    except Exception as e:
        logger.error(f"Failed to load spec file {file_path.name}: {e}")
        return None


def parse_json_spec(content: str) -> dict | None:
    """
    Parse a JSON specification.

    Args:
        content: Raw JSON string

    Returns:
        Parsed JSON as dictionary, or None if parsing failed
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse JSON spec: {e}")
        return None


def parse_yaml_spec(content: str) -> dict | None:
    """
    Parse a YAML specification.

    Args:
        content: Raw YAML string

    Returns:
        Parsed YAML as dictionary, or None if parsing failed
    """
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse YAML spec: {e}")
        return None
