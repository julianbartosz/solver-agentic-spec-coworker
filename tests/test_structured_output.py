"""
Tests for provider-agnostic structured output contract.

Verifies:
1. Pydantic models validate correctly
2. Structured output works for OpenAI (json_schema mode)
3. Structured output works for Anthropic (tool calling)
4. Fallback parser correctly extracts JSON
"""
import json
import pytest
from unittest.mock import MagicMock, patch

from integration_coworker.codegen.structured_output import (
    ArtifactLanguage,
    ArtifactPurpose,
    CodeArtifactFile,
    CodegenArtifacts,
    SingleArtifact,
    StructuredOutputConfig,
    StructuredOutputParser,
    get_structured_llm,
    create_codegen_artifacts,
)


class TestCodeArtifactFile:
    """Tests for CodeArtifactFile model."""
    
    def test_basic_creation(self):
        """Test creating a basic artifact file."""
        artifact = CodeArtifactFile(
            path="src/client.py",
            language=ArtifactLanguage.PYTHON,
            purpose=ArtifactPurpose.CLIENT,
            content="def hello(): pass",
        )
        assert artifact.path == "src/client.py"
        assert artifact.language == ArtifactLanguage.PYTHON
        assert artifact.purpose == ArtifactPurpose.CLIENT
    
    def test_path_normalization(self):
        """Test that Windows paths are normalized."""
        artifact = CodeArtifactFile(
            path="src\\clients\\api.py",
            purpose=ArtifactPurpose.CLIENT,
            content="code",
        )
        assert artifact.path == "src/clients/api.py"
    
    def test_code_fence_stripping(self):
        """Test that markdown code fences are stripped."""
        artifact = CodeArtifactFile(
            path="test.py",
            purpose=ArtifactPurpose.TEST,
            content="```python\ndef test(): pass\n```",
        )
        assert artifact.content == "def test(): pass"
    
    def test_code_fence_with_language(self):
        """Test code fence stripping with language hint."""
        artifact = CodeArtifactFile(
            path="test.py",
            purpose=ArtifactPurpose.TEST,
            content="```typescript\nconst x = 1;\n```",
        )
        assert artifact.content == "const x = 1;"
    
    def test_no_code_fence(self):
        """Test content without code fences."""
        artifact = CodeArtifactFile(
            path="test.py",
            purpose=ArtifactPurpose.TEST,
            content="def test(): pass",
        )
        assert artifact.content == "def test(): pass"


class TestCodegenArtifacts:
    """Tests for CodegenArtifacts model."""
    
    def test_basic_creation(self):
        """Test creating artifacts with multiple files."""
        artifacts = CodegenArtifacts(
            files=[
                CodeArtifactFile(
                    path="src/client.py",
                    purpose=ArtifactPurpose.CLIENT,
                    content="class Client: pass",
                ),
                CodeArtifactFile(
                    path="tests/test_client.py",
                    purpose=ArtifactPurpose.TEST,
                    content="def test_client(): pass",
                ),
            ],
            dependencies=["requests>=2.28"],
            summary="Generated API client and tests",
        )
        
        assert len(artifacts.files) == 2
        assert artifacts.dependencies == ["requests>=2.28"]
        assert "API client" in artifacts.summary
    
    def test_get_by_purpose(self):
        """Test filtering files by purpose."""
        artifacts = CodegenArtifacts(
            files=[
                CodeArtifactFile(path="client.py", purpose=ArtifactPurpose.CLIENT, content="c"),
                CodeArtifactFile(path="flow.py", purpose=ArtifactPurpose.FLOW, content="f"),
                CodeArtifactFile(path="test.py", purpose=ArtifactPurpose.TEST, content="t"),
            ]
        )
        
        clients = artifacts.get_by_purpose(ArtifactPurpose.CLIENT)
        assert len(clients) == 1
        assert clients[0].path == "client.py"
    
    def test_get_by_language(self):
        """Test filtering files by language."""
        artifacts = CodegenArtifacts(
            files=[
                CodeArtifactFile(
                    path="client.py",
                    language=ArtifactLanguage.PYTHON,
                    purpose=ArtifactPurpose.CLIENT,
                    content="c",
                ),
                CodeArtifactFile(
                    path="client.ts",
                    language=ArtifactLanguage.TYPESCRIPT,
                    purpose=ArtifactPurpose.CLIENT,
                    content="t",
                ),
            ]
        )
        
        python_files = artifacts.get_by_language(ArtifactLanguage.PYTHON)
        assert len(python_files) == 1
        assert python_files[0].path == "client.py"
    
    def test_to_dict(self):
        """Test serialization to dictionary."""
        artifacts = CodegenArtifacts(
            files=[
                CodeArtifactFile(
                    path="test.py",
                    purpose=ArtifactPurpose.TEST,
                    content="code",
                ),
            ]
        )
        
        d = artifacts.to_dict()
        assert "files" in d
        assert len(d["files"]) == 1
        assert d["files"][0]["path"] == "test.py"
    
    def test_min_one_file_required(self):
        """Test that at least one file is required."""
        with pytest.raises(Exception):  # ValidationError
            CodegenArtifacts(files=[])


class TestSingleArtifact:
    """Tests for SingleArtifact model."""
    
    def test_basic_creation(self):
        """Test creating a single artifact."""
        artifact = SingleArtifact(
            content="def hello(): return 'world'",
            language=ArtifactLanguage.PYTHON,
            imports_required=["typing"],
        )
        assert "hello" in artifact.content
        assert artifact.language == ArtifactLanguage.PYTHON
    
    def test_code_fence_stripping(self):
        """Test code fence stripping."""
        artifact = SingleArtifact(
            content="```python\ndef test(): pass\n```",
        )
        assert artifact.content == "def test(): pass"


class TestStructuredOutputParser:
    """Tests for fallback parser."""
    
    def test_parse_clean_json(self):
        """Test parsing clean JSON response."""
        parser = StructuredOutputParser(SingleArtifact)
        
        response = '{"content": "def hello(): pass", "language": "python"}'
        result = parser.parse(response)
        
        assert isinstance(result, SingleArtifact)
        assert "hello" in result.content
    
    def test_parse_json_in_markdown(self):
        """Test parsing JSON inside markdown code block."""
        parser = StructuredOutputParser(SingleArtifact)
        
        response = '''Here's the generated code:

```json
{"content": "def test(): pass", "language": "python"}
```

This function does nothing.'''
        
        result = parser.parse(response)
        assert isinstance(result, SingleArtifact)
        assert "test" in result.content
    
    def test_parse_json_with_surrounding_text(self):
        """Test parsing JSON with surrounding text."""
        parser = StructuredOutputParser(SingleArtifact)
        
        response = '''I'll generate a function for you:
{"content": "def greet(name): return f'Hello, {name}'", "language": "python"}
Done!'''
        
        result = parser.parse(response)
        assert "greet" in result.content
    
    def test_parse_invalid_json_raises(self):
        """Test that invalid JSON raises ValueError."""
        parser = StructuredOutputParser(SingleArtifact)
        
        with pytest.raises(ValueError, match="No JSON found"):
            parser.parse("No JSON here, just plain text")
    
    def test_parse_schema_mismatch_raises(self):
        """Test that schema mismatch raises ValueError."""
        parser = StructuredOutputParser(SingleArtifact)
        
        # Missing required 'content' field
        with pytest.raises(ValueError, match="validation failed"):
            parser.parse('{"language": "python"}')
    
    def test_get_format_instructions(self):
        """Test format instructions contain schema."""
        parser = StructuredOutputParser(SingleArtifact)
        
        instructions = parser.get_format_instructions()
        assert "JSON" in instructions
        assert "content" in instructions  # Schema field


class TestGetStructuredLLM:
    """Tests for get_structured_llm function."""
    
    def test_openai_uses_json_schema_mode(self):
        """Test that OpenAI uses strict JSON schema mode."""
        mock_llm = MagicMock()
        mock_llm.__class__.__name__ = "ChatOpenAI"
        
        config = StructuredOutputConfig(
            provider="openai",
            use_strict_json_schema=True,
        )
        
        get_structured_llm(mock_llm, SingleArtifact, config)
        
        # Verify with_structured_output was called with json_schema method
        mock_llm.with_structured_output.assert_called_once()
        call_kwargs = mock_llm.with_structured_output.call_args.kwargs
        assert call_kwargs.get("method") == "json_schema"
        assert call_kwargs.get("strict") is True
    
    def test_anthropic_uses_function_calling(self):
        """Test that Anthropic uses function calling mode."""
        mock_llm = MagicMock()
        mock_llm.__class__.__name__ = "ChatAnthropic"
        
        get_structured_llm(mock_llm, SingleArtifact)
        
        mock_llm.with_structured_output.assert_called_once()
        call_kwargs = mock_llm.with_structured_output.call_args.kwargs
        assert call_kwargs.get("method") == "function_calling"
    
    def test_google_uses_function_calling(self):
        """Test that Google uses function calling mode."""
        mock_llm = MagicMock()
        mock_llm.__class__.__name__ = "ChatGoogleGenerativeAI"
        
        get_structured_llm(mock_llm, SingleArtifact)
        
        mock_llm.with_structured_output.assert_called_once()
        call_kwargs = mock_llm.with_structured_output.call_args.kwargs
        assert call_kwargs.get("method") == "function_calling"
    
    def test_unknown_provider_uses_auto(self):
        """Test that unknown providers use auto mode."""
        mock_llm = MagicMock()
        mock_llm.__class__.__name__ = "CustomLLM"
        
        get_structured_llm(mock_llm, SingleArtifact)
        
        mock_llm.with_structured_output.assert_called_once()
        # Should be called with just the schema (auto mode)
        call_args = mock_llm.with_structured_output.call_args
        assert call_args.args[0] == SingleArtifact


class TestCreateCodegenArtifacts:
    """Tests for convenience function."""
    
    def test_create_from_dicts(self):
        """Test creating artifacts from simple dicts."""
        artifacts = create_codegen_artifacts(
            files=[
                {"path": "src/client.py", "content": "code", "purpose": "client"},
                {"path": "tests/test.py", "content": "test", "purpose": "test"},
            ],
            dependencies=["requests"],
            summary="Test artifacts",
        )
        
        assert len(artifacts.files) == 2
        assert artifacts.files[0].path == "src/client.py"
        assert artifacts.files[0].purpose == ArtifactPurpose.CLIENT
        assert artifacts.dependencies == ["requests"]
    
    def test_infer_language(self):
        """Test language inference defaults to Python."""
        artifacts = create_codegen_artifacts(
            files=[{"path": "test.py", "content": "code", "purpose": "test"}]
        )
        
        assert artifacts.files[0].language == ArtifactLanguage.PYTHON


class TestIntegrationRealProvider:
    """Integration tests with real LLM providers (skipped in CI)."""
    
    @pytest.mark.skip(reason="Requires real API keys")
    def test_openai_structured_output(self):
        """Test structured output with real OpenAI."""
        import os
        from langchain_openai import ChatOpenAI
        
        llm = ChatOpenAI(
            model="gpt-4o-mini",
            api_key=os.getenv("OPENAI_API_KEY"),
        )
        
        structured_llm = get_structured_llm(llm, SingleArtifact)
        
        result = structured_llm.invoke(
            "Generate a simple Python hello world function"
        )
        
        assert isinstance(result, SingleArtifact)
        assert "def" in result.content or "hello" in result.content.lower()
    
    @pytest.mark.skip(reason="Requires real API keys")
    def test_anthropic_structured_output(self):
        """Test structured output with real Anthropic."""
        import os
        from langchain_anthropic import ChatAnthropic
        
        llm = ChatAnthropic(
            model="claude-sonnet-4-5-20250929",
            api_key=os.getenv("ANTHROPIC_API_KEY"),
        )
        
        structured_llm = get_structured_llm(llm, SingleArtifact)
        
        result = structured_llm.invoke(
            "Generate a simple Python hello world function"
        )
        
        assert isinstance(result, SingleArtifact)
        assert "def" in result.content or "hello" in result.content.lower()
