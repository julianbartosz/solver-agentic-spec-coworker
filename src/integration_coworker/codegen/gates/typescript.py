"""
TypeScript language validation strategy.

Implements Tier 1 production gates for TypeScript:
- tsc --noEmit --pretty false (typecheck)
- eslint . --format json --no-error-on-unmatched-pattern (lint)
- vitest run --reporter=json --outputFile=vitest-results.json (tests)

2-Phase Design:
1. Provision: npm ci (requires package-lock.json), network allowed
2. Validate: Run gates with network disabled (--network none in Docker)

Per MULTI_LANG_CODEGEN_PRODUCTION_PLAN.md v1.2
"""

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from integration_coworker.codegen.gates.base import (
    ArtifactFile,
    GateError,
    GateResult,
    GateStatus,
    LanguageStrategy,
    ProvisionError,
    SandboxEnv,
    Tier,
    ToolchainMissingError,
    ValidationResult,
)
import logging

logger = logging.getLogger(__name__)


@dataclass
class TypeScriptGateConfig:
    """Configuration for TypeScript gates."""
    enable_tsc: bool = True
    enable_eslint: bool = True
    enable_vitest: bool = True
    timeout_seconds: int = 300
    use_container: bool = False  # Set True for Tier 1 network isolation
    container_image: str = "node:20-alpine"
    
    
class TypeScriptStrategy(LanguageStrategy):
    """
    TypeScript validation strategy.
    
    Tier 1 (PROD): Requires tsconfig.json + package-lock.json, runs npm ci,
                   executes tsc/eslint/vitest with network disabled.
    Tier 2 (EXP): May skip gates if tooling unavailable.
    
    IMPORTANT: Tools (typescript, eslint, vitest) come from npm ci using
    the repo's lockfile, NOT global installs. This ensures determinism.
    """
    
    language = "typescript"
    default_tier = Tier.PROD
    required_executables = ["node", "npm"]
    required_config_files = ["tsconfig.json", "package-lock.json"]
    container_image = "node:20-alpine"
    
    def __init__(self, config: Optional[TypeScriptGateConfig] = None):
        self.config = config or TypeScriptGateConfig()
    
    def probe_toolchain(self, workspace: Path) -> Dict[str, Any]:
        """Probe for TypeScript toolchain availability."""
        result = {
            "executables_found": [],
            "executables_missing": [],
            "config_files_found": [],
            "config_files_missing": [],
            "lockfile_found": None,
            "tier_eligible": Tier.PROD,
            "failure_reason": None,
        }
        
        # Check for node
        node_path = shutil.which("node")
        if node_path:
            result["executables_found"].append("node")
        else:
            result["executables_missing"].append("node")
            result["tier_eligible"] = Tier.EXP
            result["failure_reason"] = "Node.js not found"
        
        # Check for npm
        npm_path = shutil.which("npm")
        if npm_path:
            result["executables_found"].append("npm")
        else:
            result["executables_missing"].append("npm")
            result["tier_eligible"] = Tier.EXP
            result["failure_reason"] = "npm not found"
        
        # Check for tsconfig.json
        if (workspace / "tsconfig.json").exists():
            result["config_files_found"].append("tsconfig.json")
        else:
            result["config_files_missing"].append("tsconfig.json")
            result["tier_eligible"] = Tier.EXP
            result["failure_reason"] = "tsconfig.json not found"
        
        # Check for lockfile (required for Tier 1 determinism)
        for lockfile in ["package-lock.json", "pnpm-lock.yaml", "yarn.lock"]:
            if (workspace / lockfile).exists():
                result["lockfile_found"] = lockfile
                result["config_files_found"].append(lockfile)
                break
        
        if not result["lockfile_found"]:
            result["config_files_missing"].append("package-lock.json")
            result["tier_eligible"] = Tier.EXP
            result["failure_reason"] = "No lockfile found (required for Tier 1 determinism)"
        
        # Check for eslint config
        eslint_configs = [
            "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
            ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml"
        ]
        for config in eslint_configs:
            if (workspace / config).exists():
                result["config_files_found"].append(config)
                break
        
        # Check for vitest/jest config
        test_configs = [
            "vitest.config.ts", "vitest.config.js", "vitest.config.mts",
            "jest.config.js", "jest.config.ts", "jest.config.json"
        ]
        for config in test_configs:
            if (workspace / config).exists():
                result["config_files_found"].append(config)
                break
        
        return result
    
    def provision(
        self,
        artifacts: List[ArtifactFile],
        workspace: Path,
        tier: Tier,
    ) -> SandboxEnv:
        """
        Provision TypeScript sandbox.
        
        Phase 1 (network allowed):
        1. Write artifacts to workspace
        2. Ensure package.json exists
        3. Run npm ci to install dependencies
        """
        start = time.time()
        
        # Create workspace
        workspace.mkdir(parents=True, exist_ok=True)
        
        # Write artifacts
        for artifact in artifacts:
            artifact_path = workspace / artifact.path
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            artifact_path.write_text(artifact.content)
            logger.debug(f"Wrote artifact: {artifact.path}")
        
        # Check toolchain
        probe = self.probe_toolchain(workspace)
        if tier == Tier.PROD and probe["tier_eligible"] != Tier.PROD:
            raise ToolchainMissingError(
                f"Tier 1 TypeScript validation requires: {probe['failure_reason']}"
            )
        
        # Check for package.json
        package_json = workspace / "package.json"
        if not package_json.exists():
            if tier == Tier.PROD:
                raise ProvisionError("package.json required for Tier 1 TypeScript validation")
            else:
                # Create minimal package.json for Tier 2
                package_json.write_text(json.dumps({
                    "name": "validation-sandbox",
                    "private": True,
                    "devDependencies": {
                        "typescript": "^5.0.0",
                    }
                }, indent=2))
                logger.warning("Created minimal package.json (Tier 2)")
        
        # Run npm ci (Tier 1) or npm install (Tier 2)
        npm_cmd = ["npm", "ci"] if probe["lockfile_found"] else ["npm", "install"]
        
        try:
            result = subprocess.run(
                npm_cmd,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=300,  # npm can be slow
            )
            if result.returncode != 0:
                raise ProvisionError(f"npm failed: {result.stderr}")
            logger.debug(f"npm completed: {' '.join(npm_cmd)}")
        except subprocess.TimeoutExpired:
            raise ProvisionError("npm timed out")
        
        duration_ms = int((time.time() - start) * 1000)
        logger.info(f"TypeScript provisioning completed in {duration_ms}ms")
        
        return SandboxEnv(
            sandbox_path=workspace,
            work_dir=workspace,
            env_vars={},
            deps_resolved=True,
        )
    
    def validate(
        self,
        env: SandboxEnv,
        tier: Tier,
    ) -> ValidationResult:
        """
        Run TypeScript validation gates.
        
        Phase 2 (network disabled for Tier 1):
        - tsc --noEmit --pretty false
        - eslint . --format json --no-error-on-unmatched-pattern
        - vitest run --reporter=json --outputFile=vitest-results.json
        """
        if not env.deps_resolved:
            raise ProvisionError("Must call provision() before validate()")
        
        start = time.time()
        gates: List[GateResult] = []
        all_passed = True
        
        # Run tsc (typecheck)
        if self.config.enable_tsc:
            gate = self._run_tsc(env, tier)
            gates.append(gate)
            if not gate.passed:
                all_passed = False
        
        # Run eslint
        if self.config.enable_eslint:
            gate = self._run_eslint(env, tier)
            gates.append(gate)
            if not gate.passed and gate.status != GateStatus.SKIPPED:
                all_passed = False
        
        # Run vitest
        if self.config.enable_vitest:
            gate = self._run_vitest(env, tier)
            gates.append(gate)
            if not gate.passed and gate.status != GateStatus.SKIPPED:
                all_passed = False
        
        duration_ms = int((time.time() - start) * 1000)
        
        # Collect validated artifacts
        validated = []
        for f in env.sandbox_path.rglob("*.ts"):
            if "node_modules" not in str(f):
                validated.append(str(f.relative_to(env.sandbox_path)))
        for f in env.sandbox_path.rglob("*.tsx"):
            if "node_modules" not in str(f):
                validated.append(str(f.relative_to(env.sandbox_path)))
        
        # Track skipped gates for Tier 2
        skipped = [g.name for g in gates if g.status == GateStatus.SKIPPED]
        untrusted = [g.name for g in gates if g.untrusted]
        
        return ValidationResult(
            success=all_passed,
            tier=tier,
            language=self.language,
            gates=gates,
            artifacts_validated=validated,
            skipped_gates=skipped,
            untrusted_checks=untrusted,
            duration_ms=duration_ms,
            validate_duration_ms=duration_ms,
        )
    
    def _run_tsc(self, env: SandboxEnv, tier: Tier) -> GateResult:
        """Run TypeScript compiler for type checking."""
        start = time.time()
        
        # Use npx to run local tsc
        cmd = ["npx", "tsc", "--noEmit", "--pretty", "false"]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            
            if not passed:
                # Parse tsc output: file(line,col): error TSxxxx: message
                for line in result.stdout.splitlines():
                    if ": error TS" in line:
                        errors.append(line.strip())
            
            return GateResult(
                name="tsc",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:20],
            )
        except FileNotFoundError:
            if tier == Tier.PROD:
                raise GateError("tsc not found - did npm ci run?")
            return GateResult(
                name="tsc",
                status=GateStatus.SKIPPED,
                exit_code=-1,
                stdout="",
                stderr="tsc not found",
                duration_ms=int((time.time() - start) * 1000),
            )
        except Exception as e:
            return GateResult(
                name="tsc",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_eslint(self, env: SandboxEnv, tier: Tier) -> GateResult:
        """Run ESLint linter."""
        start = time.time()
        
        # Check if eslint is available
        eslint_check = subprocess.run(
            ["npx", "eslint", "--version"],
            cwd=env.work_dir,
            capture_output=True,
            timeout=10,
        )
        if eslint_check.returncode != 0:
            if tier == Tier.PROD:
                raise GateError("eslint not found in dependencies")
            return GateResult(
                name="eslint",
                status=GateStatus.SKIPPED,
                exit_code=-1,
                stdout="",
                stderr="eslint not installed",
                duration_ms=int((time.time() - start) * 1000),
            )
        
        cmd = ["npx", "eslint", ".", "--format", "json", "--no-error-on-unmatched-pattern"]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                capture_output=True,
                text=True,
                timeout=120,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            # ESLint returns 0 if no errors, 1 if errors, 2 if config problems
            passed = result.returncode == 0
            errors = []
            
            if result.stdout:
                try:
                    lint_results = json.loads(result.stdout)
                    for file_result in lint_results:
                        for msg in file_result.get("messages", []):
                            if msg.get("severity", 0) >= 2:  # Error level
                                errors.append(
                                    f"{file_result.get('filePath')}:{msg.get('line')}: "
                                    f"{msg.get('message')} ({msg.get('ruleId')})"
                                )
                except json.JSONDecodeError:
                    errors.append("Failed to parse ESLint JSON output")
            
            return GateResult(
                name="eslint",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:20],
            )
        except Exception as e:
            return GateResult(
                name="eslint",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_vitest(self, env: SandboxEnv, tier: Tier) -> GateResult:
        """Run Vitest test runner."""
        start = time.time()
        
        # Check if vitest is available
        vitest_check = subprocess.run(
            ["npx", "vitest", "--version"],
            cwd=env.work_dir,
            capture_output=True,
            timeout=10,
        )
        if vitest_check.returncode != 0:
            # Try jest as fallback
            jest_check = subprocess.run(
                ["npx", "jest", "--version"],
                cwd=env.work_dir,
                capture_output=True,
                timeout=10,
            )
            if jest_check.returncode == 0:
                return self._run_jest(env, tier)
            
            if tier == Tier.PROD:
                raise GateError("Neither vitest nor jest found in dependencies")
            return GateResult(
                name="vitest",
                status=GateStatus.SKIPPED,
                exit_code=-1,
                stdout="",
                stderr="vitest/jest not installed",
                duration_ms=int((time.time() - start) * 1000),
            )
        
        # CORRECTED: Use --reporter=json --outputFile per vitest docs
        output_file = env.work_dir / "vitest-results.json"
        cmd = [
            "npx", "vitest", "run",
            "--reporter=json",
            f"--outputFile={output_file}",
        ]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            
            # Parse JSON output file (primary) rather than stdout
            if output_file.exists():
                try:
                    with open(output_file) as f:
                        test_results = json.load(f)
                    
                    # Parse vitest JSON format
                    for test_file in test_results.get("testResults", []):
                        for assertion in test_file.get("assertionResults", []):
                            if assertion.get("status") == "failed":
                                errors.append(
                                    f"{test_file.get('name')}: "
                                    f"{assertion.get('title')} - FAILED"
                                )
                except json.JSONDecodeError:
                    logger.warning("Failed to parse vitest-results.json")
            
            return GateResult(
                name="vitest",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:20],
                output_file=str(output_file) if output_file.exists() else None,
            )
        except Exception as e:
            return GateResult(
                name="vitest",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def _run_jest(self, env: SandboxEnv, tier: Tier) -> GateResult:
        """Run Jest test runner (fallback for vitest)."""
        start = time.time()
        
        output_file = env.work_dir / "jest-results.json"
        cmd = [
            "npx", "jest",
            "--json",
            f"--outputFile={output_file}",
        ]
        
        try:
            result = subprocess.run(
                cmd,
                cwd=env.work_dir,
                capture_output=True,
                text=True,
                timeout=self.config.timeout_seconds,
            )
            duration_ms = int((time.time() - start) * 1000)
            
            passed = result.returncode == 0
            errors = []
            
            if output_file.exists():
                try:
                    with open(output_file) as f:
                        test_results = json.load(f)
                    
                    for test_file in test_results.get("testResults", []):
                        for assertion in test_file.get("assertionResults", []):
                            if assertion.get("status") == "failed":
                                errors.append(
                                    f"{test_file.get('name')}: "
                                    f"{assertion.get('title')} - FAILED"
                                )
                except json.JSONDecodeError:
                    pass
            
            return GateResult(
                name="jest",
                status=GateStatus.PASSED if passed else GateStatus.FAILED,
                exit_code=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                duration_ms=duration_ms,
                errors=errors[:20],
                output_file=str(output_file) if output_file.exists() else None,
            )
        except Exception as e:
            return GateResult(
                name="jest",
                status=GateStatus.FAILED,
                exit_code=-1,
                stdout="",
                stderr=str(e),
                duration_ms=int((time.time() - start) * 1000),
                errors=[str(e)],
            )
    
    def detect_artifacts(
        self,
        workspace: Path,
        expected_classes: List[str],
        expected_functions: List[str],
    ) -> Dict[str, Any]:
        """
        Detect TypeScript artifacts using TS Compiler API.
        
        PREREQUISITE: npm ci must have been run so typescript is in node_modules.
        This is Tier 1 compliant (compiler-backed, not regex).
        """
        # Detector script uses local typescript from node_modules
        detector_script = """
const ts = require('typescript');
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
    if (sourceFile.isDeclarationFile) continue;
    if (sourceFile.fileName.includes('node_modules')) continue;
    
    ts.forEachChild(sourceFile, function visit(node) {
        if (ts.isClassDeclaration(node) && node.name) {
            result.classes.push(node.name.text);
        }
        if (ts.isFunctionDeclaration(node) && node.name) {
            result.functions.push(node.name.text);
        }
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
        
        detector_path = workspace / "_artifact_detector.js"
        detector_path.write_text(detector_script)
        
        # Find .ts files (exclude node_modules, .d.ts)
        ts_files = [
            str(f) for f in workspace.rglob("*.ts")
            if "node_modules" not in str(f) and not f.name.endswith(".d.ts")
        ]
        
        if not ts_files:
            return {"classes": [], "functions": [], "error": "No .ts files found"}
        
        try:
            result = subprocess.run(
                ["node", str(detector_path)] + ts_files,
                capture_output=True,
                text=True,
                cwd=workspace,
                timeout=30,
            )
            
            if result.returncode != 0:
                return {"classes": [], "functions": [], "error": result.stderr}
            
            found = json.loads(result.stdout)
            
            # Match against expectations
            found["classes_matched"] = [
                c for c in found["classes"]
                if any(exp.lower() in c.lower() for exp in expected_classes)
            ]
            found["functions_matched"] = [
                f for f in found["functions"]
                if any(exp.lower() in f.lower() for exp in expected_functions)
            ]
            
            return found
        except Exception as e:
            return {"classes": [], "functions": [], "error": str(e)}
        finally:
            # Cleanup detector script
            if detector_path.exists():
                detector_path.unlink()
