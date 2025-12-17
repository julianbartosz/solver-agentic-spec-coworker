# Entrypoint API

The main entry point for programmatic use of Integration Co-Worker.

## `design_and_generate_integration`

The primary function for running integration workflows.

```python
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

result = design_and_generate_integration(
    spec_refs=["path/to/openapi.yaml"],
    task_description="Create a payment checkout session",
    provider_code="stripe",  # optional
    repo_root="/path/to/repo",  # optional
    options=IntegrationOptions(dry_run=True)
)
```

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `spec_refs` | `list[str]` | List of paths or URLs to API specs |
| `task_description` | `str` | Natural language description of the task |
| `provider_code` | `str \| None` | Provider identifier (auto-detected if not provided) |
| `repo_root` | `str \| None` | Path to target repository for integration |
| `repo_profile` | `str \| None` | Repository profile name |
| `options` | `IntegrationOptions \| None` | Additional configuration options |

### Returns

Returns an `IntegrationResult` object:

```python
@dataclass
class IntegrationResult:
    run_id: str
    status: str  # "SUCCESS", "PARTIAL", "FAILED"
    artifacts: list[CodeArtifact]
    report: str  # Markdown report
    repo_changes: RepoChangeSet | None
    metrics: dict
```

### Example: Dry Run

```python
from integration_coworker.api.entrypoint import design_and_generate_integration
from integration_coworker.api.types import IntegrationOptions

result = design_and_generate_integration(
    spec_refs=["specs/stripe_api.json"],
    task_description="Create a checkout session with line items",
    options=IntegrationOptions(dry_run=True)
)

print(f"Status: {result.status}")
print(f"Artifacts: {len(result.artifacts)}")
for artifact in result.artifacts:
    print(f"  - {artifact.file_path}: {len(artifact.content)} bytes")
```

### Example: With Repository Integration

```python
result = design_and_generate_integration(
    spec_refs=["specs/stripe_api.json"],
    task_description="Create a checkout session",
    provider_code="stripe",
    repo_root="/path/to/my-app",
    repo_profile="fastapi",
    options=IntegrationOptions(
        dry_run=False,
        persist=True
    )
)

if result.repo_changes:
    for change in result.repo_changes.changes:
        print(f"{change.action}: {change.path}")
```

### Example: Multiple Specs

```python
result = design_and_generate_integration(
    spec_refs=[
        "specs/stripe_api.json",
        "specs/twilio_messaging_v1.json"
    ],
    task_description="Process payment and send SMS confirmation"
)
```

## Error Handling

The function may raise:

- `ValueError`: Invalid inputs (missing spec, empty task)
- `IntegrationError`: Workflow execution errors
- `DatabaseError`: Persistence failures

```python
from integration_coworker.runtime.exceptions import IntegrationError

try:
    result = design_and_generate_integration(...)
except IntegrationError as e:
    print(f"Integration failed: {e}")
```

---

::: integration_coworker.api.entrypoint.design_and_generate_integration
    options:
      show_source: true
      show_root_heading: true

---

[Back to Workflows](../user-guide/workflows.md) | [Types →](types.md)
