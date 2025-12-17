"""
Provider-Agnostic Structured Output Contract for Codegen

Per user request: Implement a provider-agnostic structured-output CONTRACT layer
for codegen, with OpenAI response_format json_schema as an optional fast-path.

LangChain's `with_structured_output` provides:
1. Tool calling mode (preferred) - works on OpenAI, Anthropic, Google
2. JSON mode fallback - when tool calling unavailable
3. Provider-specific optimizations (e.g., OpenAI response_format strict)

This module defines:
- Pydantic models for codegen artifacts
- Provider-agnostic structured output wrapper
- OpenAI fast-path optimization when provider==openai
- Fallback parser for providers without native support
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Type, TypeVar, Union

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# =============================================================================
# Pydantic Models for Codegen Artifacts
# =============================================================================

class ArtifactLanguage(str, Enum):
    """Supported programming languages for generated code."""
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    GO = "go"
    RUST = "rust"
    JAVA = "java"
    UNKNOWN = "unknown"


class ArtifactPurpose(str, Enum):
    """Purpose classification for generated files."""
    CLIENT = "client"           # API client code
    FLOW = "flow"               # Business logic flow
    TEST = "test"               # Test file
    CONFIG = "config"           # Configuration file
    TYPES = "types"             # Type definitions
    UTILITY = "utility"         # Helper/utility code
    DOCUMENTATION = "documentation"


class CodeArtifactFile(BaseModel):
    """A single generated code file."""
    
    path: str = Field(
        ...,
        description="Relative file path (e.g., 'src/clients/api_client.py')",
        min_length=1,
    )
    language: ArtifactLanguage = Field(
        default=ArtifactLanguage.PYTHON,
        description="Programming language of the file",
    )
    purpose: ArtifactPurpose = Field(
        ...,
        description="Purpose/type of the generated file",
    )
    content: str = Field(
        ...,
        description="The actual code content",
        min_length=1,
    )
    
    @field_validator("path")
    @classmethod
    def normalize_path(cls, v: str) -> str:
        """Normalize path separators."""
        return v.replace("\\", "/").strip()
    
    @field_validator("content")
    @classmethod
    def strip_code_fences(cls, v: str) -> str:
        """Remove markdown code fences if present."""
        v = v.strip()
        if v.startswith("```"):
            # Remove opening fence with optional language
            lines = v.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            v = "\n".join(lines)
        return v


class CodegenArtifacts(BaseModel):
    """
    Complete codegen output with multiple files.
    
    This is the CONTRACT for structured output from codegen LLM calls.
    Works across all providers via LangChain's with_structured_output.
    """
    
    files: List[CodeArtifactFile] = Field(
        ...,
        description="List of generated code files",
        min_length=1,
    )
    dependencies: Optional[List[str]] = Field(
        default=None,
        description="Optional list of package dependencies (e.g., ['requests>=2.28', 'pydantic'])",
    )
    summary: Optional[str] = Field(
        default=None,
        description="Brief summary of what was generated",
    )
    
    def get_by_purpose(self, purpose: ArtifactPurpose) -> List[CodeArtifactFile]:
        """Get all files with a specific purpose."""
        return [f for f in self.files if f.purpose == purpose]
    
    def get_by_language(self, language: ArtifactLanguage) -> List[CodeArtifactFile]:
        """Get all files in a specific language."""
        return [f for f in self.files if f.language == language]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return self.model_dump(mode="json")


class SingleArtifact(BaseModel):
    """
    Single file artifact for simpler codegen calls.
    
    Used when generating one file at a time (client, flow, or test).
    """
    
    content: str = Field(
        ...,
        description="The generated code content",
        min_length=1,
    )
    language: ArtifactLanguage = Field(
        default=ArtifactLanguage.PYTHON,
        description="Programming language",
    )
    imports_required: Optional[List[str]] = Field(
        default=None,
        description="List of imports used in the code",
    )
    
    @field_validator("content")
    @classmethod
    def strip_code_fences(cls, v: str) -> str:
        """Remove markdown code fences if present."""
        v = v.strip()
        if v.startswith("```"):
            lines = v.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            v = "\n".join(lines)
        return v


# =============================================================================
# Structured Output Provider Interface
# =============================================================================

@dataclass
class StructuredOutputConfig:
    """Configuration for structured output generation."""
    
    # Provider name (openai, anthropic, google)
    provider: str
    
    # Use strict JSON schema mode (OpenAI only)
    use_strict_json_schema: bool = True
    
    # Include schema in prompt as fallback
    include_schema_in_prompt: bool = True
    
    # Method preference: "tool_calling" or "json_mode"
    method: Literal["tool_calling", "json_mode", "auto"] = "auto"


def get_structured_llm(
    llm: Any,
    schema: Type[T],
    config: Optional[StructuredOutputConfig] = None,
) -> Any:
    """
    Wrap an LLM with structured output support.
    
    Provider-agnostic: Uses LangChain's with_structured_output which
    automatically selects the best method per provider.
    
    Args:
        llm: A LangChain chat model (ChatOpenAI, ChatAnthropic, etc.)
        schema: Pydantic model class defining the output structure
        config: Optional configuration for structured output
        
    Returns:
        LLM configured for structured output (returns schema instances)
        
    Example:
        >>> from langchain_openai import ChatOpenAI
        >>> llm = ChatOpenAI(model="gpt-4o")
        >>> structured_llm = get_structured_llm(llm, SingleArtifact)
        >>> result = structured_llm.invoke("Generate a hello world function")
        >>> assert isinstance(result, SingleArtifact)
    """
    config = config or StructuredOutputConfig(provider="auto")
    
    # Detect provider from LLM class name
    llm_class = llm.__class__.__name__.lower()
    
    if "openai" in llm_class:
        provider = "openai"
    elif "anthropic" in llm_class:
        provider = "anthropic"
    elif "google" in llm_class:
        provider = "google"
    else:
        provider = "unknown"
    
    logger.debug(f"Configuring structured output for provider: {provider}")
    
    # OpenAI fast-path: Use strict JSON schema mode
    if provider == "openai" and config.use_strict_json_schema:
        logger.debug("Using OpenAI strict JSON schema mode (fast-path)")
        return llm.with_structured_output(
            schema,
            method="json_schema",  # Uses response_format with strict schema
            strict=True,
        )
    
    # Anthropic/Google: Use tool calling (most reliable)
    if provider in ("anthropic", "google"):
        logger.debug(f"Using tool calling mode for {provider}")
        return llm.with_structured_output(
            schema,
            method="function_calling",  # Tool calling mode
        )
    
    # Fallback: Let LangChain choose the best method
    logger.debug(f"Using auto mode for {provider}")
    return llm.with_structured_output(schema)


async def get_structured_llm_async(
    llm: Any,
    schema: Type[T],
    config: Optional[StructuredOutputConfig] = None,
) -> Any:
    """
    Async version of get_structured_llm.
    
    Returns an async-compatible structured LLM.
    """
    # The synchronous version works for async too since
    # with_structured_output returns an object that supports both
    return get_structured_llm(llm, schema, config)


# =============================================================================
# Fallback Parser (for providers without native structured output)
# =============================================================================

class StructuredOutputParser:
    """
    Fallback parser using prompt engineering + JSON extraction.
    
    For LLM providers that don't support native structured output,
    this embeds the schema in the prompt and parses JSON from the response.
    """
    
    def __init__(self, schema: Type[T]):
        self.schema = schema
        self._schema_json = schema.model_json_schema()
    
    def get_format_instructions(self) -> str:
        """Get instructions to append to the prompt."""
        import json
        schema_str = json.dumps(self._schema_json, indent=2)
        
        return f"""
Respond with a JSON object matching this schema:
```json
{schema_str}
```

Important:
- Output ONLY valid JSON, no additional text
- All required fields must be present
- Follow the exact field names and types
"""
    
    def parse(self, text: str) -> T:
        """
        Parse LLM output into the schema type.
        
        Args:
            text: Raw LLM output (may contain extra text)
            
        Returns:
            Parsed Pydantic model instance
            
        Raises:
            ValueError: If parsing fails
        """
        import json
        import re
        
        # Try to extract JSON from the text
        # Look for JSON block in markdown
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON object
            json_match = re.search(r"\{.*\}", text, re.DOTALL)
            if json_match:
                json_str = json_match.group(0)
            else:
                raise ValueError(f"No JSON found in response: {text[:200]}...")
        
        try:
            data = json.loads(json_str)
            return self.schema.model_validate(data)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
        except Exception as e:
            raise ValueError(f"Schema validation failed: {e}")
    
    async def aparse(self, text: str) -> T:
        """Async parse (same as sync for now)."""
        return self.parse(text)


# =============================================================================
# Convenience Functions
# =============================================================================

def create_codegen_artifacts(
    files: List[Dict[str, str]],
    dependencies: Optional[List[str]] = None,
    summary: Optional[str] = None,
) -> CodegenArtifacts:
    """
    Convenience function to create CodegenArtifacts from simple dicts.
    
    Args:
        files: List of {"path": ..., "content": ..., "purpose": ...}
        dependencies: Optional package dependencies
        summary: Optional summary
        
    Returns:
        CodegenArtifacts instance
    """
    artifact_files = []
    for f in files:
        # Infer language from extension if not provided
        language = f.get("language", ArtifactLanguage.PYTHON)
        if isinstance(language, str):
            language = ArtifactLanguage(language) if language in [e.value for e in ArtifactLanguage] else ArtifactLanguage.UNKNOWN
        
        # Infer purpose if not provided
        purpose = f.get("purpose", ArtifactPurpose.UTILITY)
        if isinstance(purpose, str):
            purpose = ArtifactPurpose(purpose) if purpose in [e.value for e in ArtifactPurpose] else ArtifactPurpose.UTILITY
        
        artifact_files.append(CodeArtifactFile(
            path=f["path"],
            language=language,
            purpose=purpose,
            content=f["content"],
        ))
    
    return CodegenArtifacts(
        files=artifact_files,
        dependencies=dependencies,
        summary=summary,
    )
