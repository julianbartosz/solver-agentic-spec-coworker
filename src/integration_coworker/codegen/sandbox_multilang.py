"""
Multi-Language Sandbox Execution Module

Extends sandbox.py to support TypeScript and Go validation using the
strategy pattern from codegen/gates/*.

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.4:
- 2-Phase Design: provision (network) → validate (network none)
- Tier 1 (PROD): Docker execution with --network none during validate
- Tier 2 (EXP): Host execution fallback (non-deterministic)

DOCKER RUNNER (as of 2025-12-19):
- DockerRunner class in codegen/gates/docker_runner.py
- 2-phase execution: provision with network → validate with --network none
- Images: node:20-slim (TypeScript), golang:1.22-bookworm (Go)
- Resource limits: --memory 2g, --cpus 2, --pids-limit 256

Tier 1 requires:
- Docker execution with --network none during validate phase
- Lockfile present (package-lock.json for TS, go.sum for Go)
- All tooling from workspace node_modules/.bin (not global)

TIER SEMANTICS (2025-12-19):
- Tier is EXPLICIT - never inferred from Docker availability
- Default: Tier.EXP (safe, host execution)
- Tier.PROD + Docker unavailable = HARD FAIL (no silent downgrade)

Usage:
    from integration_coworker.codegen.sandbox_multilang import (
        execute_multilang_sandbox,
        MultiLangSandboxConfig,
        ArtifactLanguage,
        Tier,
        recommended_tier,
    )
    
    # Explicit Tier.PROD (recommended for CI/production)
    result = await execute_multilang_sandbox(
        artifacts=[ArtifactFile("src/client.ts", "export class Client {}")],
        language=ArtifactLanguage.TYPESCRIPT,
        config=MultiLangSandboxConfig(tier=Tier.PROD, use_docker=True),
    )
    
    # Check recommended tier for current environment
    tier = recommended_tier()  # Tier.PROD if Docker available
"""
import asyncio
import logging
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Any

from integration_coworker.codegen.gates import (
    get_strategy,
    LanguageStrategy,
    ValidationResult,
    GateResult as GatesGateResult,
    GateStatus,
    Tier,
    ArtifactFile as GatesArtifactFile,
    ProvisionError,
    ToolchainMissingError,
)
from integration_coworker.codegen.sandbox import (
    ArtifactFile,
    GateResult,
    SandboxResult,
    SandboxConfig,
    execute_in_sandbox,
)
# Docker runner for Tier 1 (production) execution
from integration_coworker.codegen.gates.docker_runner import (
    DockerRunner,
    DockerConfig,
    DockerNotAvailableError,
    is_docker_available,
    get_gates_for_language,
)

logger = logging.getLogger(__name__)


class ArtifactLanguage(str, Enum):
    """Supported artifact languages."""
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    GO = "go"
    JAVA = "java"
    
    @classmethod
    def from_extension(cls, path: str) -> "ArtifactLanguage":
        """Detect language from file extension."""
        ext = Path(path).suffix.lower()
        mapping = {
            ".py": cls.PYTHON,
            ".ts": cls.TYPESCRIPT,
            ".tsx": cls.TYPESCRIPT,
            ".js": cls.JAVASCRIPT,
            ".jsx": cls.JAVASCRIPT,
            ".go": cls.GO,
            ".java": cls.JAVA,
        }
        return mapping.get(ext, cls.PYTHON)


def _check_docker_available_cached() -> bool:
    """Check Docker availability with caching to avoid repeated subprocess calls."""
    global _DOCKER_AVAILABLE_CACHE
    if _DOCKER_AVAILABLE_CACHE is None:
        _DOCKER_AVAILABLE_CACHE = is_docker_available()
        if _DOCKER_AVAILABLE_CACHE:
            logger.info("Docker detected - Tier.PROD validation available")
        else:
            logger.info("Docker not detected - Tier.EXP validation only")
    return _DOCKER_AVAILABLE_CACHE


# Global cache for Docker availability
_DOCKER_AVAILABLE_CACHE: Optional[bool] = None


def recommended_tier(docker_available: Optional[bool] = None) -> Tier:
    """
    Helper to determine recommended tier based on environment.
    
    This is a CONVENIENCE FUNCTION only - callers should explicitly choose
    their tier. Never called implicitly by MultiLangSandboxConfig.
    
    Args:
        docker_available: Override Docker detection (for testing)
        
    Returns:
        Tier.PROD if Docker is available, Tier.EXP otherwise
    """
    if docker_available is None:
        docker_available = _check_docker_available_cached()
    return Tier.PROD if docker_available else Tier.EXP


@dataclass
class MultiLangSandboxConfig:
    """Configuration for multi-language sandbox execution.
    
    Tier semantics (EXPLICIT - never inferred from environment):
    - Tier.EXP (default): Host execution, non-deterministic, for development
    - Tier.PROD: Docker execution with --network none, deterministic, for CI/production
    
    INVARIANTS:
    - Tier is NEVER auto-selected from Docker availability
    - Tier.PROD + Docker unavailable = HARD FAIL (no silent downgrade)
    - Tier.PROD validation runs with --network none (network-isolated)
    
    To check what tier is recommended for the current environment, use:
        from sandbox_multilang import recommended_tier
        tier = recommended_tier()  # Returns Tier.PROD if Docker available
    """
    tier: Tier = Tier.EXP  # EXPLICIT default - never inferred from environment
    timeout_seconds: int = 300
    cleanup_on_success: bool = True
    cleanup_on_failure: bool = False
    
    # Docker settings for Tier.PROD
    use_docker: bool = False  # Must be explicitly enabled, matches tier
    container_network_none: bool = True  # --network none during validate phase
    
    # Language-specific config files to include
    typescript_config: Optional[Dict[str, Any]] = None
    go_config: Optional[Dict[str, Any]] = None
    
    # Fallback to Python sandbox for unsupported languages
    fallback_to_python_sandbox: bool = True
    
    def __post_init__(self):
        """Validate configuration - NO auto-selection from environment."""
        # Validate tier + use_docker consistency
        if self.tier == Tier.PROD and not self.use_docker:
            # Tier.PROD requires Docker - caller must explicitly enable
            raise ValueError(
                "Tier.PROD requires use_docker=True. "
                "Set MultiLangSandboxConfig(tier=Tier.PROD, use_docker=True) explicitly."
            )
        
        # Hard invariant: Tier.PROD + Docker unavailable = FAIL
        if self.tier == Tier.PROD and self.use_docker:
            docker_available = _check_docker_available_cached()
            if not docker_available:
                raise DockerNotAvailableError(
                    "Tier.PROD requires Docker but Docker is not available. "
                    "Install Docker or use tier=Tier.EXP for host execution."
                )


@dataclass 
class MultiLangSandboxResult:
    """Result of multi-language sandbox execution."""
    success: bool
    language: ArtifactLanguage
    tier: Tier
    gate_results: List[GateResult]  # Converted to sandbox-compatible format
    validation_result: Optional[ValidationResult]  # Raw result from strategy
    sandbox_dir: Optional[str]
    summary: str
    is_tier1_compliant: bool
    untrusted_checks: List[str] = field(default_factory=list)
    skipped_gates: List[str] = field(default_factory=list)
    
    @property
    def failed_gates(self) -> List[GateResult]:
        return [g for g in self.gate_results if not g.passed]
    
    @property
    def passed_gates(self) -> List[GateResult]:
        return [g for g in self.gate_results if g.passed]


def _convert_gates_artifact(artifact: ArtifactFile) -> GatesArtifactFile:
    """Convert sandbox ArtifactFile to gates ArtifactFile."""
    return GatesArtifactFile(
        path=artifact.path,
        content=artifact.content,
    )


def _convert_gate_result(gate: GatesGateResult) -> GateResult:
    """Convert gates GateResult to sandbox-compatible GateResult."""
    return GateResult(
        name=gate.name,
        passed=gate.status == GateStatus.PASSED,
        output=gate.stdout or gate.stderr or "",
        return_code=gate.exit_code,
        duration_ms=gate.duration_ms,
    )


async def _execute_docker_sandbox(
    artifacts: List[ArtifactFile],
    language: ArtifactLanguage,
    config: MultiLangSandboxConfig,
    workspace: Path,
    sandbox_dir: str,
) -> MultiLangSandboxResult:
    """
    Execute artifacts using Docker 2-phase runner (Tier.PROD).
    
    Phase 1 (Provision): Run with network to install deps
    Phase 2 (Validate): Run with --network none for isolation
    
    This is the only path that can produce Tier 1 compliant results.
    """
    from integration_coworker.codegen.gates.docker_runner import (
        DockerRunner,
        DockerConfig,
        DockerNotAvailableError,
        ProvisionError as DockerProvisionError,
        ValidationError as DockerValidationError,
    )
    
    try:
        docker_config = DockerConfig(
            provision_timeout=config.timeout_seconds,
            validate_timeout=config.timeout_seconds,
            cleanup_on_success=config.cleanup_on_success,
            cleanup_on_failure=config.cleanup_on_failure,
        )
        runner = DockerRunner(config=docker_config)
        
        # Convert artifacts to Docker format
        docker_artifacts = [{"path": a.path, "content": a.content} for a in artifacts]
        
        with runner.workspace(docker_artifacts) as ws:
            # Phase 1: Provision (network allowed)
            logger.info(f"Docker provision: {language.value}")
            try:
                runner.provision(ws, language=language.value)
            except DockerProvisionError as e:
                return MultiLangSandboxResult(
                    success=False,
                    language=language,
                    tier=Tier.EXP,  # Failed provision = not Tier 1
                    gate_results=[GateResult(
                        name="docker_provision",
                        passed=False,
                        output=str(e),
                        return_code=1,
                        duration_ms=0,
                    )],
                    validation_result=None,
                    sandbox_dir=sandbox_dir,
                    summary=f"FAILED: Docker provision failed - {e}",
                    is_tier1_compliant=False,
                )
            
            # Phase 2: Validate (network disabled)
            logger.info(f"Docker validate: {language.value} (--network none)")
            gates = get_gates_for_language(language.value)
            docker_results = runner.validate(ws, gates=gates)
            
            # Convert Docker results to GateResult
            gate_results = [
                GateResult(
                    name=r.name,
                    passed=r.passed,
                    output=r.stdout if r.stdout else r.stderr,
                    return_code=r.exit_code,
                    duration_ms=r.duration_ms,
                )
                for r in docker_results
            ]
            
            # All passed?
            success = all(r.passed for r in docker_results)
            passed_count = sum(1 for r in docker_results if r.passed)
            
            return MultiLangSandboxResult(
                success=success,
                language=language,
                tier=Tier.PROD,  # Docker execution = Tier 1
                gate_results=gate_results,
                validation_result=None,
                sandbox_dir=str(ws.host_path),
                summary=f"{'PASSED' if success else 'FAILED'}: {passed_count}/{len(gate_results)} gates (Docker)",
                is_tier1_compliant=success,  # Only compliant if all passed
            )
            
    except DockerNotAvailableError as e:
        logger.error(f"Docker not available: {e}")
        return MultiLangSandboxResult(
            success=False,
            language=language,
            tier=Tier.EXP,
            gate_results=[GateResult(
                name="docker_check",
                passed=False,
                output=str(e),
                return_code=1,
                duration_ms=0,
            )],
            validation_result=None,
            sandbox_dir=sandbox_dir,
            summary=f"FAILED: Docker not available - {e}",
            is_tier1_compliant=False,
        )
    except Exception as e:
        logger.exception(f"Docker execution failed: {e}")
        return MultiLangSandboxResult(
            success=False,
            language=language,
            tier=Tier.EXP,
            gate_results=[GateResult(
                name="docker_error",
                passed=False,
                output=str(e),
                return_code=1,
                duration_ms=0,
            )],
            validation_result=None,
            sandbox_dir=sandbox_dir,
            summary=f"FAILED: Docker execution error - {e}",
            is_tier1_compliant=False,
        )


async def execute_multilang_sandbox(
    artifacts: List[ArtifactFile],
    language: ArtifactLanguage,
    config: Optional[MultiLangSandboxConfig] = None,
    workspace_config_files: Optional[Dict[str, str]] = None,
) -> MultiLangSandboxResult:
    """
    Execute artifacts in a language-specific sandbox.
    
    Dispatches to the appropriate LanguageStrategy based on language.
    Falls back to Python sandbox for Python artifacts or unsupported languages.
    
    Args:
        artifacts: Code files to validate
        language: Target language
        config: Multi-language sandbox configuration
        workspace_config_files: Additional config files (e.g., tsconfig.json contents)
        
    Returns:
        MultiLangSandboxResult with gate outcomes
    """
    config = config or MultiLangSandboxConfig()
    workspace_config_files = workspace_config_files or {}
    
    # Python uses existing sandbox
    if language == ArtifactLanguage.PYTHON:
        return await _execute_python_sandbox(artifacts, config)
    
    # Get strategy for language
    strategy_class = get_strategy(language.value)
    
    if not strategy_class:
        if config.fallback_to_python_sandbox:
            logger.warning(
                f"No strategy for {language.value}, falling back to Python sandbox"
            )
            return await _execute_python_sandbox(artifacts, config)
        else:
            return MultiLangSandboxResult(
                success=False,
                language=language,
                tier=Tier.EXP,
                gate_results=[GateResult(
                    name="strategy_lookup",
                    passed=False,
                    output=f"No strategy registered for language: {language.value}",
                    return_code=1,
                    duration_ms=0,
                )],
                validation_result=None,
                sandbox_dir=None,
                summary=f"FAILED: No strategy for {language.value}",
                is_tier1_compliant=False,
            )
    
    # Create workspace
    sandbox_dir = tempfile.mkdtemp(prefix=f"codegen_{language.value}_")
    workspace = Path(sandbox_dir)
    
    try:
        # Write config files first (before artifacts)
        for filename, content in workspace_config_files.items():
            config_path = workspace / filename
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(content)
            logger.debug(f"Wrote config file: {filename}")
        
        # Instantiate strategy
        strategy: LanguageStrategy = strategy_class()
        
        # Convert artifacts
        gates_artifacts = [_convert_gates_artifact(a) for a in artifacts]
        
        # Probe toolchain
        probe = strategy.probe_toolchain(workspace)
        
        # Execution mode is determined by explicit config (validated in __post_init__):
        # - config.tier == Tier.PROD requires config.use_docker=True (enforced by __post_init__)
        # - __post_init__ already verifies Docker availability for Tier.PROD
        # - No environment inference or silent downgrades here
        tier = config.tier
        use_docker = config.use_docker
        
        # Additional downgrade if toolchain requirements not met
        # (e.g., missing lockfile for Tier.PROD TypeScript)
        if tier == Tier.PROD and probe["tier_eligible"] != Tier.PROD:
            # This is NOT a silent downgrade - toolchain requirements are explicit
            failure_reason = probe.get("failure_reason", "toolchain requirements not met")
            logger.warning(f"Downgrading to Tier.EXP: {failure_reason}")
            tier = Tier.EXP
            use_docker = False
        
        # Execute based on mode
        if use_docker and tier == Tier.PROD:
            # Docker 2-phase execution
            return await _execute_docker_sandbox(
                artifacts=artifacts,
                language=language,
                config=config,
                workspace=workspace,
                sandbox_dir=sandbox_dir,
            )
        
        # Host execution (Tier.EXP)
        # Phase 1: Provision (network allowed)
        logger.info(f"Provisioning {language.value} sandbox (tier={tier.value})")
        try:
            env = strategy.provision(gates_artifacts, workspace, tier)
        except (ProvisionError, ToolchainMissingError) as e:
            return MultiLangSandboxResult(
                success=False,
                language=language,
                tier=tier,
                gate_results=[GateResult(
                    name="provision",
                    passed=False,
                    output=str(e),
                    return_code=1,
                    duration_ms=0,
                )],
                validation_result=None,
                sandbox_dir=sandbox_dir,
                summary=f"FAILED: Provisioning failed - {e}",
                is_tier1_compliant=False,
            )
        
        # Phase 2: Validate (host execution - no network isolation)
        logger.info(f"Validating {language.value} artifacts (tier={tier.value}, host execution)")
        validation_result = strategy.validate(env, tier)
        
        # Convert results
        gate_results = [_convert_gate_result(g) for g in validation_result.gates]
        
        # Build summary
        passed_count = sum(1 for g in gate_results if g.passed)
        total_count = len(gate_results)
        status = "PASSED" if validation_result.success else "FAILED"
        summary = f"{status}: {passed_count}/{total_count} gates passed"
        
        if validation_result.untrusted_checks:
            summary += f" (UNTRUSTED: {', '.join(validation_result.untrusted_checks)})"
        
        result = MultiLangSandboxResult(
            success=validation_result.success,
            language=language,
            tier=tier,
            gate_results=gate_results,
            validation_result=validation_result,
            sandbox_dir=sandbox_dir,
            summary=summary,
            is_tier1_compliant=validation_result.is_tier1_compliant(),
            untrusted_checks=validation_result.untrusted_checks,
            skipped_gates=validation_result.skipped_gates,
        )
        
        # Cleanup
        if (validation_result.success and config.cleanup_on_success) or \
           (not validation_result.success and config.cleanup_on_failure):
            strategy.cleanup(env)
            result.sandbox_dir = None
        
        return result
        
    except Exception as e:
        logger.exception(f"Multi-language sandbox failed: {e}")
        return MultiLangSandboxResult(
            success=False,
            language=language,
            tier=Tier.EXP,
            gate_results=[GateResult(
                name="sandbox_error",
                passed=False,
                output=str(e),
                return_code=-1,
                duration_ms=0,
            )],
            validation_result=None,
            sandbox_dir=sandbox_dir,
            summary=f"FAILED: {e}",
            is_tier1_compliant=False,
        )


async def _execute_python_sandbox(
    artifacts: List[ArtifactFile],
    config: MultiLangSandboxConfig,
) -> MultiLangSandboxResult:
    """Execute using existing Python sandbox."""
    python_config = SandboxConfig(
        timeout_seconds=config.timeout_seconds,
        cleanup_on_success=config.cleanup_on_success,
        cleanup_on_failure=config.cleanup_on_failure,
    )
    
    result = await execute_in_sandbox(
        artifacts=artifacts,
        config=python_config,
    )
    
    # Convert to MultiLangSandboxResult
    return MultiLangSandboxResult(
        success=result.success,
        language=ArtifactLanguage.PYTHON,
        tier=Tier.PROD,  # Python sandbox is always Tier 1
        gate_results=result.gate_results,
        validation_result=None,  # Python sandbox doesn't use ValidationResult
        sandbox_dir=result.sandbox_dir,
        summary=result.summary,
        is_tier1_compliant=result.success,  # Python sandbox is compiler-backed
    )


def detect_language(artifacts: List[ArtifactFile]) -> ArtifactLanguage:
    """
    Detect the primary language from artifacts.
    
    Uses file extensions to determine language. If mixed, uses majority.
    """
    from collections import Counter
    
    languages = [ArtifactLanguage.from_extension(a.path) for a in artifacts]
    if not languages:
        return ArtifactLanguage.PYTHON
    
    counter = Counter(languages)
    return counter.most_common(1)[0][0]


# Convenience function for backward compatibility
async def validate_artifacts(
    artifacts: List[ArtifactFile],
    language: Optional[ArtifactLanguage] = None,
    tier: Tier = Tier.EXP,
) -> MultiLangSandboxResult:
    """
    Validate artifacts with automatic language detection.
    
    Convenience wrapper that auto-detects language if not specified.
    For Tier.PROD, Docker must be available.
    """
    if language is None:
        language = detect_language(artifacts)
    
    # Build config with explicit tier + use_docker consistency
    use_docker = (tier == Tier.PROD)
    config = MultiLangSandboxConfig(tier=tier, use_docker=use_docker)
    return await execute_multilang_sandbox(artifacts, language, config)
