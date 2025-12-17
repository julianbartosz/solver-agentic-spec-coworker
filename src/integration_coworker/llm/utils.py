"""
LLM output processing utilities.

Provides functions to clean and normalize LLM outputs.
"""
import re
from typing import Optional


def strip_code_fences(text: str) -> str:
    """
    Remove markdown code fences from LLM output.
    
    Handles:
    - ```python ... ```
    - ```py ... ```
    - ``` ... ```
    - Preamble text before first fence (e.g., "Here is the code:")
    - Multiple code blocks (extracts first one)
    
    Args:
        text: Raw LLM output that may contain code fences
        
    Returns:
        Clean code without markdown formatting
        
    Examples:
        >>> strip_code_fences("```python\\nprint('hello')\\n```")
        "print('hello')"
        
        >>> strip_code_fences("Here is the code:\\n```python\\nprint('hello')\\n```")
        "print('hello')"
    """
    if not text:
        return text
    
    text = text.strip()
    
    # Pattern 1: Full code block with optional preamble
    # Matches: optional text + ```[language]\n code \n```
    pattern = r'^.*?```(?:python|py|json|yaml|javascript|js)?\s*\n(.*?)```'
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    # Pattern 2: Code block without language specifier
    pattern2 = r'^.*?```\s*\n(.*?)```'
    match2 = re.search(pattern2, text, re.DOTALL)
    if match2:
        return match2.group(1).strip()
    
    # Pattern 3: Just strip leading/trailing fences (no newline after ```)
    lines = text.split('\n')
    if lines and lines[0].startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip() == '```':
        lines = lines[:-1]
    
    result = '\n'.join(lines).strip()
    
    # If we still have fences, try one more aggressive pattern
    if result.startswith('```') or result.endswith('```'):
        result = re.sub(r'^```\w*\s*', '', result)
        result = re.sub(r'\s*```$', '', result)
    
    return result.strip()


def extract_code_block(text: str, language: Optional[str] = None) -> Optional[str]:
    """
    Extract a specific language code block from text.
    
    Args:
        text: Text containing code blocks
        language: Optional language to match (e.g., 'python', 'json')
        
    Returns:
        Extracted code or None if not found
    """
    if not text:
        return None
    
    if language:
        pattern = rf'```{language}\s*\n(.*?)```'
    else:
        pattern = r'```\w*\s*\n(.*?)```'
    
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    
    return None


def clean_llm_code_output(text: str) -> str:
    """
    Comprehensive cleanup of LLM-generated code.
    
    Applies:
    1. Strip markdown code fences
    2. Remove common LLM preambles/postambles
    3. Normalize whitespace
    
    Args:
        text: Raw LLM code output
        
    Returns:
        Clean Python code ready for AST parsing
    """
    if not text:
        return text
    
    # Step 1: Strip code fences
    code = strip_code_fences(text)
    
    # Step 2: Remove common LLM preambles
    # Lines like "Here is the code:", "Sure, here's...", etc.
    preamble_patterns = [
        r'^(?:Here(?:\'s| is) (?:the |your |a )?(?:code|implementation|solution)[:\.]?\s*\n)+',
        r'^(?:Sure[,!]?\s*(?:here(?:\'s| is)[^:]*:)?\s*\n)+',
        r'^(?:I\'ll[^:]*:\s*\n)+',
        r'^(?:The following[^:]*:\s*\n)+',
    ]
    
    for pattern in preamble_patterns:
        code = re.sub(pattern, '', code, flags=re.IGNORECASE)
    
    # Step 3: Remove common postambles
    postamble_patterns = [
        r'\n+(?:This code[^.]*\.|Let me know[^.]*\.|Feel free[^.]*\.)\s*$',
        r'\n+(?:Note:[^\n]*)\s*$',
    ]
    
    for pattern in postamble_patterns:
        code = re.sub(pattern, '', code, flags=re.IGNORECASE)
    
    return code.strip()
