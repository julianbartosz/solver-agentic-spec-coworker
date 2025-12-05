"""
LLM Mode Configuration (LLM-003)

Provides a unified enum for controlling LLM behavior across the codebase.
Replaces the fragmented use_mock boolean with a robust mode system.

Modes:
- REAL: Call OpenAI/Anthropic APIs
- MOCK: Return deterministic static strings (for tests)
- RECORD: Call Real APIs, save request/response to disk (for replay)
- REPLAY: Read from disk, fail if interaction not found

Environment Variable:
- LLM_MODE: Set to "real", "mock", "record", or "replay"
- USE_MOCK_LLM: Legacy compatibility - maps to MOCK mode if "true"

Per V2_IMPLEMENTATION_PLAN_SUPPLEMENT.md Section 5.
"""
import os
from enum import Enum
from typing import Optional


class LLMMode(str, Enum):
    """
    Unified LLM behavior control.
    
    This enum centralizes all LLM behavior switching, replacing ad-hoc
    boolean flags and enabling advanced modes for testing and debugging.
    """
    REAL = "real"       # Call OpenAI/Anthropic
    MOCK = "mock"       # Return static strings for testing
    RECORD = "record"   # Call Real, save interaction to disk
    REPLAY = "replay"   # Read from disk, fail if missing

    @classmethod
    def from_env(cls) -> "LLMMode":
        """
        Determine LLMMode from environment variables.
        
        Priority:
        1. LLM_MODE env var (explicit mode selection)
        2. USE_MOCK_LLM=true (legacy compatibility → MOCK)
        3. Default to REAL
        
        Returns:
            The resolved LLMMode
        """
        # Check explicit LLM_MODE first
        llm_mode_str = os.getenv("LLM_MODE", "").lower().strip()
        if llm_mode_str:
            try:
                return cls(llm_mode_str)
            except ValueError:
                valid = ", ".join(m.value for m in cls)
                raise ValueError(
                    f"Invalid LLM_MODE='{llm_mode_str}'. "
                    f"Valid values: {valid}"
                )
        
        # Legacy fallback: USE_MOCK_LLM=true → MOCK
        if os.getenv("USE_MOCK_LLM", "").lower() in ("true", "1", "yes"):
            return cls.MOCK
        
        # Default to REAL
        return cls.REAL

    @property
    def is_real(self) -> bool:
        """Check if this mode calls real APIs."""
        return self in (LLMMode.REAL, LLMMode.RECORD)

    @property
    def is_mock(self) -> bool:
        """Check if this mode uses mock responses."""
        return self == LLMMode.MOCK

    @property
    def should_record(self) -> bool:
        """Check if interactions should be saved to disk."""
        return self == LLMMode.RECORD

    @property
    def should_replay(self) -> bool:
        """Check if interactions should be read from disk."""
        return self == LLMMode.REPLAY


# Module-level cache
_cached_mode: Optional[LLMMode] = None


def get_llm_mode() -> LLMMode:
    """
    Get the current LLM mode (cached).
    
    Returns:
        The resolved LLMMode from environment or default
    """
    global _cached_mode
    if _cached_mode is None:
        _cached_mode = LLMMode.from_env()
    return _cached_mode


def reset_llm_mode() -> None:
    """Reset the cached LLM mode (for testing)."""
    global _cached_mode
    _cached_mode = None
