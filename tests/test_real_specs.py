"""
Parameterized tests for real-world OpenAPI specs.

Tests the full pipeline against a diverse corpus of 14+ real-world API specs
from major providers. Includes complexity metrics analysis.

Spec Categories:
- AI/ML: OpenAI
- Payments: Stripe, Plaid
- Communications: Twilio, Slack, Zoom
- Cloud: DigitalOcean, Box
- Developer Tools: GitHub, CircleCI
- Marketing: Mailchimp
- Productivity: Asana
- E-commerce: Spotify
- Testing: Petstore, httpbin

Covers:
- Format detection (YAML, JSON)
- OpenAPI version handling (3.0.x, 3.1.x)
- Authentication schemes (apiKey, OAuth2, Bearer, Basic)
- Scale: from 19 endpoints (Petstore) to 1106 endpoints (GitHub)
- Schema complexity: from 0 schemas (httpbin) to 1278 schemas (Plaid)
"""
import pytest
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, List, Optional
from enum import Enum

import yaml

from integration_coworker.graph.state import WorkflowState
from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
from integration_coworker.graph.nodes.ingest_spec import ingest_spec
from integration_coworker.graph.nodes.build_silver_api_model import build_silver_api_model
from integration_coworker.api.types import IntegrationOptions


# =============================================================================
# Spec Complexity Metrics
# =============================================================================

class AuthType(Enum):
    """Authentication scheme types."""
    API_KEY = "apiKey"
    OAUTH2 = "oauth2"
    BEARER = "bearer"
    BASIC = "basic"
    HTTP = "http"
    OPEN_ID_CONNECT = "openIdConnect"
    NONE = "none"


class SpecCategory(Enum):
    """API category classification."""
    AI_ML = "AI/ML"
    PAYMENTS = "Payments"
    COMMUNICATIONS = "Communications"
    CLOUD = "Cloud"
    DEVELOPER_TOOLS = "Developer Tools"
    MARKETING = "Marketing"
    PRODUCTIVITY = "Productivity"
    ENTERTAINMENT = "Entertainment"
    TESTING = "Testing"
    GENERAL = "General"


@dataclass
class SpecMetrics:
    """Complexity metrics for an OpenAPI spec."""
    name: str
    file: str
    format: str  # yaml or json
    
    # Size metrics
    file_size_kb: float = 0.0
    
    # Version
    openapi_version: str = ""
    
    # Endpoint metrics
    endpoint_count: int = 0
    methods_used: List[str] = field(default_factory=list)
    path_depth_max: int = 0
    path_depth_avg: float = 0.0
    
    # Schema metrics
    schema_count: int = 0
    schema_refs_count: int = 0  # Total $ref usages
    max_schema_depth: int = 0
    
    # Auth metrics
    auth_schemes: List[str] = field(default_factory=list)
    auth_types: List[AuthType] = field(default_factory=list)
    
    # Classification
    category: SpecCategory = SpecCategory.GENERAL
    
    # Known issues
    known_issues: List[str] = field(default_factory=list)
    
    # Expectations for validation
    min_endpoints: int = 0
    min_schemas: int = 0


def calculate_spec_metrics(file_path: Path, spec_info: Dict[str, Any]) -> SpecMetrics:
    """Calculate complexity metrics for a spec file."""
    metrics = SpecMetrics(
        name=spec_info["name"],
        file=spec_info["file"],
        format=spec_info["format"],
        category=spec_info.get("category", SpecCategory.GENERAL),
        known_issues=spec_info.get("known_issues", []),
        min_endpoints=spec_info.get("expected", {}).get("min_endpoints", 0),
        min_schemas=spec_info.get("expected", {}).get("min_schemas", 0),
    )
    
    # File size
    metrics.file_size_kb = round(file_path.stat().st_size / 1024, 1)
    
    # Parse spec
    content = file_path.read_text()
    if metrics.format == "json":
        spec = json.loads(content)
    else:
        spec = yaml.safe_load(content)
    
    # Version
    metrics.openapi_version = spec.get("openapi", spec.get("swagger", "unknown"))
    
    # Endpoint metrics
    paths = spec.get("paths", {})
    methods_seen = set()
    path_depths = []
    
    for path_key, path_item in paths.items():
        # Path depth (count of / segments)
        depth = path_key.count("/")
        path_depths.append(depth)
        
        for method in ["get", "post", "put", "patch", "delete", "head", "options"]:
            if method in path_item:
                metrics.endpoint_count += 1
                methods_seen.add(method.upper())
    
    metrics.methods_used = sorted(methods_seen)
    if path_depths:
        metrics.path_depth_max = max(path_depths)
        metrics.path_depth_avg = round(sum(path_depths) / len(path_depths), 1)
    
    # Schema metrics
    schemas = spec.get("components", {}).get("schemas", {}) or spec.get("definitions", {})
    metrics.schema_count = len(schemas)
    
    # Count $ref usages (rough complexity indicator)
    content_str = json.dumps(spec) if metrics.format == "json" else content
    metrics.schema_refs_count = content_str.count('"$ref"')
    
    # Estimate max schema depth by looking at nested properties
    def get_schema_depth(schema_def: dict, current_depth: int = 1, max_depth: int = 10) -> int:
        if current_depth > max_depth or not isinstance(schema_def, dict):
            return current_depth
        props = schema_def.get("properties", {})
        if not props:
            return current_depth
        max_child_depth = current_depth
        for prop_def in props.values():
            if isinstance(prop_def, dict):
                child_depth = get_schema_depth(prop_def, current_depth + 1, max_depth)
                max_child_depth = max(max_child_depth, child_depth)
        return max_child_depth
    
    for schema_def in schemas.values():
        if isinstance(schema_def, dict):
            depth = get_schema_depth(schema_def)
            metrics.max_schema_depth = max(metrics.max_schema_depth, depth)
    
    # Auth metrics
    sec_schemes = spec.get("components", {}).get("securitySchemes", {}) or spec.get("securityDefinitions", {})
    metrics.auth_schemes = list(sec_schemes.keys())
    
    for scheme_name, scheme_def in sec_schemes.items():
        if isinstance(scheme_def, dict):
            scheme_type = scheme_def.get("type", "").lower()
            if scheme_type == "apikey":
                metrics.auth_types.append(AuthType.API_KEY)
            elif scheme_type == "oauth2":
                metrics.auth_types.append(AuthType.OAUTH2)
            elif scheme_type == "http":
                scheme_subtype = scheme_def.get("scheme", "").lower()
                if scheme_subtype == "bearer":
                    metrics.auth_types.append(AuthType.BEARER)
                elif scheme_subtype == "basic":
                    metrics.auth_types.append(AuthType.BASIC)
                else:
                    metrics.auth_types.append(AuthType.HTTP)
            elif scheme_type == "openidconnect":
                metrics.auth_types.append(AuthType.OPEN_ID_CONNECT)
    
    if not metrics.auth_types:
        metrics.auth_types.append(AuthType.NONE)
    
    return metrics


# =============================================================================
# Spec Corpus Definition - 14 Real-World Specs
# =============================================================================

SPECS_DIR = Path(__file__).parent.parent / "specs"

# Comprehensive corpus with diverse characteristics
SPEC_CORPUS = [
    # === AI/ML ===
    {
        "name": "OpenAI API",
        "file": "openai_api.yaml",
        "format": "yaml",
        "category": SpecCategory.AI_ML,
        "expected": {
            "min_endpoints": 100,
            "min_schemas": 200,
            "has_auth": True,
        },
        "known_issues": [
            "Bug #78/#79: Large integers in seed.minimum/maximum (sanitized)",
            "OpenAPI 3.1.0 (latest version)",
        ],
    },
    
    # === Payments ===
    {
        "name": "Stripe API",
        "file": "stripe_api.json",
        "format": "json",
        "category": SpecCategory.PAYMENTS,
        "expected": {
            "min_endpoints": 500,
            "min_schemas": 1000,
            "has_auth": True,
        },
        "known_issues": [
            "Very large spec (~7MB)",
            "Complex polymorphic schemas",
        ],
    },
    {
        "name": "Plaid API",
        "file": "plaid_api.yaml",
        "format": "yaml",
        "category": SpecCategory.PAYMENTS,
        "expected": {
            "min_endpoints": 150,
            "min_schemas": 1000,
            "has_auth": True,
        },
        "known_issues": [
            "Multiple auth schemes (API key, client credentials)",
        ],
    },
    
    # === Communications ===
    {
        "name": "Twilio Messaging",
        "file": "twilio_messaging_v1.json",
        "format": "json",
        "category": SpecCategory.COMMUNICATIONS,
        "expected": {
            "min_endpoints": 40,
            "min_schemas": 30,
            "has_auth": True,
        },
        "known_issues": [],
    },
    {
        "name": "Slack API",
        "file": "slack_api.yaml",
        "format": "yaml",
        "category": SpecCategory.COMMUNICATIONS,
        "expected": {
            "min_endpoints": 100,
            "min_schemas": 40,
            "has_auth": True,
        },
        "known_issues": [],
    },
    {
        "name": "Zoom API",
        "file": "zoom_api.yaml",
        "format": "yaml",
        "category": SpecCategory.COMMUNICATIONS,
        "expected": {
            "min_endpoints": 300,
            "min_schemas": 100,
            "has_auth": True,
        },
        "known_issues": [
            "Multiple OAuth2 flows",
            "Large spec (~5MB)",
        ],
    },
    
    # === Cloud ===
    {
        "name": "DigitalOcean API",
        "file": "digitalocean_api.yaml",
        "format": "yaml",
        "category": SpecCategory.CLOUD,
        "expected": {
            "min_endpoints": 200,
            "min_schemas": 0,  # Uses inline schemas
            "has_auth": True,
        },
        "known_issues": [
            "Heavy use of inline schemas (0 named schemas)",
        ],
    },
    {
        "name": "Box API",
        "file": "box_api.yaml",
        "format": "yaml",
        "category": SpecCategory.CLOUD,
        "expected": {
            "min_endpoints": 200,
            "min_schemas": 150,
            "has_auth": True,
        },
        "known_issues": [],
    },
    
    # === Developer Tools ===
    {
        "name": "GitHub REST API",
        "file": "github_api.json",
        "format": "json",
        "category": SpecCategory.DEVELOPER_TOOLS,
        "expected": {
            "min_endpoints": 1000,
            "min_schemas": 800,
            "has_auth": False,  # Uses different auth mechanism
        },
        "known_issues": [
            "Largest spec in corpus (~11MB)",
            "1100+ endpoints",
            "OpenAPI 3.0.3",
        ],
    },
    {
        "name": "CircleCI API",
        "file": "circleci_api.yaml",
        "format": "yaml",
        "category": SpecCategory.DEVELOPER_TOOLS,
        "expected": {
            "min_endpoints": 15,
            "min_schemas": 20,
            "has_auth": True,
        },
        "known_issues": [
            "Smaller, simpler spec (~27KB)",
        ],
    },
    
    # === Marketing ===
    {
        "name": "Mailchimp API",
        "file": "mailchimp_api.yaml",
        "format": "yaml",
        "category": SpecCategory.MARKETING,
        "expected": {
            "min_endpoints": 200,
            "min_schemas": 0,  # Uses inline schemas
            "has_auth": True,
        },
        "known_issues": [
            "Very large spec (~10MB)",
            "Heavy use of inline schemas",
        ],
    },
    
    # === Productivity ===
    {
        "name": "Asana API",
        "file": "asana_api.yaml",
        "format": "yaml",
        "category": SpecCategory.PRODUCTIVITY,
        "expected": {
            "min_endpoints": 100,
            "min_schemas": 100,
            "has_auth": True,
        },
        "known_issues": [],
    },
    
    # === Entertainment ===
    {
        "name": "Spotify API",
        "file": "spotify_api.yaml",
        "format": "yaml",
        "category": SpecCategory.ENTERTAINMENT,
        "expected": {
            "min_endpoints": 50,
            "min_schemas": 50,
            "has_auth": True,
        },
        "known_issues": [
            "OAuth2 authorization code flow",
        ],
    },
    
    # === Testing ===
    {
        "name": "Petstore v3",
        "file": "petstore_v3.json",
        "format": "json",
        "category": SpecCategory.TESTING,
        "expected": {
            "min_endpoints": 15,
            "min_schemas": 5,
            "has_auth": True,
        },
        "known_issues": [
            "Canonical OpenAPI example spec",
            "Used for testing OpenAPI tooling",
        ],
    },
    {
        "name": "httpbin",
        "file": "httpbin_api.json",
        "format": "json",
        "category": SpecCategory.TESTING,
        "expected": {
            "min_endpoints": 50,
            "min_schemas": 0,  # No schemas
            "has_auth": False,
        },
        "known_issues": [
            "No authentication required",
            "No named schemas (all inline)",
        ],
    },
]


def get_spec_ids() -> List[str]:
    """Generate test IDs from spec corpus."""
    return [s["name"] for s in SPEC_CORPUS]


def get_spec_path(filename: str) -> Path:
    """Get absolute path to spec file."""
    path = SPECS_DIR / filename
    if not path.exists():
        pytest.skip(f"Spec file not found: {path}")
    return path


# =============================================================================
# Spec Loading Tests
# =============================================================================

class TestSpecLoading:
    """Test basic spec file loading and format detection."""

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_spec_file_exists(self, spec_info: Dict[str, Any]):
        """Verify all specs in corpus exist."""
        path = SPECS_DIR / spec_info["file"]
        assert path.exists(), f"Spec file missing: {spec_info['file']}"

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_spec_file_parses(self, spec_info: Dict[str, Any]):
        """Verify specs parse without errors."""
        path = get_spec_path(spec_info["file"])
        content = path.read_text()
        
        if spec_info["format"] == "json":
            parsed = json.loads(content)
        else:
            parsed = yaml.safe_load(content)
        
        assert isinstance(parsed, dict)
        assert "openapi" in parsed or "swagger" in parsed, \
            f"{spec_info['name']} should be an OpenAPI spec"

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_openapi_version_valid(self, spec_info: Dict[str, Any]):
        """Verify OpenAPI version is recognized."""
        path = get_spec_path(spec_info["file"])
        content = path.read_text()
        
        if spec_info["format"] == "json":
            parsed = json.loads(content)
        else:
            parsed = yaml.safe_load(content)
        
        version = parsed.get("openapi", parsed.get("swagger", ""))
        assert version, f"{spec_info['name']} has no version"
        assert version.startswith(("2.", "3.0", "3.1")), \
            f"{spec_info['name']} has unknown version: {version}"


# =============================================================================
# Complexity Metrics Tests
# =============================================================================

class TestSpecMetrics:
    """Calculate and validate spec complexity metrics."""

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_calculate_metrics(self, spec_info: Dict[str, Any]):
        """Calculate and report metrics for each spec."""
        path = get_spec_path(spec_info["file"])
        metrics = calculate_spec_metrics(path, spec_info)
        
        # Validate against expected minimums
        min_endpoints = spec_info["expected"]["min_endpoints"]
        assert metrics.endpoint_count >= min_endpoints, \
            f"{metrics.name}: Expected >= {min_endpoints} endpoints, got {metrics.endpoint_count}"
        
        min_schemas = spec_info["expected"]["min_schemas"]
        assert metrics.schema_count >= min_schemas, \
            f"{metrics.name}: Expected >= {min_schemas} schemas, got {metrics.schema_count}"
        
        # Report metrics
        print(f"\n=== {metrics.name} ===")
        print(f"  Category: {metrics.category.value}")
        print(f"  File: {metrics.file} ({metrics.file_size_kb} KB)")
        print(f"  OpenAPI Version: {metrics.openapi_version}")
        print(f"  Endpoints: {metrics.endpoint_count}")
        print(f"  Methods: {', '.join(metrics.methods_used)}")
        print(f"  Path Depth: max={metrics.path_depth_max}, avg={metrics.path_depth_avg}")
        print(f"  Schemas: {metrics.schema_count}")
        print(f"  $ref Count: {metrics.schema_refs_count}")
        print(f"  Max Schema Depth: {metrics.max_schema_depth}")
        print(f"  Auth Schemes: {metrics.auth_schemes}")
        print(f"  Auth Types: {[t.value for t in metrics.auth_types]}")
        if metrics.known_issues:
            print(f"  Known Issues: {len(metrics.known_issues)}")

    def test_corpus_diversity(self):
        """Verify the corpus covers diverse characteristics."""
        categories = set()
        formats = set()
        auth_types = set()
        total_endpoints = 0
        total_schemas = 0
        
        for spec_info in SPEC_CORPUS:
            path = get_spec_path(spec_info["file"])
            metrics = calculate_spec_metrics(path, spec_info)
            
            categories.add(metrics.category)
            formats.add(metrics.format)
            for at in metrics.auth_types:
                auth_types.add(at)
            total_endpoints += metrics.endpoint_count
            total_schemas += metrics.schema_count
        
        # Assert diversity
        assert len(categories) >= 5, f"Need >= 5 categories, got {len(categories)}"
        assert "yaml" in formats and "json" in formats, "Need both YAML and JSON formats"
        assert len(auth_types) >= 3, f"Need >= 3 auth types, got {len(auth_types)}"
        assert total_endpoints >= 3000, f"Expected >= 3000 total endpoints, got {total_endpoints}"
        assert total_schemas >= 5000, f"Expected >= 5000 total schemas, got {total_schemas}"
        
        print(f"\n=== Corpus Diversity Summary ===")
        print(f"  Categories: {len(categories)} ({[c.value for c in categories]})")
        print(f"  Formats: {formats}")
        print(f"  Auth Types: {[t.value for t in auth_types]}")
        print(f"  Total Endpoints: {total_endpoints}")
        print(f"  Total Schemas: {total_schemas}")


# =============================================================================
# Silver Model Extraction Tests
# =============================================================================

class TestSilverModelExtraction:
    """Test Silver model extraction from parsed specs."""

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_endpoint_extraction(self, spec_info: Dict[str, Any]):
        """Verify endpoint extraction meets minimums."""
        path = get_spec_path(spec_info["file"])
        content = path.read_text()
        
        if spec_info["format"] == "json":
            parsed = json.loads(content)
        else:
            parsed = yaml.safe_load(content)
        
        # Count endpoints
        paths = parsed.get("paths", {})
        endpoint_count = sum(
            1 for path_item in paths.values()
            for method in ["get", "post", "put", "patch", "delete", "head", "options"]
            if method in path_item
        )
        
        min_expected = spec_info["expected"]["min_endpoints"]
        assert endpoint_count >= min_expected, \
            f"{spec_info['name']}: Expected >= {min_expected} endpoints, got {endpoint_count}"

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_schema_extraction(self, spec_info: Dict[str, Any]):
        """Verify schema extraction meets minimums."""
        path = get_spec_path(spec_info["file"])
        content = path.read_text()
        
        if spec_info["format"] == "json":
            parsed = json.loads(content)
        else:
            parsed = yaml.safe_load(content)
        
        schemas = parsed.get("components", {}).get("schemas", {}) or parsed.get("definitions", {})
        schema_count = len(schemas)
        
        min_expected = spec_info["expected"]["min_schemas"]
        assert schema_count >= min_expected, \
            f"{spec_info['name']}: Expected >= {min_expected} schemas, got {schema_count}"


# =============================================================================
# Auth Scheme Tests
# =============================================================================

class TestAuthSchemes:
    """Test authentication scheme detection."""

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_has_expected_auth(self, spec_info: Dict[str, Any]):
        """Verify auth scheme presence matches expectations."""
        path = get_spec_path(spec_info["file"])
        content = path.read_text()
        
        if spec_info["format"] == "json":
            parsed = json.loads(content)
        else:
            parsed = yaml.safe_load(content)
        
        has_security = bool(
            parsed.get("security") or
            parsed.get("components", {}).get("securitySchemes") or
            parsed.get("securityDefinitions")
        )
        
        expected_has_auth = spec_info["expected"]["has_auth"]
        assert has_security == expected_has_auth, \
            f"{spec_info['name']}: Expected has_auth={expected_has_auth}, got {has_security}"


# =============================================================================
# Pipeline Integration Tests
# =============================================================================

class TestPipelineIntegration:
    """Test full ingest → parse → Silver pipeline."""

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_ingest_spec(self, spec_info: Dict[str, Any]):
        """Test ingest_spec node with real specs."""
        path = get_spec_path(spec_info["file"])
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[str(path)],
            task_description=f"Test {spec_info['name']}",
            provider_code=f"test_{spec_info['file'].replace('.', '_')}",
            options=IntegrationOptions(dry_run=True),
        )
        
        result = ingest_spec(state)
        
        assert result.pending_specs or result.openapi_spec or result.doc_chunks, \
            f"{spec_info['name']}: Ingest should load spec content"

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_detect_and_parse_spec(self, spec_info: Dict[str, Any]):
        """Test detect_and_parse_spec node with real specs."""
        path = get_spec_path(spec_info["file"])
        raw_content = path.read_text()
        
        # Pre-populate pending_specs to simulate ingest output
        state = WorkflowState(
            source_refs=[],
            spec_refs=[str(path)],
            task_description=f"Test {spec_info['name']}",
            provider_code=f"test_{spec_info['file'].replace('.', '_')}",
            options=IntegrationOptions(dry_run=True),
            pending_specs=[{"ref": str(path), "content": raw_content}],
            plan={"openapi_specs": [str(path)]},
        )
        
        result = detect_and_parse_spec(state)
        
        assert not result.errors, f"{spec_info['name']}: Parse errors: {result.errors}"
        assert result.openapi_spec is not None or result.parsed_specs, \
            f"{spec_info['name']}: Should have parsed spec"

    @pytest.mark.parametrize("spec_info", SPEC_CORPUS, ids=get_spec_ids())
    def test_full_silver_pipeline(self, spec_info: Dict[str, Any]):
        """Test complete ingest → parse → build_silver pipeline."""
        path = get_spec_path(spec_info["file"])
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[str(path)],
            task_description=f"Integration with {spec_info['name']}",
            provider_code=f"test_{spec_info['file'].replace('.', '_')}",
            options=IntegrationOptions(dry_run=True),
        )
        
        # Step 1: Ingest
        state = ingest_spec(state)
        assert state.pending_specs or state.openapi_spec or state.doc_chunks, "Ingest failed"
        
        # Step 2: Detect and parse
        state = detect_and_parse_spec(state)
        assert not state.errors, f"Parse errors: {state.errors}"
        
        # Step 3: Build Silver model
        state = build_silver_api_model(state)
        
        # Validate Silver model
        min_endpoints = spec_info["expected"]["min_endpoints"]
        assert len(state.endpoints) >= min_endpoints, \
            f"{spec_info['name']}: Expected >= {min_endpoints} endpoints, got {len(state.endpoints)}"
        
        # Report extraction results
        print(f"\n{spec_info['name']} Silver Model:")
        print(f"  Endpoints: {len(state.endpoints)}")
        print(f"  Schemas: {len(state.schemas)}")
        print(f"  Entities: {len(state.entities)}")
        print(f"  Parameters: {len(state.endpoint_parameters)}")


# =============================================================================
# Bug Regression Tests
# =============================================================================

class TestBugRegressions:
    """Regression tests for known bugs."""

    def test_bug_78_79_large_integers(self):
        """
        Bug #78/#79: OpenAI spec has seed.minimum/maximum exceeding INT64.
        
        Verify integer sanitization clamps these values.
        """
        from integration_coworker.graph.nodes.detect_and_parse_spec import _sanitize_large_ints
        
        test_data = {
            "seed": {
                "minimum": -9223372036854776000,
                "maximum": 9223372036854776000,
            },
            "normal_int": 42,
            "nested": {
                "also_large": 10000000000000000000,
            }
        }
        
        sanitized = _sanitize_large_ints(test_data)
        
        INT64_MAX = 9223372036854775807
        INT64_MIN = -9223372036854775808
        
        assert sanitized["seed"]["minimum"] == INT64_MIN
        assert sanitized["seed"]["maximum"] == INT64_MAX
        assert sanitized["normal_int"] == 42
        assert sanitized["nested"]["also_large"] == INT64_MAX

    def test_openai_spec_parses_with_sanitization(self):
        """Verify OpenAI spec parses with integer sanitization."""
        path = get_spec_path("openai_api.yaml")
        raw_content = path.read_text()
        
        state = WorkflowState(
            source_refs=[],
            spec_refs=[str(path)],
            task_description="Test OpenAI parsing",
            provider_code="openai_bug_test",
            options=IntegrationOptions(dry_run=True),
            pending_specs=[{"ref": str(path), "content": raw_content}],
            plan={"openapi_specs": [str(path)]},
        )
        
        result = detect_and_parse_spec(state)
        
        assert not result.errors, f"OpenAI spec parsing failed: {result.errors}"


# =============================================================================
# Scale Tests
# =============================================================================

class TestScaleHandling:
    """Test handling of specs at different scales."""

    def test_smallest_spec_petstore(self):
        """Test smallest spec (Petstore ~17KB)."""
        path = get_spec_path("petstore_v3.json")
        metrics = calculate_spec_metrics(path, SPEC_CORPUS[13])  # Petstore
        
        assert metrics.file_size_kb < 50, "Petstore should be small"
        assert metrics.endpoint_count < 30, "Petstore should have few endpoints"

    def test_largest_spec_github(self):
        """Test largest spec (GitHub ~11MB)."""
        path = get_spec_path("github_api.json")
        metrics = calculate_spec_metrics(path, SPEC_CORPUS[8])  # GitHub
        
        assert metrics.file_size_kb > 10000, "GitHub should be large"
        assert metrics.endpoint_count > 1000, "GitHub should have many endpoints"

    def test_most_complex_schemas_plaid(self):
        """Test spec with most schemas (Plaid ~1278)."""
        path = get_spec_path("plaid_api.yaml")
        metrics = calculate_spec_metrics(path, SPEC_CORPUS[2])  # Plaid
        
        assert metrics.schema_count > 1000, "Plaid should have many schemas"

    def test_no_schemas_spec(self):
        """Test spec with no named schemas (httpbin)."""
        path = get_spec_path("httpbin_api.json")
        metrics = calculate_spec_metrics(path, SPEC_CORPUS[14])  # httpbin
        
        assert metrics.schema_count == 0, "httpbin should have no named schemas"


# =============================================================================
# Format Tests
# =============================================================================

class TestFormatHandling:
    """Test handling of different formats."""

    @pytest.mark.parametrize("spec_info", [s for s in SPEC_CORPUS if s["format"] == "json"], 
                             ids=[s["name"] for s in SPEC_CORPUS if s["format"] == "json"])
    def test_json_format_specs(self, spec_info: Dict[str, Any]):
        """Test all JSON format specs parse correctly."""
        path = get_spec_path(spec_info["file"])
        content = path.read_text()
        
        parsed = json.loads(content)
        assert "openapi" in parsed or "swagger" in parsed

    @pytest.mark.parametrize("spec_info", [s for s in SPEC_CORPUS if s["format"] == "yaml"], 
                             ids=[s["name"] for s in SPEC_CORPUS if s["format"] == "yaml"])
    def test_yaml_format_specs(self, spec_info: Dict[str, Any]):
        """Test all YAML format specs parse correctly."""
        path = get_spec_path(spec_info["file"])
        content = path.read_text()
        
        parsed = yaml.safe_load(content)
        assert "openapi" in parsed or "swagger" in parsed
