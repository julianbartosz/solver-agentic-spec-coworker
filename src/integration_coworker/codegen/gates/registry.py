"""
Language strategy registry.

Maps language strings to LanguageStrategy implementations.
"""

from typing import Dict, Type, Optional
import logging

from integration_coworker.codegen.gates.base import LanguageStrategy

logger = logging.getLogger(__name__)

# Registry of language strategies
GATE_REGISTRY: Dict[str, Type[LanguageStrategy]] = {}


def register_strategy(language: str, strategy_class: Type[LanguageStrategy]) -> None:
    """
    Register a language strategy.
    
    Args:
        language: Language identifier (lowercase, e.g., "python", "typescript")
        strategy_class: LanguageStrategy subclass
    """
    language = language.lower()
    if language in GATE_REGISTRY:
        logger.warning(f"Overwriting existing strategy for {language}")
    GATE_REGISTRY[language] = strategy_class
    logger.debug(f"Registered strategy for {language}: {strategy_class.__name__}")


def get_strategy(language: str) -> Optional[Type[LanguageStrategy]]:
    """
    Get strategy class for a language.
    
    Args:
        language: Language identifier (case-insensitive)
        
    Returns:
        LanguageStrategy subclass or None if not registered
    """
    return GATE_REGISTRY.get(language.lower())


def list_languages() -> list:
    """List all registered languages."""
    return list(GATE_REGISTRY.keys())


def _auto_register() -> None:
    """
    Auto-register built-in strategies.
    
    Called when module is imported.
    """
    # Import strategies to trigger registration
    try:
        from integration_coworker.codegen.gates.python import PythonStrategy
        register_strategy("python", PythonStrategy)
    except ImportError as e:
        logger.debug(f"Python strategy not available: {e}")
    
    try:
        from integration_coworker.codegen.gates.typescript import TypeScriptStrategy
        register_strategy("typescript", TypeScriptStrategy)
    except ImportError as e:
        logger.debug(f"TypeScript strategy not available: {e}")
    
    try:
        from integration_coworker.codegen.gates.go import GoStrategy
        register_strategy("go", GoStrategy)
    except ImportError as e:
        logger.debug(f"Go strategy not available: {e}")


# Auto-register on import
_auto_register()
