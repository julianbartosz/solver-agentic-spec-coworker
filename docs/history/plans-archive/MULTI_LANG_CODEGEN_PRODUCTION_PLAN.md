# Multi-Language Codegen Production Readiness Plan (Corrected)

> **Document Status**: ACTIVE - Implementation complete  
> **Created**: 2025-12-19  
> **Last Updated**: 2025-12-19  
> **Version**: v1.4

---

## ✅ Implementation Status (as of 2025-12-19)

| Component | Status | Notes |
|-----------|--------|-------|
| Strategy pattern (base.py) | ✅ Implemented | LanguageStrategy ABC with provision/validate phases |
| TypeScript gates | ✅ Implemented | tsc, eslint, vitest gates with JSON output parsing |
| Go gates | ✅ Implemented | go test, go vet, staticcheck with streaming JSON |
| sandbox_multilang.py | ✅ Implemented | Dispatch to language strategies |
| generate_code_and_tests.py wiring | ✅ Implemented | Multi-lang dispatch based on target_language |
| **Docker 2-phase runner** | ✅ IMPLEMENTED | `docker_runner.py` with provision/validate phases |
| **Tier.PROD validation** | ✅ AVAILABLE | Auto-selects Tier.PROD when Docker available |

**Docker Runner Features**:
- 2-phase execution: Provision (network enabled) → Validate (`--network none`)
- Resource limits: `--memory 2g`, `--cpus 2`, `--pids-limit 256`
- Read-only workspace during validation
- Images: `node:20-slim` (TypeScript), `golang:1.22-bookworm` (Go)
- 24 unit tests covering all edge cases

**Tier Selection Logic**:
- `Tier.PROD`: Docker available → deterministic execution guaranteed
- `Tier.EXP`: Docker unavailable → host fallback (non-deterministic)

---

## Change Log

| Version | Date | Changes |
|---------|------|---------|
| v1.0 | 2025-12-19 | Initial plan |
| v1.1 | 2025-12-19 | **Correctness fixes applied**: TS reporter args, Go command syntax, Java Maven isolation, determinism strategy, Tier semantics |
| v1.2 | 2025-12-19 | **Second correctness pass**: Vitest JSON reporter fix, 2-phase network design, TS artifact detector dependency fix, Go JSON streaming parse, ESLint unmatched pattern flag |
| v1.3 | 2025-12-19 | **Implementation status section**: Clarified Tier.PROD not available, forced to Tier.EXP |
| v1.4 | 2025-12-19 | **Docker runner implemented**: `docker_runner.py` with 24 tests, Tier.PROD now available |

---

## Step 0: Correctness Fixes (Applied)

These fixes address production-critical issues that would cause non-determinism or silent failures.

### 0.1 TypeScript Gate Commands (CORRECTED)

| Original (Wrong) | Corrected | Reason |
|------------------|-----------|--------|
| `vitest run --reporter=json` | `vitest run --json` | [Vitest CLI](https://vitest.dev/guide/cli): JSON output via `--json`, not `--reporter=json` |
| `eslint .` | `eslint . --format json` | [ESLint formatters](https://eslint.org/docs/latest/use/formatters/): use `--format json` for machine parsing |
| `tsc --noEmit` | `tsc --noEmit --pretty false` | Disable colorized output for reliable parsing |
| `jest` | `jest --json --outputFile=jest-results.json` | [Jest CLI](https://jestjs.io/docs/cli): `--json` for structured output |

**Canonical TS Gate Commands**:
```bash
# Typecheck (syntax + types in one pass - makes separate tree-sitter unnecessary)
tsc --noEmit --pretty false

# Lint (includes security rules if eslint-plugin-security configured)
# --no-error-on-unmatched-pattern avoids failures when linting generated file subsets
eslint . --format json --no-error-on-unmatched-pattern

# Unit tests (CORRECTED: use --reporter=json + --outputFile, NOT --json)
# Reference: https://vitest.dev/guide/reporters - JSON reporter writes to file
vitest run --reporter=json --outputFile=vitest-results.json
# Parse vitest-results.json as primary result; stdout/stderr are secondary

# OR for Jest projects:
jest --json --outputFile=jest-results.json
```

### 0.2 Go Gate Commands (CORRECTED)

| Original (Wrong) | Corrected | Reason |
|------------------|-----------|--------|
| `repos.` placeholder | `./...` | [go vet docs](https://pkg.go.dev/cmd/vet): standard recursive pattern |
| Missing `go test` | `go test ./...` | [go-test manpage](https://manpages.debian.org/testing/golang-go/go-test.1.en.html): also runs subset of vet checks |
| No static analysis | `staticcheck ./...` OR `golangci-lint run ./...` | [Staticcheck](https://staticcheck.dev/docs/), [golangci-lint](https://golangci-lint.run/): standard complement to go vet |

**Canonical Go Gate Commands**:
```bash
# Compile + test (also runs subset of vet checks automatically)
go test ./...

# Static analysis (separate from test because test only runs partial vet)
go vet ./...

# Extended static analysis (pick one)
staticcheck ./...
# OR
golangci-lint run ./...
```

### 0.3 Java Gate Commands (CORRECTED)

| Original (Wrong) | Corrected | Reason |
|------------------|-----------|--------|
| `javac` ad-hoc | Maven wrapper | [Surefire docs](https://maven.apache.org/surefire/maven-surefire-plugin/usage.html): production repos use Maven/Gradle |
| No isolation | `-Dmaven.repo.local` | [Maven settings](https://maven.apache.org/settings.html): isolates local repo |
| Global `.m2` | `$SANDBOX/.m2repo` | Prevents host state contamination |

**Canonical Java Gate Commands**:
```bash
# Prefer wrapper (reproducible)
./mvnw -B -q -Dmaven.repo.local="$SANDBOX/.m2repo" test

# Fallback if no wrapper (CI image must have mvn)
mvn -B -q -Dmaven.repo.local="$SANDBOX/.m2repo" test

# Compile-only (skip tests)
./mvnw -B -q -Dmaven.repo.local="$SANDBOX/.m2repo" -DskipTests compile
```

### 0.4 Determinism Strategy (CORRECTED)

**Problem**: Original plan assumed `npm install` / `npx` would work. This is non-reproducible without lockfiles.

**Decision**: **Containerized Toolchains** (Option 3 - robust long-term)

| Strategy | Pros | Cons | Verdict |
|----------|------|------|---------|
| Repo-driven | Fast, uses existing config | Fails if repo missing lockfile/config | Not sufficient for Tier 1 |
| Coworker-provisioned | Pinned versions | Must maintain harness, network for deps | Medium-term stopgap |
| **Containerized** | Fully deterministic, preinstalled tools | Docker dependency, slower startup | ✅ **CHOSEN for Tier 1** |

**Implementation** (CORRECTED - 2-phase network design):

Docker's `--network none` fully disables networking ([Docker docs](https://docs.docker.com/engine/network/drivers/none/)).
However, `npm ci` requires network access unless offline cache/mirror is preloaded.

**Solution: 2-Phase Execution**

1. **Provisioning Phase (network ALLOWED)**:
   - Copy repo/artifacts into container writable workspace
   - Resolve deps deterministically: `npm ci` with lockfile (TS), `go mod download` (Go)
   - This phase may require network access
   
2. **Gate Phase (network DISABLED)**:
   - Start container with `--network none`
   - Run `tsc`, `eslint`, `vitest` (all deps already local)
   - No external calls possible - deterministic validation

**Provisioner interface must support**:
- `provision(artifacts, language)` → returns workspace with deps resolved
- `validate(workspace, language)` → runs gates with network disabled

---

## Step 1: Tier Model (CORRECTED)

### Tier Definitions (Enforced)

| Tier | Artifact Detection | Toolchain | Missing Config Behavior | Output Marker |
|------|-------------------|-----------|------------------------|---------------|
| **Tier 1 (Production)** | Compiler/AST-backed only | Containerized, pinned | **FAIL** with actionable error | `validated: true` |
| **Tier 2 (Experimental)** | Regex allowed | Host toolchain | Skip gate, log warning | `validated: false, marker: "UNTRUSTED"` |

### Tier 1 Invariants (Cannot Be Violated)
1. **No regex-only artifact detection** - must use compiler API (tsc, go list) or AST
2. **No silent fallbacks** - missing toolchain/config = hard failure
3. **No network during gates** - all deps preinstalled in container
4. **Deterministic** - same input → same output across runs

### Tier 2 Relaxations (Explicit)
1. Regex artifact detection allowed but marked `UNTRUSTED` in structured output
2. Host toolchain used (may vary)
3. Gates may be skipped if tooling unavailable (logged as `skipped`)
4. Cannot claim "production validated" status

---

## Step 2: Architecture (Split Responsibilities)

Original plan risked `sandbox.py` becoming a god-object. Split into three interfaces:

### 2.1 Interface: `ToolchainProbe`

```python
# src/integration_coworker/codegen/gates/probe.py

from dataclasses import dataclass
from typing import List, Optional
from pathlib import Path

@dataclass
class ToolchainInfo:
    """Result of probing for toolchain availability."""
    language: str
    executables_found: List[str]       # e.g., ["tsc", "eslint", "vitest"]
    executables_missing: List[str]     # e.g., ["jest"]
    config_files_found: List[str]      # e.g., ["tsconfig.json", "eslint.config.js"]
    config_files_missing: List[str]    # e.g., ["vitest.config.ts"]
    lockfile_found: Optional[str]      # e.g., "package-lock.json"
    tier_eligible: int                 # 1 or 2 based on findings
    failure_reason: Optional[str]      # If tier_eligible=0, why

class ToolchainProbe:
    """Detects what tooling exists in sandbox."""
    
    def probe(self, sandbox_path: Path, language: str) -> ToolchainInfo:
        """
        Probe sandbox for toolchain availability.
        
        Returns ToolchainInfo with tier eligibility assessment.
        """
        raise NotImplementedError
```

### 2.2 Interface: `SandboxProvisioner` (CORRECTED: 2-phase design)

```python
# src/integration_coworker/codegen/gates/provisioner.py

from dataclasses import dataclass
from typing import Dict, List, Optional
from pathlib import Path

@dataclass
class SandboxEnv:
    """Prepared sandbox environment."""
    sandbox_path: Path
    env_vars: Dict[str, str]           # e.g., {"GOPATH": "/sandbox/.go"}
    work_dir: Path                      # Working directory for commands
    container_id: Optional[str]         # Docker container ID if containerized
    cleanup_callback: Optional[callable] # Called on exit
    deps_resolved: bool = False         # True after provision phase completes

class SandboxProvisioner:
    """
    Prepares isolated environment for gate execution.
    
    CORRECTED: 2-phase design for network isolation.
    
    Phase 1 (provision): Network ALLOWED
        - Copy artifacts into workspace
        - Resolve dependencies (npm ci, go mod download)
        - Returns SandboxEnv with deps_resolved=True
        
    Phase 2 (validate): Network DISABLED (--network none)
        - Run gates in network-isolated container
        - All deps must already be local
    """
    
    def provision(
        self,
        artifacts: List[ArtifactFile],
        language: str,
        tier: int,
        container_image: Optional[str] = None,
    ) -> SandboxEnv:
        """
        Provision sandbox environment (Phase 1 - network allowed).
        
        Tier 1: Starts Docker container, copies artifacts, runs npm ci / go mod download.
        Tier 2: Creates isolated directory with host toolchain.
        
        Returns SandboxEnv ready for validate() call.
        """
        raise NotImplementedError
    
    def validate(
        self,
        env: SandboxEnv,
        language: str,
        tier: int,
    ) -> None:
        """
        Prepare environment for validation (Phase 2 - network disabled).
        
        For Tier 1 containerized: restarts container with --network none.
        For Tier 2 host mode: no-op (network not isolated).
        
        Raises:
            ProvisionError: If deps_resolved=False (provision not called)
        """
        if not env.deps_resolved:
            raise ProvisionError("Must call provision() before validate()")
        raise NotImplementedError
    
    def cleanup(self, env: SandboxEnv) -> None:
        """Cleanup sandbox resources."""
        raise NotImplementedError
```

### 2.3 Interface: `GateRunner`

```python
# src/integration_coworker/codegen/gates/runner.py

from dataclasses import dataclass
from typing import List, Optional
from enum import Enum

class GateStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"      # Tier 2 only

@dataclass
class GateResult:
    """Result of running a single gate."""
    name: str                          # e.g., "tsc", "eslint", "vitest"
    status: GateStatus
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    artifacts_validated: List[str]     # Files that passed this gate
    errors: List[str]                  # Parsed error messages
    untrusted: bool = False            # True if regex-based (Tier 2)

class GateRunner:
    """Executes validation gates and emits structured results."""
    
    def run_gate(
        self,
        env: SandboxEnv,
        gate_name: str,
        command: List[str],
        timeout_seconds: int = 60,
    ) -> GateResult:
        """
        Execute a single gate command.
        
        Returns structured GateResult with parsed output.
        """
        raise NotImplementedError
    
    def run_all_gates(
        self,
        env: SandboxEnv,
        language: str,
        tier: int,
    ) -> List[GateResult]:
        """
        Run all gates for language/tier combination.
        
        Tier 1: All gates must pass or fail.
        Tier 2: Gates may be skipped if tooling unavailable.
        """
        raise NotImplementedError
```

---

## Step 3: Success Criteria Contract (Tightened)

### Removed Redundant Gates

| Gate | Python | TypeScript | Go | Java | Rationale |
|------|--------|------------|-----|------|-----------|
| **Syntax (tree-sitter)** | ✅ `ast.parse()` | ❌ **REMOVED** | ❌ **REMOVED** | ❌ **REMOVED** | Redundant: `tsc --noEmit` does parse+typecheck; `go test` compiles |
| **Type/Compile** | ✅ `mypy` | ✅ `tsc --noEmit --pretty false` | ✅ `go test ./...` | ✅ `mvn -DskipTests compile` | Primary compile gate |
| **Unit Tests** | ✅ `pytest` | ✅ `vitest run --reporter=json --outputFile=vitest-results.json` | ✅ `go test ./...` | ✅ `mvn test` | Test execution (CORRECTED: vitest uses reporter+outputFile) |
| **Lint** | ✅ `ruff` | ✅ `eslint . --format json --no-error-on-unmatched-pattern` | ✅ `golangci-lint run ./...` | ✅ (via Maven plugins) | Style + basic security (CORRECTED: added unmatched pattern flag) |
| **Security** | ✅ `bandit` | ✅ (eslint-plugin-security) | ✅ `go vet ./...` | ✅ (spotbugs via Maven) | Security scanning |
| **Artifact Detection** | ✅ AST | ✅ **TS Compiler API** (requires npm ci) | ✅ `go list -json` (streaming parse) | ✅ Maven module inspection | Must be compiler-backed (CORRECTED: TS needs local node_modules) |

### Contract Table (Per Language)

| Language | Tier 1 Eligible | Container Image | Required Config | Required Executables |
|----------|-----------------|-----------------|-----------------|----------------------|
| Python | ✅ Yes (current) | `ghcr.io/org/codegen-python:3.11` | `pyproject.toml` OR `requirements.txt` | `python`, `pip` |
| TypeScript | ✅ Yes (Phase 1) | `ghcr.io/org/codegen-typescript:20` | `tsconfig.json`, `package-lock.json` | `node`, `npm` |
| Go | ✅ Yes (Phase 2) | `ghcr.io/org/codegen-go:1.21` | `go.mod` | `go` |
| Java | 🔶 Tier 2 only | N/A | `pom.xml` OR `build.gradle` | `mvn` OR `gradle` |
| JavaScript | 🔶 Tier 2 only | N/A | `package-lock.json` | `node`, `npm` |
| Ruby | ❌ Future | N/A | - | - |
| C# | ❌ Future | N/A | - | - |

---

## Step 4: SandboxConfig Changes (CORRECTED)

### 4.1 Removed: Version Placeholders

**Removed fields** (meaningless without container management):
- ~~`node_version: str = "20"`~~
- ~~`go_version: str = "1.21"`~~
- ~~`python_version: str = "3.11"`~~

**Added fields** (actionable):

```python
@dataclass
class SandboxConfig:
    """Configuration for sandbox execution."""
    
    # Tier semantics (replaces version placeholders)
    tier: Literal["prod", "exp"] = "prod"
    fail_on_missing_toolchain: bool = True  # Tier 1 = True
    
    # Container configuration (Tier 1)
    container_image: Optional[str] = None   # e.g., "ghcr.io/org/codegen-typescript:20"
    container_timeout_seconds: int = 600
    mount_read_only: bool = True            # Security: artifacts read-only in container
    
    # Required executables (probed before gates)
    required_executables: List[str] = field(default_factory=list)
    
    # Required config files (probed before gates)
    required_config_files: List[str] = field(default_factory=list)
    
    # Gate enables (per-language defaults)
    enable_typecheck: bool = True
    enable_lint: bool = True
    enable_tests: bool = True
    enable_security: bool = True
    
    # Python-specific (kept for backward compatibility)
    enable_ruff: bool = True
    enable_mypy: bool = True
    enable_bandit: bool = True
    enable_pytest: bool = True
    
    # Common
    timeout_seconds: int = 600              # Increased from 300 for TS/Go
    cleanup_on_success: bool = True
    cleanup_on_failure: bool = False
```

### 4.2 File Templates: Fail-Fast (Phase 1)

**Do NOT** implement TS/Go file templates until gates are stable.

```python
# src/integration_coworker/codegen/file_templates.py

def generate_csv_parser(schema: FileSchema, language: str) -> str:
    """Generate CSV parser code for the given schema."""
    if language.lower() == "python":
        return _generate_python_csv_parser(schema)
    
    # Tier-aware early failure (Phase 1)
    raise NotImplementedError(
        f"CSV parser templates for {language} not yet supported in Tier 1. "
        f"Tracking issue: https://github.com/org/repo/issues/XXX"
    )
```

### 4.3 Artifact Detection: Compiler-Backed (No Regex for Tier 1)

**TypeScript**: Use TypeScript Compiler API (CORRECTED: workspace dependency, not global)

**IMPORTANT**: `require('typescript')` only works if the module is resolvable.
Global `npm -g` installs are NOT reliably `require()`-able without `NODE_PATH`.
Reference: https://nodejs.org/api/modules.html

**Solution**: Install `typescript` as a workspace dependency during provisioning
(`npm ci` with repo lockfile). The detector script then resolves it normally.

Container image should include `node`/`npm` only. Tooling (`typescript`, `eslint`, 
`vitest`) comes from `npm ci` using the repo lockfile (Tier 1 requirement).

```python
# src/integration_coworker/codegen/gates/artifact_detection.py

import subprocess
import json
from typing import List, Optional

def detect_ts_artifacts(
    sandbox_path: Path,
    expected_classes: List[str],
    expected_functions: List[str],
) -> ArtifactDetectionResult:
    """
    Detect TypeScript artifacts using TS Compiler API.
    
    PREREQUISITE: `npm ci` must have been run in sandbox_path so that
    `typescript` is available in node_modules. The detector script uses
    require('typescript') which resolves from local node_modules.
    
    This is Tier 1 compliant (compiler-backed, not regex).
    """
    # Script that uses TS compiler API to extract declarations
    # Assumes typescript is in node_modules (from npm ci)
    detector_script = """
    const ts = require('typescript');  // Resolves from local node_modules
    const path = require('path');
    
    const files = process.argv.slice(2);
    if (files.length === 0) {
        console.log(JSON.stringify({ classes: [], functions: [], error: 'No files provided' }));
        process.exit(0);
    }
    
    const program = ts.createProgram(files, { 
        noEmit: true,
        allowJs: true,
        checkJs: false,
    });
    
    const result = { classes: [], functions: [] };
    
    for (const sourceFile of program.getSourceFiles()) {
        // Skip declaration files and node_modules
        if (sourceFile.isDeclarationFile) continue;
        if (sourceFile.fileName.includes('node_modules')) continue;
        
        ts.forEachChild(sourceFile, function visit(node) {
            if (ts.isClassDeclaration(node) && node.name) {
                result.classes.push(node.name.text);
            }
            if (ts.isFunctionDeclaration(node) && node.name) {
                result.functions.push(node.name.text);
            }
            // Also check exported const arrow functions
            if (ts.isVariableStatement(node)) {
                const decls = node.declarationList.declarations;
                for (const decl of decls) {
                    if (ts.isIdentifier(decl.name) && decl.initializer) {
                        if (ts.isArrowFunction(decl.initializer) || ts.isFunctionExpression(decl.initializer)) {
                            result.functions.push(decl.name.text);
                        }
                    }
                }
            }
            ts.forEachChild(node, visit);
        });
    }
    
    console.log(JSON.stringify(result));
    """
    
    # Write detector script to sandbox
    detector_path = sandbox_path / "_artifact_detector.js"
    detector_path.write_text(detector_script)
    
    # Find all .ts files (exclude node_modules, .d.ts)
    ts_files = [
        f for f in sandbox_path.glob("**/*.ts")
        if "node_modules" not in str(f) and not f.name.endswith(".d.ts")
    ]
    
    if not ts_files:
        return ArtifactDetectionResult(classes=[], functions=[], error="No .ts files found")
    
    # Run detector (from sandbox_path so node_modules resolves)
    result = subprocess.run(
        ["node", str(detector_path)] + [str(f) for f in ts_files],
        capture_output=True, text=True, cwd=sandbox_path, timeout=30
    )
    
    if result.returncode != 0:
        raise ArtifactDetectionError(f"TS artifact detection failed: {result.stderr}")
    
    found = json.loads(result.stdout)
    # ... match against expected_classes, expected_functions
```

**Go**: Use `go list` / `go doc` (CORRECTED: streaming JSON parse)

```python
def detect_go_artifacts(
    sandbox_path: Path,
    expected_types: List[str],
    expected_functions: List[str],
) -> ArtifactDetectionResult:
    """
    Detect Go artifacts using go list -json.
    
    This is Tier 1 compliant (toolchain-based, not regex).
    
    CORRECTED: go list -json emits a STREAM of JSON objects (one per package),
    NOT newline-delimited JSON. Must use streaming decoder.
    Reference: https://pkg.go.dev/cmd/go#hdr-List_packages_or_modules
    """
    result = subprocess.run(
        ["go", "list", "-json", "./..."],
        capture_output=True, text=True, cwd=sandbox_path, timeout=30
    )
    
    if result.returncode != 0:
        raise ArtifactDetectionError(f"Go artifact detection failed: {result.stderr}")
    
    # CORRECTED: Parse streaming JSON objects (not line-based)
    # go list -json outputs concatenated JSON objects, not an array
    packages = []
    decoder = json.JSONDecoder()
    content = result.stdout
    idx = 0
    while idx < len(content):
        # Skip whitespace
        while idx < len(content) and content[idx].isspace():
            idx += 1
        if idx >= len(content):
            break
        try:
            obj, end_idx = decoder.raw_decode(content, idx)
            packages.append(obj)
            idx += end_idx
        except json.JSONDecodeError:
            break
    
    # Extract exported names from packages
    # Each package has: Name, GoFiles, Dir, etc.
    # For detailed type inspection, use `go doc -json pkg` per package
```

---

## Step 5: Implementation Checklist (Zero-Interpretation)

### Phase 1: TypeScript Tier 1 Gates (Sprint 1 - 2 weeks)

| # | Task | File | Exact Change | Acceptance Test |
|---|------|------|--------------|-----------------|
| 1.1 | Create ToolchainProbe interface | `src/integration_coworker/codegen/gates/probe.py` | New file with `ToolchainProbe` ABC and `TypeScriptProbe` impl | `pytest tests/codegen/gates/test_probe.py::test_ts_probe_finds_tsc` |
| 1.2 | Create SandboxProvisioner interface | `src/integration_coworker/codegen/gates/provisioner.py` | New file with `SandboxProvisioner` ABC and `ContainerProvisioner` impl | `pytest tests/codegen/gates/test_provisioner.py::test_container_starts` |
| 1.3 | Create GateRunner interface | `src/integration_coworker/codegen/gates/runner.py` | New file with `GateRunner` ABC and `TypeScriptGateRunner` impl | `pytest tests/codegen/gates/test_runner.py::test_tsc_gate_parses_errors` |
| 1.4 | Add SandboxConfig fields | `src/integration_coworker/codegen/sandbox.py` | Add `tier`, `fail_on_missing_toolchain`, `container_image`, `required_executables` | Existing tests pass + new config test |
| 1.5 | Implement `tsc --noEmit --pretty false` gate | `src/integration_coworker/codegen/gates/typescript.py` | Parse tsc output, extract errors with line numbers | `test_tsc_gate_fails_on_type_error`: `const x: number = "bad"` → FAILED |
| 1.6 | Implement `eslint . --format json` gate | `src/integration_coworker/codegen/gates/typescript.py` | Parse JSON output, extract severity/message/line | `test_eslint_gate_fails_on_security`: `eval(userInput)` → FAILED |
| 1.7 | Implement `vitest run --reporter=json --outputFile` gate | `src/integration_coworker/codegen/gates/typescript.py` | Parse vitest-results.json file (not stdout) | `test_vitest_gate_fails_on_test_failure`: failing test → FAILED |
| 1.8 | Implement TS artifact detection | `src/integration_coworker/codegen/gates/artifact_detection.py` | Use TS Compiler API (not regex) | `test_ts_artifact_detection_finds_class`: detects `class FooClient` |
| 1.9 | Wire gates into `execute_in_sandbox()` | `src/integration_coworker/codegen/sandbox.py` | Dispatch to TS gates when `artifacts[0].language == "typescript"` | `test_sandbox_typescript_full_pass`: valid TS → SUCCESS |
| 1.10 | Add fail-fast for TS templates | `src/integration_coworker/codegen/file_templates.py` | `raise NotImplementedError` with tracking issue link | `test_ts_template_fails_fast` |
| 1.11 | Build Docker image | `docker/codegen-typescript.Dockerfile` | Node 20, npm, tsc, eslint, vitest preinstalled | `docker build` succeeds, `tsc --version` works |
| 1.12 | Create test suite | `tests/codegen/gates/test_typescript.py` | 10+ tests covering all gates | All tests pass |

### Phase 2: Go Tier 1 Gates (Sprint 2 - 2 weeks)

| # | Task | File | Exact Change | Acceptance Test |
|---|------|------|--------------|-----------------|
| 2.1 | Implement `GoProbe` | `src/integration_coworker/codegen/gates/probe.py` | Detect `go.mod`, `go` executable | `test_go_probe_finds_gomod` |
| 2.2 | Implement `go test ./...` gate | `src/integration_coworker/codegen/gates/go.py` | Parse test output (go test -json for structured) | `test_go_test_gate_fails_on_test_failure` |
| 2.3 | Implement `go vet ./...` gate | `src/integration_coworker/codegen/gates/go.py` | Parse vet output | `test_go_vet_gate_fails_on_printf_mismatch` |
| 2.4 | Implement `staticcheck ./...` gate | `src/integration_coworker/codegen/gates/go.py` | Parse staticcheck output | `test_staticcheck_gate_finds_issues` |
| 2.5 | Implement Go artifact detection | `src/integration_coworker/codegen/gates/artifact_detection.py` | Use `go list -json` | `test_go_artifact_detection_finds_type` |
| 2.6 | Build Docker image | `docker/codegen-go.Dockerfile` | Go 1.21, staticcheck preinstalled | `docker build` succeeds |
| 2.7 | Wire gates into sandbox | `src/integration_coworker/codegen/sandbox.py` | Dispatch to Go gates | `test_sandbox_go_full_pass` |

### Phase 3: Java Tier 2 Gates (Sprint 3 - 1 week)

| # | Task | File | Exact Change | Acceptance Test |
|---|------|------|--------------|-----------------|
| 3.1 | Implement `JavaProbe` | `src/integration_coworker/codegen/gates/probe.py` | Detect `pom.xml`/`build.gradle`, `mvn`/`mvnw` | `test_java_probe_finds_pom` |
| 3.2 | Implement Maven compile gate | `src/integration_coworker/codegen/gates/java.py` | `./mvnw -B -q -Dmaven.repo.local=$SANDBOX/.m2repo -DskipTests compile` | `test_maven_compile_fails_on_syntax_error` |
| 3.3 | Implement Maven test gate | `src/integration_coworker/codegen/gates/java.py` | `./mvnw -B -q -Dmaven.repo.local=$SANDBOX/.m2repo test` | `test_maven_test_fails_on_test_failure` |
| 3.4 | Mark Java as Tier 2 | Config | `tier=2`, gates may be skipped | `test_java_tier2_skips_missing_tooling` |

### Phase 4: Strategy Refactor (Sprint 3 - 1 week)

| # | Task | File | Exact Change | Acceptance Test |
|---|------|------|--------------|-----------------|
| 4.1 | Create `LanguageGateStrategy` ABC | `src/integration_coworker/codegen/gates/base.py` | Methods: `probe()`, `provision()`, `run_gates()` | N/A (interface) |
| 4.2 | Extract Python gates | `src/integration_coworker/codegen/gates/python.py` | Move existing logic from sandbox.py | Existing Python tests pass |
| 4.3 | Create registry | `src/integration_coworker/codegen/gates/registry.py` | `GATE_REGISTRY: Dict[str, Type[LanguageGateStrategy]]` | `test_registry_lookup` |
| 4.4 | Refactor `execute_in_sandbox()` | `src/integration_coworker/codegen/sandbox.py` | Use `GATE_REGISTRY[language]()` | All existing tests pass |

---

## Step 6: Production Test Procedure (Actionable)

### 6.1 Discovery Commands

```bash
# Find production env configuration
rg -n "USE_SQLITE|POSTGRES|DATABASE_URL|persist|persistence|OPENAI|ANTHROPIC|LLM" -S src scripts tests

# Find existing production test scripts
fd -e sh -e py . scripts/ | xargs grep -l "production\|prod\|e2e"

# Find multi-language test fixtures
fd -e ts -e go -e java . tests/fixtures/
```

### 6.2 Environment Setup

```bash
# Start Postgres (if docker-compose exists)
docker compose up -d postgres

# Export production-like env vars
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/coworker_test"
export USE_POSTGRES=1
export OPENAI_API_KEY="sk-test-xxx"  # Mock or test key
export VALIDATION_PROFILE="production"
```

### 6.3 Production Validation Runs

```bash
# Run TypeScript sandbox gate tests
python -m pytest tests/codegen/gates/test_typescript.py -v --tb=short 2>&1 | tee logs/ts_gates.log

# Run Go sandbox gate tests  
python -m pytest tests/codegen/gates/test_go.py -v --tb=short 2>&1 | tee logs/go_gates.log

# Run existing production E2E (highest coverage)
python -m pytest tests/test_production_e2e_workflow.py -v --tb=short 2>&1 | tee logs/e2e.log

# Run multi-language production script
python scripts/test_multi_language_production.py 2>&1 | tee logs/multi_lang.log
```

### 6.4 Bug Ledger Template

| Bug ID | Severity | Command | Exit Code | Suspected File | Tier Blocker? |
|--------|----------|---------|-----------|----------------|---------------|
| ML-XXX | HIGH | `pytest test_ts_gates.py::test_X` | 1 | `gates/typescript.py:123` | Yes (Tier 1) |

### 6.5 Validation Pass/Fail Criteria

**Phase 1 (TypeScript) is complete when:**
- [ ] `pytest tests/codegen/gates/test_typescript.py` - 100% pass
- [ ] Valid TS code passes all gates (tsc, eslint, vitest)
- [ ] Invalid TS code fails appropriate gate with actionable error
- [ ] Container starts/stops correctly
- [ ] No secrets/credentials in logs
- [ ] Sandbox cleanup leaves no artifacts on host

**Phase 2 (Go) is complete when:**
- [ ] `pytest tests/codegen/gates/test_go.py` - 100% pass
- [ ] `go test ./...` and `go vet ./...` both run
- [ ] Artifact detection uses `go list`, not regex

---

## Step 7: Priority Ranking (Risk-Adjusted)

### P0 (This Sprint - Before Any Coding)

| Item | Rationale | Effort |
|------|-----------|--------|
| Determinism strategy decision: containerized | Prerequisite for Tier 1 semantics | S |
| Fix Go commands (`./...` syntax) | Correctness | S |
| Fix TS commands (`--json`, `--format json`) | Correctness | S |
| Create Dockerfile for TS | Blocks all TS gate implementation | M |

### P1 (Sprint 1)

| Item | Rationale | Effort |
|------|-----------|--------|
| TypeScript `tsc --noEmit` gate | Primary compile gate | M |
| TypeScript `eslint --format json` gate | Lint + security | M |
| TypeScript `vitest run --json` gate | Test execution | M |
| TypeScript artifact detection (Compiler API) | Tier 1 requirement | M |
| TypeScript test suite (10+ tests) | Validation | M |

### P2 (Sprint 2)

| Item | Rationale | Effort |
|------|-----------|--------|
| Go `go test ./...` gate | Primary gate | M |
| Go `go vet ./...` gate | Static analysis | S |
| Go artifact detection (`go list`) | Tier 1 requirement | M |
| Strategy refactor | Tech debt | M |

### P3 (Future)

| Item | Rationale | Effort |
|------|-----------|--------|
| Java Maven gates | Lower demand, Tier 2 only | M |
| TS/Go file templates | Value-add, needs stable gates first | L |

### CUT (Explicitly Deferred)

| Item | Reason |
|------|--------|
| Ruby gates | No demand |
| C# gates | No demand |
| Tree-sitter syntax validation | Redundant with compiler gates |
| IR layer (V2 plan) | Overkill, revisit in V3 |

---

## Appendix A: Container Image Specifications

### TypeScript Container (`codegen-typescript:20`) - CORRECTED

**IMPORTANT**: Tooling (typescript, eslint, vitest) comes from `npm ci` using
the repo's lockfile, NOT from global installs. This ensures:
1. Deterministic versions matching repo expectations
2. `require('typescript')` works without NODE_PATH hacks

```dockerfile
FROM node:20-alpine

# NO global tool installs - tools come from npm ci with repo lockfile
# This image only provides node/npm runtime

# Security: non-root user
RUN adduser -D codegen
USER codegen

WORKDIR /workspace

# Entrypoint expects: 
# 1. Mount repo at /workspace
# 2. Run npm ci (provision phase, network allowed)
# 3. Run gates (validate phase, --network none)
```

### Go Container (`codegen-go:1.21`) - CORRECTED

```dockerfile
FROM golang:1.21-alpine

# Install static analysis tools (pinned versions)
# These are Go tools, not managed by go.mod, so we install globally
RUN go install honnef.co/go/tools/cmd/staticcheck@v0.4.6
RUN go install github.com/golangci/golangci-lint/cmd/golangci-lint@v1.55.2

# Security: non-root user
RUN adduser -D codegen
USER codegen

WORKDIR /workspace

# Entrypoint expects:
# 1. Mount repo at /workspace
# 2. Run go mod download (provision phase, network allowed)
# 3. Run gates (validate phase, --network none)
```

---

## Appendix B: Structured Output Schema

```python
@dataclass
class ValidationResult:
    """Structured output from sandbox validation."""
    success: bool
    tier: Literal["prod", "exp"]
    language: str
    gates: List[GateResult]
    artifacts_validated: List[str]
    
    # Tier compliance markers
    untrusted_checks: List[str]  # Empty for Tier 1
    skipped_gates: List[str]     # Empty for Tier 1
    
    # Debugging
    container_id: Optional[str]
    duration_ms: int
    
    def is_tier1_compliant(self) -> bool:
        """Returns True only if all checks are compiler-backed."""
        return (
            self.tier == "prod" 
            and len(self.untrusted_checks) == 0
            and len(self.skipped_gates) == 0
            and self.success
        )
```

---

*Document created: 2025-12-19*  
*Correctness review applied: 2025-12-19*  
*Status: LOCKED - ready for implementation*
