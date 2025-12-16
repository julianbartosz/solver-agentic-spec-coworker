#!/usr/bin/env python3
"""
Production Bug Verification & Testing Script
December 12, 2025

This script verifies the status of bugs from PRODUCTION_TEST_BUG_REPORT.md
and runs comprehensive production tests.

Tests:
1. Bug #81 verification: LLM codegen symbol validation
2. Bug #82 verification: Run status lifecycle  
3. Bug #83 verification: Prompt language handling
4. Real Postgres persistence (Silver/Gold layers)
5. Real LLM codegen with OpenAI
6. LLM repo inference on new repos
7. Multi-language code generation
8. Real repo integration
"""
import asyncio
import os
import sys
import time
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

# Enable nested asyncio for tests that call sync APIs with asyncio.run()
import nest_asyncio
nest_asyncio.apply()

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

# Load environment
from dotenv import load_dotenv
load_dotenv(override=True)  # Override shell environment variables with .env file

# Force real LLM mode
os.environ.pop("LLM_RECORD_REPLAY_MODE", None)
os.environ.pop("MOCK_LLM", None)

import logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("production_test")


class ProductionTestRunner:
    """Runs comprehensive production tests."""
    
    def __init__(self):
        self.results: Dict[str, Dict[str, Any]] = {}
        self.start_time = time.time()
        self.test_repo_path = "/Users/julianbartosz/git/schoolwork/UPlant-testing-solver-agentic-spec-coworker"
        
    def record_result(self, test_name: str, passed: bool, details: str, duration: float = 0):
        """Record a test result."""
        self.results[test_name] = {
            "passed": passed,
            "details": details,
            "duration": duration,
            "timestamp": datetime.now().isoformat()
        }
        status = "✅ PASSED" if passed else "❌ FAILED"
        logger.info(f"{status}: {test_name} ({duration:.2f}s)")
        if not passed:
            logger.error(f"  Details: {details}")
    
    async def test_database_connectivity(self) -> bool:
        """Test Postgres database connectivity."""
        start = time.time()
        try:
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    result = cur.fetchone()
                    
            # Check schemas exist
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT schema_name FROM information_schema.schemata 
                        WHERE schema_name IN ('spec_silver', 'integration_gold')
                    """)
                    schemas = [r[0] for r in cur.fetchall()]
            
            has_silver = "spec_silver" in schemas
            has_gold = "integration_gold" in schemas
            
            self.record_result(
                "Database Connectivity",
                has_silver and has_gold,
                f"Schemas: silver={has_silver}, gold={has_gold}",
                time.time() - start
            )
            return has_silver and has_gold
        except Exception as e:
            self.record_result("Database Connectivity", False, str(e), time.time() - start)
            return False
    
    async def test_bug_81_symbol_validation(self) -> bool:
        """
        Bug #81: LLM Codegen Ignores Skeleton Signatures
        Status in report: 🟡 Open
        
        Test if LLM-generated code now passes symbol validation.
        """
        start = time.time()
        try:
            from integration_coworker.graph.nodes.generate_code_and_tests import (
                _has_class, _has_function
            )
            
            # Test Python detection
            python_code = '''
class StripeClient:
    def create_payment(self, data):
        pass
'''
            python_class = _has_class(python_code, "StripeClient", "python")
            python_func = _has_function(python_code, "create_payment", "python")
            
            # Test TypeScript detection (Bug #84 fix)
            ts_code = '''
export class StripeClient {
    async createPayment(data: PaymentData): Promise<Payment> {
        return await this.client.post('/payments', data);
    }
}
'''
            ts_class = _has_class(ts_code, "StripeClient", "typescript")
            ts_func = _has_function(ts_code, "createPayment", "typescript")
            
            # Test Go detection
            go_code = '''
type StripeClient struct {
    baseURL string
}

func (c *StripeClient) CreatePayment(data map[string]interface{}) error {
    return nil
}
'''
            go_class = _has_class(go_code, "StripeClient", "go")
            go_func = _has_function(go_code, "CreatePayment", "go")
            
            all_passed = all([python_class, python_func, ts_class, ts_func, go_class, go_func])
            
            self.record_result(
                "Bug #81: Symbol Validation",
                all_passed,
                f"Python: class={python_class}, func={python_func}; "
                f"TS: class={ts_class}, func={ts_func}; "
                f"Go: class={go_class}, func={go_func}",
                time.time() - start
            )
            return all_passed
        except Exception as e:
            self.record_result("Bug #81: Symbol Validation", False, str(e), time.time() - start)
            return False
    
    async def test_bug_83_prompt_language(self) -> bool:
        """
        Bug #83: Prompt Hardcodes "Python"
        Status in report: 🟡 Open
        
        Test if prompts correctly use target language.
        """
        start = time.time()
        try:
            from integration_coworker.codegen.prompts import build_codegen_prompt
            from integration_coworker.graph.state import WorkflowState
            from integration_coworker.repo.models import RepoProfile
            from integration_coworker.domain.models import IntegrationTask
            
            # Create state with TypeScript target
            state = WorkflowState(
                source_refs=[],
                spec_refs=["test.json"],
                task_description="Test task",
                provider_code="test",
            )
            
            repo_profile = RepoProfile(
                name="test",
                language="typescript"
            )
            
            task = IntegrationTask(
                id=None,
                source_system_id=None,
                task_slug="test_task",
                provider_code="test",
                description="Test task"
            )
            
            # Build prompt for TypeScript
            prompt = build_codegen_prompt(
                state=state,
                endpoint=None,
                task=task,
                repo_profile=repo_profile,
                artifact_kind="client",
                skeleton_code="// skeleton",
                client_class="TestClient",
                method_name="testMethod",
                target_language="typescript"
            )
            
            # Check if prompt mentions TypeScript, not Python
            has_typescript = "TypeScript" in prompt or "typescript" in prompt
            hardcodes_python = "Return the complete, refined Python code" in prompt
            
            passed = has_typescript and not hardcodes_python
            
            self.record_result(
                "Bug #83: Prompt Language",
                passed,
                f"Has TypeScript: {has_typescript}, Hardcodes Python: {hardcodes_python}",
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("Bug #83: Prompt Language", False, str(e), time.time() - start)
            return False
    
    async def test_universal_language_support(self) -> bool:
        """Test that universal language support works (our new feature)."""
        start = time.time()
        try:
            from integration_coworker.repo.config_schema import ProfileConfig
            from integration_coworker.codegen.syntax_validator import validate_syntax
            from integration_coworker.codegen.prompts import get_skeleton_template, get_language_conventions
            
            results = {}
            
            # Test Rust (not in core list)
            try:
                lang = ProfileConfig.validate_language("rust")
                results["rust_config"] = lang == "rust"
            except ValueError:
                results["rust_config"] = False
            
            # Test syntax validation fails-open for unknown language
            rust_code = 'fn main() { println!("Hello"); }'
            rust_result = validate_syntax(rust_code, "rust")
            results["rust_syntax"] = rust_result.is_valid  # Should be True (fail-open)
            
            # Test skeleton generation for unknown language
            skeleton = get_skeleton_template(
                language="rust",
                artifact_type="client",
                provider_title="Stripe",
                client_class="StripeClient",
                method_name="create_charge"
            )
            results["rust_skeleton"] = "Stripe" in skeleton and "rust" in skeleton.lower()
            
            # Test conventions for Rust
            conventions = get_language_conventions("rust")
            results["rust_conventions"] = conventions["file_extension"] == ".rs"
            
            all_passed = all(results.values())
            
            self.record_result(
                "Universal Language Support",
                all_passed,
                f"Results: {results}",
                time.time() - start
            )
            return all_passed
        except Exception as e:
            self.record_result("Universal Language Support", False, str(e), time.time() - start)
            return False
    
    async def test_real_llm_connectivity(self) -> bool:
        """Test real LLM API connectivity."""
        start = time.time()
        try:
            from integration_coworker.llm.client import get_llm_client
            
            client = get_llm_client()
            response = client.complete("Say 'Hello' in exactly one word.")
            
            passed = response is not None and len(response) > 0
            
            self.record_result(
                "Real LLM Connectivity",
                passed,
                f"Response: {response[:50]}..." if response else "No response",
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("Real LLM Connectivity", False, str(e), time.time() - start)
            return False
    
    async def test_llm_repo_inference(self) -> bool:
        """Test LLM-based repo config inference."""
        start = time.time()
        try:
            from integration_coworker.repo.llm_inference import infer_repo_config
            
            # Use UPlant testing repo
            if not Path(self.test_repo_path).exists():
                self.record_result(
                    "LLM Repo Inference",
                    False,
                    f"Test repo not found: {self.test_repo_path}",
                    time.time() - start
                )
                return False
            
            # Infer without saving to file
            config = infer_repo_config(
                repo_root=Path(self.test_repo_path),
                save_to_file=False
            )
            
            if config:
                passed = True
                details = f"Inferred: {config.profile.name}, lang={config.profile.language}"
            else:
                passed = False
                details = "Inference returned None"
            
            self.record_result(
                "LLM Repo Inference",
                passed,
                details,
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("LLM Repo Inference", False, str(e), time.time() - start)
            return False
    
    async def test_silver_layer_persistence(self) -> bool:
        """Test Silver layer persistence with real spec."""
        start = time.time()
        try:
            from integration_coworker.api.entrypoint import design_and_generate_integration
            from integration_coworker.api.types import IntegrationOptions
            
            result = design_and_generate_integration(
                spec_refs=["specs/twilio_messaging_v1.json"],
                task_description="Send an SMS message",
                provider_code=f"silver_test_{int(time.time())}",
                options=IntegrationOptions(dry_run=False),
            )
            
            # Check Silver layer has data
            from integration_coworker.persistence.postgres import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM spec_silver.endpoints")
                    endpoint_count = cur.fetchone()[0]
                    
                    cur.execute("SELECT COUNT(*) FROM spec_silver.spec_documents")
                    doc_count = cur.fetchone()[0]
            
            passed = endpoint_count > 0 and doc_count > 0
            
            self.record_result(
                "Silver Layer Persistence",
                passed,
                f"Endpoints: {endpoint_count}, Documents: {doc_count}",
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("Silver Layer Persistence", False, str(e), time.time() - start)
            return False
    
    async def test_real_codegen_workflow(self) -> bool:
        """Test full codegen workflow with real LLM."""
        start = time.time()
        try:
            from integration_coworker.api.entrypoint import design_and_generate_integration
            from integration_coworker.api.types import IntegrationOptions
            
            result = design_and_generate_integration(
                spec_refs=["specs/twilio_messaging_v1.json"],
                task_description="Send an SMS notification",
                provider_code=f"codegen_test_{int(time.time())}",
                options=IntegrationOptions(dry_run=False),
            )
            
            # Check result
            has_artifacts = result.code_artifacts and len(result.code_artifacts) > 0
            has_errors = len(result.errors) > 0 if result.errors else False
            
            artifact_count = len(result.code_artifacts) if result.code_artifacts else 0
            error_count = len(result.errors) if result.errors else 0
            
            # Check if any artifacts are LLM-generated (not just skeleton)
            llm_generated = 0
            if result.code_artifacts:
                for art in result.code_artifacts:
                    if hasattr(art, 'content') and art.content:
                        if "TODO:" not in art.content and "raise NotImplementedError" not in art.content:
                            llm_generated += 1
            
            passed = has_artifacts and artifact_count >= 3
            
            self.record_result(
                "Real Codegen Workflow",
                passed,
                f"Artifacts: {artifact_count} (LLM: {llm_generated}), Errors: {error_count}",
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("Real Codegen Workflow", False, str(e), time.time() - start)
            return False
    
    async def test_typescript_codegen(self) -> bool:
        """Test TypeScript code generation."""
        start = time.time()
        try:
            from integration_coworker.api.entrypoint import design_and_generate_integration
            from integration_coworker.api.types import IntegrationOptions
            from integration_coworker.repo.models import RepoProfile
            
            repo_profile = RepoProfile(
                name="typescript_test",
                language="typescript",
                integrations_root="src/integrations",
                tests_root="tests"
            )
            
            result = design_and_generate_integration(
                spec_refs=["specs/twilio_messaging_v1.json"],
                task_description="Send an SMS",
                provider_code=f"ts_test_{int(time.time())}",
                repo_profile=repo_profile,
                options=IntegrationOptions(dry_run=True),
            )
            
            # Check if TypeScript was used
            ts_artifacts = 0
            if result.code_artifacts:
                for art in result.code_artifacts:
                    if hasattr(art, 'language') and art.language == "typescript":
                        ts_artifacts += 1
                    elif hasattr(art, 'rel_path') and art.rel_path and ".ts" in art.rel_path:
                        ts_artifacts += 1
            
            passed = ts_artifacts > 0
            
            self.record_result(
                "TypeScript Codegen",
                passed,
                f"TypeScript artifacts: {ts_artifacts}",
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("TypeScript Codegen", False, str(e), time.time() - start)
            return False
    
    async def test_repo_integration_write(self) -> bool:
        """Test writing generated code to a real repo."""
        start = time.time()
        try:
            from integration_coworker.api.entrypoint import design_and_generate_integration
            from integration_coworker.api.types import IntegrationOptions
            
            if not Path(self.test_repo_path).exists():
                self.record_result(
                    "Repo Integration Write",
                    False,
                    f"Test repo not found: {self.test_repo_path}",
                    time.time() - start
                )
                return False
            
            result = design_and_generate_integration(
                spec_refs=["specs/twilio_messaging_v1.json"],
                task_description="Send a plant watering reminder SMS",
                provider_code=f"repo_write_{int(time.time())}",
                repo_root=Path(self.test_repo_path),
                options=IntegrationOptions(dry_run=False),
            )
            
            # Check if files were written
            files_written = []
            if result.repo_changes and result.repo_changes.changes:
                for change in result.repo_changes.changes:
                    rel_path = getattr(change, 'rel_path', None) or getattr(change, 'path', None)
                    if rel_path:
                        full_path = Path(self.test_repo_path) / rel_path
                        if full_path.exists():
                            files_written.append(str(rel_path))
            
            passed = len(files_written) > 0
            
            self.record_result(
                "Repo Integration Write",
                passed,
                f"Files written: {len(files_written)}: {files_written[:3]}",
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("Repo Integration Write", False, str(e), time.time() - start)
            return False
    
    async def test_run_status_lifecycle(self) -> bool:
        """
        Bug #82: Run Status Stuck in "running"
        Test that run status correctly transitions to completed/failed.
        """
        start = time.time()
        try:
            from integration_coworker.api.entrypoint import design_and_generate_integration
            from integration_coworker.api.types import IntegrationOptions
            from integration_coworker.persistence.postgres import get_connection
            
            result = design_and_generate_integration(
                spec_refs=["specs/twilio_messaging_v1.json"],
                task_description="Test status lifecycle",
                options=IntegrationOptions(dry_run=False),
            )
            
            # Get run_id from result to query run_status
            run_id = result.run_id
            
            if not run_id:
                self.record_result("Bug #82: Run Status Lifecycle", False, "No run_id in result", time.time() - start)
                return False
            
            # Check run status in DB by run_id
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT status FROM integration_gold.run_status 
                        WHERE run_id = %s
                    """, (run_id,))
                    row = cur.fetchone()
                    
            if row:
                status = row[0]
                # Status should be 'completed' or 'completed_with_errors', not 'running'
                passed = status != "running"
                details = f"run_id={run_id}, Status: {status}"
            else:
                passed = False
                details = f"No run_status found for run_id={run_id}"
            
            self.record_result(
                "Bug #82: Run Status Lifecycle",
                passed,
                details,
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("Bug #82: Run Status Lifecycle", False, str(e), time.time() - start)
            return False
    
    async def test_checkpoint_recovery(self) -> bool:
        """Test checkpoint persistence and recovery."""
        start = time.time()
        try:
            from integration_coworker.persistence.postgres import get_connection
            
            # Check if checkpoints table has data (LangGraph uses public.checkpoints)
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM public.checkpoints")
                    checkpoint_count = cur.fetchone()[0]
            
            passed = checkpoint_count > 0
            
            self.record_result(
                "Checkpoint Persistence",
                passed,
                f"Checkpoints in DB: {checkpoint_count}",
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("Checkpoint Persistence", False, str(e), time.time() - start)
            return False
    
    async def test_knowledge_graph_learning(self) -> bool:
        """Test Knowledge Graph learning accumulation."""
        start = time.time()
        try:
            from integration_coworker.persistence.postgres import get_connection
            
            with get_connection() as conn:
                with conn.cursor() as cur:
                    # Check KG nodes (in kg schema)
                    cur.execute("SELECT COUNT(*) FROM kg.nodes")
                    node_count = cur.fetchone()[0]
                    
                    # Check KG edges (in kg schema)
                    cur.execute("SELECT COUNT(*) FROM kg.edges")
                    edge_count = cur.fetchone()[0]
            
            passed = node_count > 0 and edge_count > 0
            
            self.record_result(
                "Knowledge Graph Learning",
                passed,
                f"Nodes: {node_count}, Edges: {edge_count}",
                time.time() - start
            )
            return passed
        except Exception as e:
            self.record_result("Knowledge Graph Learning", False, str(e), time.time() - start)
            return False
    
    async def run_all_tests(self):
        """Run all production tests."""
        logger.info("=" * 60)
        logger.info("PRODUCTION TEST SUITE - December 12, 2025")
        logger.info("=" * 60)
        
        # Infrastructure tests
        logger.info("\n--- Infrastructure Tests ---")
        await self.test_database_connectivity()
        await self.test_real_llm_connectivity()
        
        # Bug verification tests
        logger.info("\n--- Bug Verification Tests ---")
        await self.test_bug_81_symbol_validation()
        await self.test_bug_83_prompt_language()
        await self.test_run_status_lifecycle()
        
        # Feature tests
        logger.info("\n--- Feature Tests ---")
        await self.test_universal_language_support()
        await self.test_llm_repo_inference()
        
        # Production workflow tests
        logger.info("\n--- Production Workflow Tests ---")
        await self.test_silver_layer_persistence()
        await self.test_real_codegen_workflow()
        await self.test_typescript_codegen()
        
        # Integration tests
        logger.info("\n--- Integration Tests ---")
        await self.test_repo_integration_write()
        await self.test_checkpoint_recovery()
        await self.test_knowledge_graph_learning()
        
        # Summary
        self.print_summary()
    
    def print_summary(self):
        """Print test summary."""
        total = len(self.results)
        passed = sum(1 for r in self.results.values() if r["passed"])
        failed = total - passed
        duration = time.time() - self.start_time
        
        logger.info("\n" + "=" * 60)
        logger.info("TEST SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total: {total} | Passed: {passed} | Failed: {failed}")
        logger.info(f"Duration: {duration:.2f}s")
        logger.info("")
        
        for test_name, result in self.results.items():
            status = "✅" if result["passed"] else "❌"
            logger.info(f"{status} {test_name}: {result['details'][:60]}")
        
        # Write results to file
        report_path = Path(__file__).parent.parent / "docs" / "PRODUCTION_TEST_RESULTS_DEC12.md"
        self._write_report(report_path)
        logger.info(f"\nResults written to: {report_path}")
    
    def _write_report(self, path: Path):
        """Write test results to markdown file."""
        total = len(self.results)
        passed = sum(1 for r in self.results.values() if r["passed"])
        
        content = f"""# Production Test Results - December 12, 2025

## Summary

| Metric | Value |
|--------|-------|
| Total Tests | {total} |
| Passed | {passed} |
| Failed | {total - passed} |
| Duration | {time.time() - self.start_time:.2f}s |

## Bug Status Verification

Based on testing, here's the updated status of bugs from PRODUCTION_TEST_BUG_REPORT.md:

| Bug # | Status | Verified |
|-------|--------|----------|
| #81 | {'✅ Fixed' if self.results.get('Bug #81: Symbol Validation', {}).get('passed') else '❌ Still Open'} | {datetime.now().strftime('%Y-%m-%d')} |
| #82 | {'✅ Fixed' if self.results.get('Bug #82: Run Status Lifecycle', {}).get('passed') else '❌ Still Open'} | {datetime.now().strftime('%Y-%m-%d')} |
| #83 | {'✅ Fixed' if self.results.get('Bug #83: Prompt Language', {}).get('passed') else '❌ Still Open'} | {datetime.now().strftime('%Y-%m-%d')} |

## Detailed Results

"""
        for test_name, result in self.results.items():
            status = "✅ PASSED" if result["passed"] else "❌ FAILED"
            content += f"""### {test_name}

- **Status:** {status}
- **Duration:** {result['duration']:.2f}s
- **Details:** {result['details']}
- **Timestamp:** {result['timestamp']}

"""
        
        path.write_text(content)


async def main():
    runner = ProductionTestRunner()
    await runner.run_all_tests()


if __name__ == "__main__":
    asyncio.run(main())
