"""Structured logging configuration for integration_coworker.

Uses stdlib logging with extra fields for structured output.
When JSON_LOGS=1 env var is set, outputs JSON-formatted logs.

Per docs/BUCKET_2_NO_INTERPRETATION_PLAN.md Step 2
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, Optional


class StructuredFormatter(logging.Formatter):
    """Formatter that outputs JSON when JSON_LOGS=1, else human-readable.
    
    Extra fields passed via logger.info(..., extra={...}) are included
    in the structured output for filtering and alerting.
    """
    
    # Standard fields that are always present on LogRecord
    STANDARD_FIELDS = {
        'name', 'msg', 'args', 'levelname', 'levelno',
        'pathname', 'filename', 'module', 'lineno', 'funcName',
        'created', 'msecs', 'relativeCreated', 'thread',
        'threadName', 'processName', 'process', 'message',
        'exc_info', 'exc_text', 'stack_info', 'taskName',
    }
    
    def __init__(self, json_output: bool = False):
        super().__init__()
        self._json_output = json_output
    
    def format(self, record: logging.LogRecord) -> str:
        # Ensure message is populated
        record.message = record.getMessage()
        
        # Extract extra fields (anything not in standard set)
        extra: Dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key not in self.STANDARD_FIELDS:
                # Serialize non-trivial types
                if isinstance(value, (dict, list, tuple, set)):
                    extra[key] = value
                elif isinstance(value, (str, int, float, bool, type(None))):
                    extra[key] = value
                else:
                    extra[key] = str(value)
        
        if self._json_output:
            log_entry = {
                "timestamp": datetime.utcfromtimestamp(record.created).isoformat() + "Z",
                "level": record.levelname,
                "logger": record.name,
                "message": record.message,
            }
            # Merge extra fields at top level
            log_entry.update(extra)
            
            if record.exc_info:
                log_entry["exception"] = self.formatException(record.exc_info)
            return json.dumps(log_entry)
        else:
            # Human-readable format
            extra_str = ""
            if extra:
                # Format as key=value pairs, limit to most useful fields
                formatted_pairs = []
                for k, v in extra.items():
                    if isinstance(v, float):
                        formatted_pairs.append(f"{k}={v:.3f}")
                    elif isinstance(v, dict):
                        # Skip nested dicts in human format
                        continue
                    else:
                        formatted_pairs.append(f"{k}={v}")
                if formatted_pairs:
                    extra_str = " [" + " ".join(formatted_pairs) + "]"
            
            base = f"{record.levelname:8s} {record.name}: {record.message}"
            return base + extra_str


def configure_logging(
    level: int = logging.INFO,
    json_output: Optional[bool] = None,
) -> None:
    """Configure structured logging for the package.
    
    Args:
        level: Logging level (default: INFO)
        json_output: Force JSON output. If None, uses JSON_LOGS env var.
    """
    if json_output is None:
        json_output = os.environ.get("JSON_LOGS", "0") == "1"
    
    formatter = StructuredFormatter(json_output=json_output)
    
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    
    # Configure package logger
    logger = logging.getLogger("integration_coworker")
    logger.setLevel(level)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers = []
    logger.addHandler(handler)
    logger.propagate = False


def get_logger(name: str) -> logging.Logger:
    """Get a logger for the given name.
    
    Convenience function that ensures logger is under package namespace.
    
    Args:
        name: Logger name (will be prefixed with integration_coworker. if needed)
        
    Returns:
        Configured logger instance
    """
    if not name.startswith("integration_coworker"):
        name = f"integration_coworker.{name}"
    return logging.getLogger(name)


__all__ = [
    "StructuredFormatter",
    "configure_logging",
    "get_logger",
]
