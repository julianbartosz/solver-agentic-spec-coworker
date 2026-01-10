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
    
    V22-002 Fix: Enhanced to handle more edge cases that cause syntax errors
    at line 8 (typically the first import after docstring).
    
    Applies:
    1. Strip markdown code fences
    2. Remove common LLM preambles/postambles
    3. Remove inline comments/notes that aren't Python
    4. Normalize whitespace
    
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
        # V22-002: Additional patterns for LLM text that slips through
        r'^(?:Below is[^:]*:\s*\n)+',
        r'^(?:Here\'s (?:my |the )?(?:updated|revised|completed)[^:]*:\s*\n)+',
    ]
    
    for pattern in preamble_patterns:
        code = re.sub(pattern, '', code, flags=re.IGNORECASE)
    
    # Step 3: Remove common postambles
    postamble_patterns = [
        r'\n+(?:This code[^.]*\.|Let me know[^.]*\.|Feel free[^.]*\.)\s*$',
        r'\n+(?:Note:[^\n]*)\s*$',
        # V22-002: Additional postamble patterns
        r'\n+(?:I hope this helps[^.]*\.)\s*$',
        r'\n+(?:Please let me know[^.]*\.)\s*$',
    ]
    
    for pattern in postamble_patterns:
        code = re.sub(pattern, '', code, flags=re.IGNORECASE)
    
    # V22-002: Remove any lines that are clearly not Python code
    # (e.g., LLM commentary that slipped through)
    lines = code.split('\n')
    clean_lines = []
    in_docstring = False
    docstring_char = None
    
    for line in lines:
        stripped = line.strip()
        
        # Track docstrings (triple quotes)
        if '"""' in stripped or "'''" in stripped:
            if not in_docstring:
                in_docstring = True
                docstring_char = '"""' if '"""' in stripped else "'''"
                # Check if it closes on same line
                if stripped.count(docstring_char) >= 2:
                    in_docstring = False
            else:
                if docstring_char in stripped:
                    in_docstring = False
        
        # Inside docstring, keep everything
        if in_docstring:
            clean_lines.append(line)
            continue
        
        # Skip lines that look like LLM commentary (not in docstring)
        if stripped and not stripped.startswith('#'):
            # Check if line looks like English prose (not Python)
            # Skip lines that start with common LLM phrases
            commentary_starts = (
                'here is', 'here\'s', 'this is', 'below is', 
                'the code', 'note:', 'note that', 'please',
                'i hope', 'let me', 'feel free', 'sure,', 'sure!',
            )
            if stripped.lower().startswith(commentary_starts):
                continue
        
        clean_lines.append(line)
    
    return '\n'.join(clean_lines).strip()
