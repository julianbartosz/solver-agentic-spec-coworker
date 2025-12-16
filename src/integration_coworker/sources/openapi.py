"""
OpenAPI source handler.

Implements the SpecSource protocol for OpenAPI/Swagger specifications.
This wraps the existing OpenAPI detection and parsing logic from
detect_and_parse_spec.py into the new plugin architecture.
"""

import json
import logging
import re
from typing import Union

import yaml

from .base import (
    ContentType,
    ParsedSpec,
    SourceType,
    normalize_content,
)

logger = logging.getLogger(__name__)

# PostgreSQL/msgpack BIGINT range
INT64_MAX = 9223372036854775807
INT64_MIN = -9223372036854775808


class OpenAPISource:
    """
    Handler for OpenAPI/Swagger specifications.
    
    Supports:
    - OpenAPI 3.x (YAML and JSON)
    - Swagger 2.0 (YAML and JSON)
    - GraphQL schemas (converted to pseudo-OpenAPI)
    - AsyncAPI (converted to pseudo-OpenAPI)
    - HTML API docs (converted to pseudo-OpenAPI)
    - PDF API docs (converted to pseudo-OpenAPI)
    
    Note: HTML and PDF parsing create "pseudo-OpenAPI" dicts for downstream
    compatibility with the existing API processing pipeline.
    """
    
    def detect(
        self,
        content: ContentType,
        uri: str,
        content_type: str,
    ) -> float:
        """
        Detect if content is an OpenAPI/Swagger/GraphQL/AsyncAPI specification.
        
        Returns confidence score 0-1.
        """
        content_str = normalize_content(content)
        ct = content_type.lower()
        uri_lower = uri.lower()
        
        # High confidence: explicit OpenAPI/Swagger files
        if ".yaml" in uri_lower or ".yml" in uri_lower:
            if self._looks_like_openapi_yaml(content_str):
                return 0.95
        
        if ".json" in uri_lower:
            if self._looks_like_openapi_json(content_str):
                return 0.95
        
        # High confidence: content-type indicators
        if "openapi" in ct or "swagger" in ct:
            return 0.95
        
        # Medium confidence: content heuristics
        if self._is_openapi_content(content_str, ct):
            return 0.85
        
        if self._is_graphql_content(content_str, ct):
            return 0.80
        
        if self._is_asyncapi_content(content_str, ct):
            return 0.80
        
        # Lower confidence: HTML/PDF that might contain API docs
        if self._is_html_content(content_str, ct):
            return 0.40  # Lower than CSV so files are properly detected
        
        if self._is_pdf_content(content_str, ct):
            return 0.35  # Even lower for PDFs
        
        return 0.0
    
    def parse(
        self,
        content: ContentType,
        uri: str,
    ) -> ParsedSpec:
        """
        Parse OpenAPI/Swagger content.
        
        Returns ParsedSpec with source_type=API.
        """
        content_str = normalize_content(content)
        
        # Try each parser in order of specificity
        result = None
        errors = []
        
        # Try OpenAPI YAML/JSON first
        if result is None:
            result, error = self._try_parse_openapi(content_str, uri)
            if error:
                errors.append(error)
        
        # Try GraphQL
        if result is None and self._is_graphql_content(content_str, ""):
            result, error = self._try_parse_graphql(content_str, uri)
            if error:
                errors.append(error)
        
        # Try AsyncAPI
        if result is None and self._is_asyncapi_content(content_str, ""):
            result, error = self._try_parse_asyncapi(content_str, uri)
            if error:
                errors.append(error)
        
        # Try HTML docs
        if result is None and self._is_html_content(content_str, ""):
            result, error = self._try_parse_html(content_str, uri)
            if error:
                errors.append(error)
        
        # Try PDF docs
        if result is None and self._is_pdf_content(content_str, ""):
            result, error = self._try_parse_pdf(content, uri)
            if error:
                errors.append(error)
        
        if result is None:
            return ParsedSpec(
                source_type=SourceType.API,
                source_uri=uri,
                data=None,
                errors=errors or ["Could not parse content as OpenAPI/Swagger/GraphQL"],
            )
        
        return ParsedSpec(
            source_type=SourceType.API,
            source_uri=uri,
            data=result,
            metadata=self._extract_metadata(result),
            errors=[],
            confidence=0.9,
        )
    
    # -------------------------------------------------------------------------
    # Detection helpers
    # -------------------------------------------------------------------------
    
    def _looks_like_openapi_yaml(self, content: str) -> bool:
        """Check if YAML content looks like OpenAPI/Swagger."""
        content_lower = content[:2000].lower()
        return (
            "openapi:" in content_lower
            or "swagger:" in content_lower
            or '"openapi":' in content_lower
            or '"swagger":' in content_lower
        )
    
    def _looks_like_openapi_json(self, content: str) -> bool:
        """Check if JSON content looks like OpenAPI/Swagger."""
        content_lower = content[:2000].lower()
        return '"openapi":' in content_lower or '"swagger":' in content_lower
    
    def _is_openapi_content(self, content: str, content_type: str) -> bool:
        """Check if content is OpenAPI/Swagger."""
        ct = content_type.lower()
        if "openapi" in ct or "swagger" in ct:
            return True
        
        content_sample = content.strip()[:2000].lower()
        return (
            "openapi:" in content_sample
            or "swagger:" in content_sample
            or '"openapi":' in content_sample
            or '"swagger":' in content_sample
        )
    
    def _is_graphql_content(self, content: str, content_type: str) -> bool:
        """Check if content is a GraphQL schema."""
        ct = content_type.lower()
        if "graphql" in ct:
            return True
        
        content_sample = content.strip()[:3000]
        content_lower = content_sample.lower()
        
        # Strong indicators
        strong_patterns = [
            "type query {", "type query{",
            "type mutation {", "type mutation{",
            "type subscription {", "type subscription{",
            "schema {", "schema{",
        ]
        
        for pattern in strong_patterns:
            if pattern in content_lower:
                return True
        
        # Look for "type <Name> {" pattern
        type_def_pattern = r'\btype\s+[A-Z][a-zA-Z0-9_]*\s*\{'
        type_matches = len(re.findall(type_def_pattern, content_sample))
        
        if type_matches >= 2:
            return True
        
        # Check for scalar/interface/union/enum/input keywords
        weak_patterns = ["scalar ", "interface ", "union ", "enum ", "input "]
        weak_count = sum(1 for p in weak_patterns if p in content_lower)
        
        return type_matches >= 1 and weak_count >= 1
    
    def _is_asyncapi_content(self, content: str, content_type: str) -> bool:
        """Check if content is AsyncAPI."""
        ct = content_type.lower()
        if "asyncapi" in ct:
            return True
        
        content_lower = content.strip()[:1000].lower()
        return "asyncapi:" in content_lower or '"asyncapi":' in content_lower
    
    def _is_html_content(self, content: str, content_type: str) -> bool:
        """Check if content is HTML."""
        ct = content_type.lower()
        if "html" in ct:
            return True
        
        content_lower = content.strip()[:500].lower()
        return (
            content_lower.startswith("<!doctype html")
            or content_lower.startswith("<html")
        )
    
    def _is_pdf_content(self, content: Union[str, bytes], content_type: str) -> bool:
        """Check if content is PDF."""
        ct = content_type.lower()
        if "pdf" in ct:
            return True
        
        # Check for PDF magic bytes
        if isinstance(content, bytes):
            return content[:10].startswith(b"%PDF-")
        return content.strip()[:10].startswith("%PDF-")
    
    # -------------------------------------------------------------------------
    # Parsing helpers
    # -------------------------------------------------------------------------
    
    def _try_parse_openapi(self, content: str, uri: str) -> tuple:
        """Try to parse as OpenAPI YAML/JSON. Returns (result, error)."""
        try:
            # Try YAML first (handles JSON too)
            data = yaml.safe_load(content)
            
            if not isinstance(data, dict):
                return None, "Content is not a valid dictionary"
            
            # Verify it's OpenAPI/Swagger
            if "openapi" not in data and "swagger" not in data:
                return None, "Not an OpenAPI or Swagger specification"
            
            # Sanitize large integers
            data = self._sanitize_large_ints(data)
            data["_source_uri"] = uri
            
            return data, None
            
        except yaml.YAMLError as e:
            return None, f"YAML parse error: {e}"
        except json.JSONDecodeError as e:
            return None, f"JSON parse error: {e}"
        except Exception as e:
            return None, f"Parse error: {e}"
    
    def _try_parse_graphql(self, content: str, uri: str) -> tuple:
        """Try to parse as GraphQL schema. Returns (result, error)."""
        try:
            from integration_coworker.parsers.graphql_parser import (
                parse_graphql_schema,
            )
        except ImportError:
            return None, "GraphQL parser not available"
        
        try:
            graphql_doc = parse_graphql_schema(content)
            
            if graphql_doc.errors and not graphql_doc.types:
                return None, f"GraphQL parse errors: {graphql_doc.errors}"
            
            # Convert to pseudo-OpenAPI for downstream compatibility
            spec = self._graphql_to_pseudo_openapi(graphql_doc, uri)
            return spec, None
            
        except Exception as e:
            return None, f"GraphQL parse error: {e}"
    
    def _try_parse_asyncapi(self, content: str, uri: str) -> tuple:
        """Try to parse as AsyncAPI. Returns (result, error)."""
        try:
            data = yaml.safe_load(content)
            
            if not isinstance(data, dict) or "asyncapi" not in data:
                return None, "Not an AsyncAPI specification"
            
            # Convert to pseudo-OpenAPI
            spec = self._asyncapi_to_pseudo_openapi(data, uri)
            return spec, None
            
        except Exception as e:
            return None, f"AsyncAPI parse error: {e}"
    
    def _try_parse_html(self, content: str, uri: str) -> tuple:
        """Try to parse HTML as API docs. Returns (result, error)."""
        try:
            from integration_coworker.parsers.html_parser import parse_html_spec
        except ImportError:
            return None, "HTML parser not available"
        
        try:
            html_doc = parse_html_spec(content)
            
            if html_doc.errors and not html_doc.endpoints:
                return None, f"HTML parse errors: {html_doc.errors}"
            
            # Convert to pseudo-OpenAPI
            spec = self._html_to_pseudo_openapi(html_doc, uri)
            return spec, None
            
        except Exception as e:
            return None, f"HTML parse error: {e}"
    
    def _try_parse_pdf(self, content: Union[str, bytes], uri: str) -> tuple:
        """Try to parse PDF as API docs. Returns (result, error)."""
        try:
            from integration_coworker.parsers.pdf_parser import parse_pdf_spec
        except ImportError:
            return None, "PDF parser not available"
        
        try:
            pdf_doc = parse_pdf_spec(content, uri)
            
            if pdf_doc.errors and not pdf_doc.endpoints:
                return None, f"PDF parse errors: {pdf_doc.errors}"
            
            # Convert to pseudo-OpenAPI
            spec = self._pdf_to_pseudo_openapi(pdf_doc, uri)
            return spec, None
            
        except Exception as e:
            return None, f"PDF parse error: {e}"
    
    # -------------------------------------------------------------------------
    # Conversion helpers
    # -------------------------------------------------------------------------
    
    def _graphql_to_pseudo_openapi(self, graphql_doc, uri: str) -> dict:
        """Convert GraphQL schema document to pseudo-OpenAPI structure."""
        spec = {
            "openapi": "3.0.0",
            "_parsed_from": "graphql",
            "_source_uri": uri,
            "info": {
                "title": graphql_doc.title or "GraphQL API",
                "description": graphql_doc.description,
                "version": "1.0.0",
            },
            "paths": {},
            "components": {"schemas": {}},
        }
        
        # Convert types to schemas
        for gtype in getattr(graphql_doc, "types", []):
            spec["components"]["schemas"][gtype.name] = {
                "type": "object",
                "description": gtype.description,
                "properties": {
                    f.name: {"type": f.type_name, "description": f.description}
                    for f in getattr(gtype, "fields", [])
                },
            }
        
        return spec
    
    def _asyncapi_to_pseudo_openapi(self, asyncapi: dict, uri: str) -> dict:
        """Convert AsyncAPI to pseudo-OpenAPI structure."""
        info = asyncapi.get("info", {})
        spec = {
            "openapi": "3.0.0",
            "_parsed_from": "asyncapi",
            "_source_uri": uri,
            "info": {
                "title": info.get("title", "AsyncAPI"),
                "description": info.get("description"),
                "version": info.get("version", "1.0.0"),
            },
            "paths": {},
            "components": asyncapi.get("components", {"schemas": {}}),
        }
        
        # Convert channels to paths (for discoverability)
        for channel_name, channel in asyncapi.get("channels", {}).items():
            path_name = f"/channels/{channel_name}"
            spec["paths"][path_name] = {
                "get": {
                    "summary": f"Channel: {channel_name}",
                    "description": channel.get("description"),
                    "operationId": f"channel_{channel_name.replace('/', '_')}",
                },
            }
        
        return spec
    
    def _html_to_pseudo_openapi(self, html_doc, uri: str) -> dict:
        """Convert HTML API doc to pseudo-OpenAPI structure."""
        spec = {
            "openapi": "3.0.0",
            "_parsed_from": "html",
            "_source_uri": uri,
            "info": {
                "title": html_doc.title or "API from HTML",
                "description": html_doc.description,
                "version": "1.0.0",
            },
            "paths": {},
            "components": {"schemas": {}},
        }
        
        if html_doc.base_url:
            spec["servers"] = [{"url": html_doc.base_url}]
        
        # Convert endpoints to paths
        for endpoint in html_doc.endpoints:
            path = endpoint.path
            method = endpoint.method.lower()
            
            if path not in spec["paths"]:
                spec["paths"][path] = {}
            
            spec["paths"][path][method] = {
                "summary": endpoint.summary or f"{method.upper()} {path}",
                "description": endpoint.description,
                "operationId": endpoint.operation_id or f"{method}_{path.replace('/', '_')}",
            }
        
        return spec
    
    def _pdf_to_pseudo_openapi(self, pdf_doc, uri: str) -> dict:
        """Convert PDF API doc to pseudo-OpenAPI structure."""
        spec = {
            "openapi": "3.0.0",
            "_parsed_from": "pdf",
            "_source_uri": uri,
            "info": {
                "title": pdf_doc.title or "API from PDF",
                "description": pdf_doc.description,
                "version": "1.0.0",
            },
            "paths": {},
            "components": {"schemas": {}},
        }
        
        # Convert endpoints to paths
        for endpoint in getattr(pdf_doc, "endpoints", []):
            path = endpoint.path
            method = endpoint.method.lower()
            
            if path not in spec["paths"]:
                spec["paths"][path] = {}
            
            spec["paths"][path][method] = {
                "summary": endpoint.summary or f"{method.upper()} {path}",
                "description": endpoint.description,
                "operationId": getattr(endpoint, "operation_id", None) or f"{method}_{path.replace('/', '_')}",
            }
        
        return spec
    
    def _sanitize_large_ints(self, obj, path: str = ""):
        """Recursively sanitize integers that exceed 64-bit range."""
        if isinstance(obj, bool):
            return obj
        elif isinstance(obj, int):
            if obj > INT64_MAX:
                logger.debug(f"Clamping large int at {path}: {obj} -> {INT64_MAX}")
                return INT64_MAX
            elif obj < INT64_MIN:
                logger.debug(f"Clamping small int at {path}: {obj} -> {INT64_MIN}")
                return INT64_MIN
            return obj
        elif isinstance(obj, dict):
            return {k: self._sanitize_large_ints(v, f"{path}.{k}") for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._sanitize_large_ints(v, f"{path}[{i}]") for i, v in enumerate(obj)]
        return obj
    
    def _extract_metadata(self, spec: dict) -> dict:
        """Extract metadata from parsed spec."""
        info = spec.get("info", {})
        return {
            "title": info.get("title"),
            "version": info.get("version"),
            "description": info.get("description"),
            "parsed_from": spec.get("_parsed_from", "openapi"),
            "path_count": len(spec.get("paths", {})),
            "schema_count": len(spec.get("components", {}).get("schemas", {})),
        }
