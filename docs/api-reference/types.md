# Types

Core data types used throughout Integration Co-Worker.

## Input Types

### `IntegrationOptions`

Configuration options for integration runs.

```python
@dataclass
class IntegrationOptions:
    dry_run: bool = False
    persist: bool = True
    verbose: bool = False
    json_output: bool = False
    use_repo: bool = False
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `dry_run` | `bool` | `False` | Don't write to DB or disk |
| `persist` | `bool` | `True` | Save results to database |
| `verbose` | `bool` | `False` | Enable verbose logging |
| `json_output` | `bool` | `False` | Output JSON instead of report |
| `use_repo` | `bool` | `False` | Enable repository integration |

## Output Types

### `IntegrationResult`

The main result type returned by `design_and_generate_integration`.

```python
@dataclass
class IntegrationResult:
    run_id: str
    status: str
    artifacts: list[CodeArtifact]
    report: str
    repo_changes: RepoChangeSet | None
    metrics: dict
```

| Field | Type | Description |
|-------|------|-------------|
| `run_id` | `str` | Unique identifier for the run |
| `status` | `str` | "SUCCESS", "PARTIAL", or "FAILED" |
| `artifacts` | `list[CodeArtifact]` | Generated code artifacts |
| `report` | `str` | Human-readable markdown report |
| `repo_changes` | `RepoChangeSet \| None` | Repository changes if repo integration enabled |
| `metrics` | `dict` | Timing and performance metrics |

### `CodeArtifact`

A generated code file.

```python
@dataclass
class CodeArtifact:
    id: int | None = None
    artifact_type: str = ""  # "client", "flow", "test"
    file_path: str = ""
    content: str = ""
    language: str = "python"
    created_at: datetime | None = None
```

### `RepoChangeSet`

A set of changes to be applied to a repository.

```python
@dataclass
class RepoChangeSet:
    changes: list[RepoChange]
    summary: str
```

### `RepoChange`

An individual file change.

```python
@dataclass
class RepoChange:
    action: str  # "create", "update", "delete"
    path: str
    content: str | None
```

## Domain Models

### Silver Layer (API Surface)

```python
@dataclass
class Endpoint:
    id: int | None = None
    path: str = ""
    method: str = ""
    operation_id: str = ""
    summary: str = ""
    parameters: list[EndpointParameter] = field(default_factory=list)
    request_schema_id: int | None = None
    response_schema_id: int | None = None

@dataclass
class Schema:
    id: int | None = None
    name: str = ""
    schema_type: str = ""  # "object", "array", "string", etc.
    fields: list[SchemaField] = field(default_factory=list)

@dataclass
class Entity:
    id: int | None = None
    name: str = ""
    entity_type: str = ""
    description: str = ""
```

### Gold Layer (Task-Specific)

```python
@dataclass
class IntegrationTask:
    id: int | None = None
    task_slug: str = ""
    task_description: str = ""
    constraints: dict = field(default_factory=dict)
    target_endpoints: list[int] = field(default_factory=list)

@dataclass
class IntegrationFlowNode:
    id: int | None = None
    node_type: str = ""  # "validate", "call", "transform", "return"
    node_name: str = ""
    config: dict = field(default_factory=dict)

@dataclass
class IntegrationFlowEdge:
    id: int | None = None
    source_node_id: int = 0
    target_node_id: int = 0
    condition: str | None = None

@dataclass
class Policy:
    id: int | None = None
    policy_type: str = ""  # "AUTH", "RETRY", "RATE_LIMIT", "LOGGING", "IDEMPOTENCY"
    config: dict = field(default_factory=dict)
```

## Repository Types

### `RepoProfile`

Repository configuration for code placement.

```python
@dataclass
class RepoProfile:
    archetype: str = ""  # "fastapi", "django-rest", "flask", etc.
    language: str = "python"
    integrations_root: str = "src/integrations"
    clients_dir: str = "clients"
    flows_dir: str = "flows"
    tests_dir: str = "tests/integrations"
    conventions: dict = field(default_factory=dict)
```

---

::: integration_coworker.api.types
    options:
      show_source: true
      members:
        - IntegrationOptions
        - IntegrationResult

---

[Back to Entrypoint](entrypoint.md) | [WorkflowState →](workflow-state.md)
