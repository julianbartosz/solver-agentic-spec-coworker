"""
REST/OpenAPI codegen strategy.

Generates standard HTTP client code with request/response patterns.
This bridges to the existing Endpoint-based codegen for compatibility.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from integration_coworker.codegen.protocol_dispatch import (
    ArtifactType,
    CodegenContext,
    GeneratedArtifact,
)
from integration_coworker.domain.ir import (
    Operation,
    ProtocolType,
)

logger = logging.getLogger(__name__)


@dataclass
class RESTCodegenStrategy:
    """
    Code generation strategy for REST/OpenAPI operations.
    
    Generates standard HTTP client code with request/response patterns.
    This bridges to the existing Endpoint-based codegen for compatibility.
    """
    
    @property
    def protocol_type(self) -> ProtocolType:
        return ProtocolType.REST
    
    def generate_client(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate REST client method."""
        method = operation.metadata.get("method", "GET") if operation.metadata else "GET"
        path = operation.metadata.get("path", "/") if operation.metadata else "/"
        
        class_name = f"{context.class_name_prefix}{context.provider_code.title()}Client"
        method_name = self._to_method_name(operation.name or operation.operation_id or "call_api")
        
        code = self._generate_rest_client_code(
            class_name=class_name,
            method_name=method_name,
            http_method=method,
            path=path,
            operation=operation,
            context=context,
        )
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.CLIENT,
            filename=f"{context.provider_code}_client.py",
            code=code,
            language=context.language,
            imports=["httpx", "typing"],
            dependencies=["httpx"],
            protocol=ProtocolType.REST,
            operation_id=operation.operation_id,
            metadata={"method": method, "path": path},
        )
    
    def generate_flow(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate REST integration flow."""
        flow_name = f"{context.provider_code}_{self._to_method_name(operation.name or 'flow')}"
        
        code = self._generate_flow_code(flow_name, operation, context)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.FLOW,
            filename=f"{flow_name}.py",
            code=code,
            language=context.language,
            protocol=ProtocolType.REST,
            operation_id=operation.operation_id,
        )
    
    def generate_test(
        self,
        operation: Operation,
        context: CodegenContext,
    ) -> GeneratedArtifact:
        """Generate REST test."""
        test_name = f"test_{context.provider_code}_{self._to_method_name(operation.name or 'api')}"
        
        code = self._generate_test_code(test_name, operation, context)
        
        return GeneratedArtifact(
            artifact_type=ArtifactType.TEST,
            filename=f"{test_name}.py",
            code=code,
            language=context.language,
            imports=["pytest", "unittest.mock"],
            dependencies=["pytest", "pytest-asyncio"] if context.use_async else ["pytest"],
            protocol=ProtocolType.REST,
            operation_id=operation.operation_id,
        )
    
    def build_prompt(
        self,
        operation: Operation,
        context: CodegenContext,
        artifact_type: ArtifactType,
        skeleton_code: str,
    ) -> str:
        """Build REST-specific LLM prompt."""
        method = operation.metadata.get("method", "GET") if operation.metadata else "GET"
        path = operation.metadata.get("path", "/") if operation.metadata else "/"
        
        return f"""You are generating a REST API client in {context.language}.

## OPERATION DETAILS
- HTTP Method: {method}
- Path: {path}
- Operation ID: {operation.operation_id or operation.name}
- Summary: {operation.summary or 'N/A'}
- Auth Required: {operation.auth_required}

## COMMUNICATION PATTERN
This is a standard REST {method} request (UNARY pattern - one request, one response).

## SKELETON CODE
```{context.language}
{skeleton_code}
```

## REQUIREMENTS
1. Use httpx for async HTTP calls
2. Handle errors appropriately (raise exceptions for 4xx/5xx)
3. Include proper type hints
4. Add docstrings with Google style

Return ONLY the completed code, no explanations.
"""
    
    def _to_method_name(self, name: str) -> str:
        """Convert operation name to Python method name."""
        clean = "".join(c if c.isalnum() else "_" for c in name)
        return clean.lower().strip("_")
    
    def _generate_rest_client_code(
        self,
        class_name: str,
        method_name: str,
        http_method: str,
        path: str,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """Generate REST client code."""
        async_prefix = "async " if context.use_async else ""
        await_prefix = "await " if context.use_async else ""
        
        return f'''"""
REST client for {context.provider_code}.

Auto-generated by Integration Co-Worker.
"""
import httpx
from typing import Any, Dict, Optional


class {class_name}:
    """Client for {context.provider_code} REST API."""
    
    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
    
    {async_prefix}def {method_name}(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        {operation.summary or f"Call {http_method} {path}"}
        
        Returns:
            Response data as dictionary
        """
        headers = {{"Authorization": f"Bearer {{self.api_key}}"}} if self.api_key else {{}}
        
        {await_prefix}with httpx.{"Async" if context.use_async else ""}Client() as client:
            response = {await_prefix}client.request(
                method="{http_method}",
                url=f"{{self.base_url}}{path}",
                headers=headers,
                **kwargs,
            )
            response.raise_for_status()
            return response.json()
'''
    
    def _generate_flow_code(
        self,
        flow_name: str,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """Generate flow orchestration code."""
        async_prefix = "async " if context.use_async else ""
        
        return f'''"""
Integration flow: {flow_name}

Auto-generated by Integration Co-Worker.
"""
from typing import Any, Dict


{async_prefix}def {flow_name}(
    client: Any,
    **params: Any,
) -> Dict[str, Any]:
    """
    {operation.summary or f"Execute {operation.name} flow"}
    
    Args:
        client: Configured API client
        **params: Operation parameters
    
    Returns:
        Flow result
    """
    # TODO: Implement flow logic
    raise NotImplementedError("Flow implementation required")
'''
    
    def _generate_test_code(
        self,
        test_name: str,
        operation: Operation,
        context: CodegenContext,
    ) -> str:
        """Generate test code."""
        async_marker = "@pytest.mark.asyncio\n    " if context.use_async else ""
        async_prefix = "async " if context.use_async else ""
        
        return f'''"""
Tests for {operation.name or operation.operation_id}

Auto-generated by Integration Co-Worker.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class Test{test_name.title().replace("_", "")}:
    """Tests for {operation.summary or operation.name}."""
    
    {async_marker}{async_prefix}def test_{operation.name or "operation"}_success(self):
        """Test successful operation."""
        # TODO: Implement test
        pass
    
    {async_marker}{async_prefix}def test_{operation.name or "operation"}_error(self):
        """Test error handling."""
        # TODO: Implement test
        pass
'''
