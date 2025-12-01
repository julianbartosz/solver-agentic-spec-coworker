# Appendix: Repository Profiles v2 — Detection & Inference

> **Last Updated:** This document describes the two-layer RepoProfile detection and inference system implemented in `src/integration_coworker/repo/detection.py`.

---

## Overview

The Repository Profile system enables automatic detection of a target repository's structure, framework, and conventions. This allows the Integration Co-Worker to place generated code in framework-appropriate locations without requiring manual configuration.

### Two-Layer Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     get_repo_profile()                         │
│              Main entry point (public API)                      │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│           Layer 1: detect_repo_profile()                        │
│     ─────────────────────────────────────────────────           │
│     • Scans repo structure for known framework patterns         │
│     • Analyzes dependencies (pyproject.toml, package.json)      │
│     • Checks for marker files (manage.py, next.config.js)       │
│     • Returns DetectedProfile with confidence score             │
└─────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│           Layer 2: build_effective_repo_profile()               │
│     ─────────────────────────────────────────────────           │
│     • High confidence → use archetype defaults                  │
│     • Medium confidence → archetype + heuristic refinement      │
│     • Low confidence → full heuristic inference                 │
│     • Very low + LLM enabled → LLM-assisted refinement          │
│     • Returns RepoProfile with layout configuration             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Known Archetypes

The system recognizes these framework archetypes with predefined layouts:

| Archetype | Language | Detection Signals | Default Integrations Root |
|-----------|----------|-------------------|--------------------------|
| `fastapi` | Python | `fastapi` dep, `main.py` with FastAPI import | `app/integrations` |
| `django` | Python | `django` dep, `manage.py` | `integrations` |
| `flask` | Python | `flask` dep, `app.py` with Flask import | `app/integrations` |
| `nextjs` | TypeScript | `next` dep, `next.config.js` | `lib/integrations` |
| `nestjs` | TypeScript | `@nestjs/core` dep, `nest-cli.json` | `src/integrations` |
| `express` | TypeScript | `express` dep, Express import in app files | `src/integrations` |

---

## Confidence Thresholds

```python
HIGH_CONFIDENCE_THRESHOLD = 0.8   # Use archetype defaults directly
LOW_CONFIDENCE_THRESHOLD = 0.4    # Log warning, use heuristic_fallback source
VERY_LOW_CONFIDENCE_THRESHOLD = 0.3  # Try LLM refinement if enabled
```

### Profile Sources

The `profile_source` field indicates how the profile was determined:

| Source | Meaning |
|--------|---------|
| `archetype` | High-confidence match, using archetype defaults |
| `archetype+heuristic` | Archetype detected but refined with actual repo structure |
| `heuristic` | Inferred from repo structure (no archetype match, medium confidence) |
| `heuristic_fallback` | Low confidence fallback (⚠️ may be unreliable) |
| `llm` | LLM-assisted refinement for very uncertain cases |
| `cached` | Loaded from previous run cache |

---

## Detection Evidence

The detection layer collects evidence of detected patterns:

```python
DetectedProfile(
    archetype_name="fastapi",
    language="python",
    confidence=0.95,
    evidence=[
        "Found app/main.py",
        "Dependency: fastapi",
        "Dependency: uvicorn",
        "FastAPI import in app/main.py",
    ],
    detected_paths={
        "pyproject_toml": "/path/to/repo/pyproject.toml",
        "fastapi_marker": "/path/to/repo/app/main.py",
    },
)
```

---

## Report Output

When repo_integration_enabled is true, the report includes:

```markdown
## Repository Profile

**Profile Name**: `fastapi`
**Framework**: `fastapi`
**Detected Archetype**: `fastapi`
**Language**: `python`
**Detection Confidence**: 95% (High)
**Profile Source**: Used archetype defaults (high confidence)

### Detection Evidence
- Found app/main.py
- Dependency: fastapi
- Dependency: uvicorn
- FastAPI import in app/main.py

### Layout Configuration
- Integrations root: `app/integrations`
- Tests root: `tests/integrations`
- Clients dir: `app/integrations/clients`
- Flows/Services dir: `app/integrations/services`
```

### Low Confidence Warning

For low confidence detections, the report includes:

```markdown
**Detection Confidence**: 30% (Low)
**Profile Source**: ⚠️ Heuristic fallback (low confidence detection)

> ⚠️ **Low Detection Confidence**: Layout inference may be unreliable. 
> Consider providing an explicit `repo_profile` to ensure correct file placement.
```

---

## Usage

### Automatic Detection (Recommended)

```python
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

result = design_and_generate_integration(
    spec_refs=["openapi.yaml"],
    task_description="Create payment flow",
    repo_root="/path/to/fastapi-project",
    repo_profile=None,  # Auto-detect
    options=IntegrationOptions(
        repo_integration_enabled=True,
    ),
)
```

### Direct Detection API

```python
from integration_coworker.repo.detection import (
    detect_repo_profile,
    build_effective_repo_profile,
    get_repo_profile,
)

# Two-step approach (for inspection)
detected = detect_repo_profile("/path/to/repo")
print(f"Detected: {detected.archetype_name} ({detected.confidence:.0%})")
print(f"Evidence: {detected.evidence}")

profile = build_effective_repo_profile(detected, "/path/to/repo")
print(f"Profile: {profile.name}")
print(f"Integrations: {profile.integrations_root}")

# One-step approach (convenience)
profile = get_repo_profile("/path/to/repo")
```

---

## Extending Archetypes

To add a new framework archetype, add to `KNOWN_ARCHETYPES` in `detection.py`:

```python
KNOWN_ARCHETYPES["sveltekit"] = FrameworkArchetypeConfig(
    name="sveltekit",
    framework="sveltekit",
    language="typescript",
    default_integrations_root="src/lib/integrations",
    default_tests_root="tests/integrations",
    detection_files=["svelte.config.js", "src/routes/+page.svelte"],
    detection_deps=["@sveltejs/kit", "svelte"],
)
```

---

## Testing

### Golden Repo Fixtures

Test fixtures are in `tests/fixtures/repos/`:
- `fastapi_service/` - FastAPI project structure
- `django_service/` - Django project structure
- `flask_service/` - Flask project structure
- `nextjs_app/` - Next.js project structure
- `nestjs_app/` - NestJS project structure
- `express_app/` - Express.js project structure
- `generic_python/` - Generic Python package (no framework)
- `generic_js/` - Generic JS/TS package (no framework)

### Test Files

- `tests/repo/test_detection_profiles_e2e.py` - Detection against golden repos
- `tests/repo/test_detection_edge_cases.py` - Dependency parsing edge cases
- `tests/test_end_to_end_repo_profiles.py` - Full pipeline E2E tests

### Running Tests

```bash
# All detection tests
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/repo/test_detection*.py -v

# E2E pipeline tests
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/test_end_to_end_repo_profiles.py -v

# All repo-related tests
USE_SQLITE=true USE_MOCK_LLM=true pytest tests/repo/ tests/test_end_to_end_repo_profiles.py -v
```

---

## Design Decisions

### Why Two Layers?

1. **Separation of concerns**: Detection collects evidence; inference makes decisions
2. **Debuggability**: DetectedProfile shows raw detection results for troubleshooting
3. **Extensibility**: Different inference strategies (archetype/heuristic/LLM) can be composed

### Why Confidence Thresholds?

1. **Efficiency**: High-confidence detections skip expensive heuristics
2. **Accuracy**: Low-confidence triggers warnings to alert users
3. **Fallback**: Very low confidence can trigger LLM assistance

### Why Archetype Defaults?

1. **Consistency**: Generated code follows framework conventions
2. **Speed**: Skip analysis for well-known patterns
3. **Maintainability**: Updates to archetypes benefit all projects

---

## Future Improvements

1. **Caching**: Cache DetectedProfile for subsequent runs
2. **Learning**: Use successful integrations to improve detection
3. **User Override**: Allow users to correct misdetections
4. **More Archetypes**: Add Flask-RESTful, Hono, Remix, etc.

