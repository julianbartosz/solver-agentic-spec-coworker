"""
Ingestion Agent

Responsible for loading and parsing API specifications from various sources.
"""

from pathlib import Path

from solver_coworker.logging import get_logger

logger = get_logger(__name__)


def ingest_specs(source_dir: str) -> list[str]:
    """
    Ingest API specifications from a source directory.

    Reads all spec files from the provided directory and returns their
    raw text content.

    Args:
        source_dir: Path to the directory containing spec files

    Returns:
        List of raw text contents from spec files
    """
    specs: list[str] = []
    source_path = Path(source_dir)

    if not source_path.exists():
        logger.warning(f"Source directory does not exist: {source_dir}")
        return specs

    # Iterate through files in the directory
    for file_path in source_path.iterdir():
        if file_path.is_file():
            try:
                content = file_path.read_text(encoding="utf-8")
                specs.append(content)
                logger.info(f"Ingested spec file: {file_path.name}")
            except Exception as e:
                logger.error(f"Failed to read file {file_path.name}: {e}")

    logger.info(f"Ingested {len(specs)} spec files from {source_dir}")
    return specs
