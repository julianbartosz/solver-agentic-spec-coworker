"""Structured logging configuration for integration_coworker.

Uses stdlib logging with extra fields for structured output.
When JSON_LOGS=1 env var is set, outputs JSON-formatted logs.

Per docs/BUCKET_2_NO_INTERPRETATION_PLAN.md Step 2
Production Readiness v4 - P0-1: Added RedactingFormatter for credential protection.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Pattern


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
                "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
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


class RedactingFormatter(StructuredFormatter):
    """Formatter that redacts API keys and sensitive credentials from logs.
    
    Production Readiness v4 - P0-1: Prevents credential leakage in logs.
    
    CRITICAL: This redacts in both the message AND exc_text (cached exception
    formatting). Without exc_text redaction, exceptions that contain credentials
    would leak them on subsequent log calls (LogRecord reuses exc_text).
    
    Redaction patterns:
    - OpenAI API keys: sk-... (48+ chars)
    - Anthropic API keys: sk-ant-... 
    - Generic API keys/tokens in headers or query params
    - Bearer tokens
    - Authorization headers
    """
    
    # Compiled patterns for credential redaction
    # Order matters: more specific patterns first
    REDACT_PATTERNS: List[Tuple[Pattern[str], str]] = [
        # OpenAI API key: sk- followed by alphanumeric (usually 48+ chars)
        (re.compile(r'sk-[a-zA-Z0-9]{20,}'), '[REDACTED:OPENAI_KEY]'),
        # Anthropic API key: sk-ant-...
        (re.compile(r'sk-ant-[a-zA-Z0-9\-]{20,}'), '[REDACTED:ANTHROPIC_KEY]'),
        # Google API key
        (re.compile(r'AIza[a-zA-Z0-9\-_]{35}'), '[REDACTED:GOOGLE_KEY]'),
        # Generic Bearer token (in headers)
        (re.compile(r'(?i)(bearer\s+)[a-zA-Z0-9\-_\.]{20,}'), r'\1[REDACTED:TOKEN]'),
        # Authorization header value (handles 'Authorization': 'Basic/Bearer ...')
        (re.compile(r"(?i)(['\"]?authorization['\"]?\s*[:=]\s*['\"]?(?:basic|bearer|digest)?\s*)[a-zA-Z0-9+/=\-_\.]{10,}"), r'\1[REDACTED]'),
        # API key in various formats (api_key, apikey, x-api-key, etc.)
        (re.compile(r'(?i)(api[_\-]?key[\s:=]+)[^\s,\]}"\']{8,}'), r'\1[REDACTED]'),
        # Generic token/secret patterns
        (re.compile(r'(?i)((?:access|secret|private)[_\-]?(?:token|key)[\s:=]+)[^\s,\]}"\']{10,}'), r'\1[REDACTED]'),
    ]
    
    def __init__(self, json_output: bool = False):
        super().__init__(json_output=json_output)
    
    def _redact(self, text: str) -> str:
        """Apply all redaction patterns to text."""
        if not text:
            return text
        for pattern, replacement in self.REDACT_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
    
    def format(self, record: logging.LogRecord) -> str:
        """Format record with credential redaction.
        
        Production Readiness v4 - P0-1 (IMPROVED):
        Uses snapshot/restore pattern to avoid mutating LogRecord state for
        other handlers that may process the same record.
        
        CRITICAL: We must redact in both msg AND exc_text.
        exc_text is cached by the logging module - if an exception contains
        credentials and we don't redact exc_text, subsequent logs from the
        same LogRecord will leak the credentials.
        """
        # Snapshot original values to restore after formatting
        # This prevents mutation bleed to other handlers
        orig_msg = record.msg
        orig_args = record.args
        orig_exc_text = record.exc_text
        
        try:
            # Redact the message (before getMessage() caches it)
            if record.msg and isinstance(record.msg, str):
                record.msg = self._redact(record.msg)
            
            # Redact any args that might contain credentials
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: self._redact(str(v)) if isinstance(v, str) else v 
                                  for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(
                        self._redact(str(a)) if isinstance(a, str) else a 
                        for a in record.args
                    )
            
            # CRITICAL: Redact exc_text (cached exception formatting)
            # Without this, exceptions containing credentials leak on reuse
            if record.exc_text:
                record.exc_text = self._redact(record.exc_text)
            
            # Also clear the cached message to force re-evaluation
            # (getMessage() caches in record.message)
            record.message = ""
            
            return super().format(record)
        finally:
            # Restore original values for other handlers
            record.msg = orig_msg
            record.args = orig_args
            record.exc_text = orig_exc_text


def configure_logging(
    level: int = logging.INFO,
    json_output: Optional[bool] = None,
    redact_credentials: bool = True,
) -> None:
    """Configure structured logging for the package.
    
    Args:
        level: Logging level (default: INFO)
        json_output: Force JSON output. If None, uses JSON_LOGS env var.
        redact_credentials: Enable credential redaction (default: True for production safety)
    """
    if json_output is None:
        json_output = os.environ.get("JSON_LOGS", "0") == "1"
    
    # Use RedactingFormatter by default in production
    if redact_credentials:
        formatter = RedactingFormatter(json_output=json_output)
    else:
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
    "RedactingFormatter",
    "configure_logging",
    "get_logger",
]
