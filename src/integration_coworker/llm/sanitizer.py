"""
Input sanitization for LLM prompts.

v2: Implements FT-SEC-002 from V2 Implementation Plan Section 3.9

This module provides input sanitization to prevent prompt injection attacks
by detecting and neutralizing suspicious patterns in user-provided content.
"""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Maximum input length (chars)
MAX_INPUT_LENGTH = 50_000

# Patterns that might indicate prompt injection attempts
SUSPICIOUS_PATTERNS = [
    r"ignore\s+(previous|above|all)\s+instructions",
    r"disregard\s+(previous|above|all)\s+instructions",
    r"forget\s+(everything|all)\s+(you|I)\s+(told|said)",
    r"new\s+instructions?:",
    r"system\s*prompt:",
    r"<\|.*?\|>",  # Special tokens
    r"\[INST\]",   # Llama tokens
    r"Human:",     # Claude tokens
    r"Assistant:", # Claude tokens
    r"<<SYS>>",    # Llama system tokens
    r"<</SYS>>",   # Llama system tokens
]


def sanitize_input(
    text: str,
    max_length: int = MAX_INPUT_LENGTH,
    strip_suspicious: bool = True,
) -> str:
    """
    Sanitize user input before including in LLM prompts.
    
    Args:
        text: Raw user input
        max_length: Maximum allowed length
        strip_suspicious: Whether to remove suspicious patterns
        
    Returns:
        Sanitized text
    """
    if not text:
        return ""
    
    # Truncate to max length
    if len(text) > max_length:
        logger.warning(f"Input truncated from {len(text)} to {max_length} chars")
        text = text[:max_length] + "... [truncated]"
    
    # Normalize encoding
    text = text.encode('utf-8', errors='ignore').decode('utf-8')
    
    # Strip suspicious patterns
    if strip_suspicious:
        for pattern in SUSPICIOUS_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                logger.warning(f"Stripped suspicious pattern matching: {pattern[:30]}...")
                text = re.sub(pattern, "[REDACTED]", text, flags=re.IGNORECASE)
    
    return text


def sanitize_spec_content(content: str) -> str:
    """
    Sanitize OpenAPI spec content.
    
    Less aggressive than general sanitization since specs
    may legitimately contain instruction-like text in descriptions.
    
    Args:
        content: Raw OpenAPI spec content
        
    Returns:
        Sanitized spec content
    """
    return sanitize_input(
        content,
        max_length=200_000,  # Specs can be large
        strip_suspicious=False,  # Don't strip from specs
    )


def sanitize_task_description(task: str) -> str:
    """
    Sanitize user task description.
    
    More aggressive since this is direct user input.
    
    Args:
        task: Raw task description from user
        
    Returns:
        Sanitized task description
    """
    return sanitize_input(
        task,
        max_length=10_000,
        strip_suspicious=True,
    )


def sanitize_code_context(code: str) -> str:
    """
    Sanitize code context before including in prompts.
    
    Code context may contain comments with suspicious patterns,
    so we only truncate but don't strip patterns.
    
    Args:
        code: Code content to sanitize
        
    Returns:
        Sanitized code content
    """
    return sanitize_input(
        code,
        max_length=100_000,  # Code can be lengthy
        strip_suspicious=False,  # Comments may look suspicious
    )


def detect_injection_attempt(text: str) -> bool:
    """
    Detect if text contains potential prompt injection patterns.
    
    This is a detection-only function for logging/alerting purposes.
    Does not modify the input.
    
    Args:
        text: Text to analyze
        
    Returns:
        True if suspicious patterns detected
    """
    if not text:
        return False
    
    for pattern in SUSPICIOUS_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    
    return False
