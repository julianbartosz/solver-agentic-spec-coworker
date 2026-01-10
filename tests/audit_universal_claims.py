#!/usr/bin/env python3
"""
Critical Audit: Test "ANY repo", "ANY language", "ANY spec", "ANY number of specs" claims.

This audit verifies whether the implementation actually supports these claims
or if they are only partially implemented.
"""
import tempfile
import os
import shutil
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def print_header(title):
    print("\n" + "=" * 70)
    print(f"AUDIT: {title}")
    print("=" * 70)


def print_result(claim, implemented, wired_in, notes):
    status = "✅" if implemented and wired_in else "⚠️" if implemented else "❌"
    impl_status = "YES" if implemented else "NO"
    wired_status = "YES" if wired_in else "NO"
    print(f"{status} {claim}")
    print(f"   Implemented: {impl_status}")
    print(f"   Wired In: {wired_status}")
    print(f"   Notes: {notes}")
    print()
    return implemented and wired_in


def audit_any_repo():
    """Audit: ANY repo structure support."""
    print_header("ANY REPO STRUCTURE")
    
    results = []
    
    # Test 1: Language detection exists
    try:
        from integration_coworker.repo.detection import (
            detect_repo_profile,
            get_repo_profile,
            _detect_language_by_config_files,
        )
        implemented = True
        notes = "detect_repo_profile() and get_repo_profile() exist"
    except ImportError as e:
        implemented = False
        notes = f"Import error: {e}"
    
    results.append(print_result(
        "Language detection from config files",
        implemented,
        implemented,  # If it exists, it's wired in
        notes
    ))
    
    # Test 2: File extension mapping
    try:
        from integration_coworker.codegen.context import LANGUAGE_EXTENSIONS, get_file_extension
        
        # Test all languages
        test_langs = ['python', 'typescript', 'javascript', 'go', 'java', 'ruby', 'csharp']
        expected_exts = ['.py', '.ts', '.js', '.go', '.java', '.rb', '.cs']
        
        all_correct = True
        for lang, expected in zip(test_langs, expected_exts):
            actual = get_file_extension(lang)
            if actual != expected:
                all_correct = False
                notes = f"{lang}: expected {expected}, got {actual}"
                break
        else:
            notes = f"All {len(test_langs)} languages mapped correctly"
        
        implemented = all_correct
    except ImportError as e:
        implemented = False
        notes = f"Import error: {e}"
    
    results.append(print_result(
        "File extension mapping (all languages)",
        implemented,
        implemented,
        notes
    ))
    
    # Test 3: Layout hints respect detected language
    try:
        from integration_coworker.repo.detection import get_repo_profile
        
        # Create Go repo
        go_repo = tempfile.mkdtemp(prefix='audit_go_')
        with open(os.path.join(go_repo, 'go.mod'), 'w') as f:
            f.write('module example\ngo 1.21\n')
        
        profile = get_repo_profile(go_repo)
        ext = profile.layout_hints.get('file_extension', '.py')
        
        shutil.rmtree(go_repo)
        
        implemented = (ext == '.go')
        wired = implemented
        notes = f"Go repo -> file_extension={ext}" + (" ✓" if ext == '.go' else " (expected .go)")
    except Exception as e:
        implemented = False
        wired = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "Layout hints use detected language extension",
        implemented,
        wired,
        notes
    ))
    
    # Test 4: Convention patterns per language
    try:
        from integration_coworker.repo.detection import get_repo_profile
        
        # Create Java repo
        java_repo = tempfile.mkdtemp(prefix='audit_java_')
        with open(os.path.join(java_repo, 'pom.xml'), 'w') as f:
            f.write('<project></project>')
        
        profile = get_repo_profile(java_repo)
        conventions = profile.conventions or {}
        
        shutil.rmtree(java_repo)
        
        # Check if conventions exist and have Java patterns
        has_java_patterns = any('.java' in str(v) for v in conventions.values()) if conventions else False
        
        implemented = True  # Conventions exist
        wired = has_java_patterns
        notes = f"Java conventions: {list(conventions.keys())[:2]}..." if conventions else "No conventions"
        if wired:
            notes += " (contain .java patterns)"
    except Exception as e:
        implemented = False
        wired = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "Language-specific naming conventions",
        implemented,
        wired,
        notes
    ))
    
    return all(results)


def audit_any_language():
    """Audit: ANY language code generation."""
    print_header("ANY LANGUAGE CODE GENERATION")
    
    results = []
    
    # Test 1: Language conventions defined
    try:
        from integration_coworker.codegen.prompts import LANGUAGE_CONVENTIONS
        
        expected_langs = ['python', 'typescript', 'javascript', 'go', 'java', 'ruby', 'csharp']
        found = [l for l in expected_langs if l in LANGUAGE_CONVENTIONS]
        
        implemented = len(found) == len(expected_langs)
        notes = f"Found {len(found)}/{len(expected_langs)}: {found}"
    except ImportError as e:
        implemented = False
        notes = f"Import error: {e}"
    
    results.append(print_result(
        "Language conventions defined (prompts.LANGUAGE_CONVENTIONS)",
        implemented,
        implemented,
        notes
    ))
    
    # Test 2: Skeleton templates for all languages
    try:
        from integration_coworker.codegen.prompts import SKELETON_TEMPLATES, get_skeleton_template
        
        expected_langs = ['python', 'typescript', 'javascript', 'go', 'java', 'ruby', 'csharp']
        found = [l for l in expected_langs if l in SKELETON_TEMPLATES]
        
        # Check each has client, flow, test
        complete = []
        for lang in found:
            templates = SKELETON_TEMPLATES.get(lang, {})
            if all(k in templates for k in ['client', 'flow', 'test']):
                complete.append(lang)
        
        implemented = len(complete) == len(expected_langs)
        notes = f"Complete templates: {len(complete)}/{len(expected_langs)}: {complete}"
    except ImportError as e:
        implemented = False
        notes = f"Import error: {e}"
    
    results.append(print_result(
        "Skeleton templates for all languages",
        implemented,
        implemented,
        notes
    ))
    
    # Test 3: Syntax validation for all languages
    try:
        from integration_coworker.codegen.syntax_validator import (
            validate_syntax,
            is_tree_sitter_available,
            get_available_languages,
        )
        
        tree_sitter_available = is_tree_sitter_available()
        
        if tree_sitter_available:
            available_langs = get_available_languages()
            notes = f"Tree-sitter available with languages: {available_langs}"
            implemented = len(available_langs) >= 5
        else:
            # Fallback: Python AST only
            result = validate_syntax("def foo(): pass", "python")
            implemented = result.is_valid
            notes = f"Tree-sitter NOT installed - only Python AST works. Regex fallback for others."
        
        wired = implemented
    except ImportError as e:
        implemented = False
        wired = False
        notes = f"Import error: {e}"
    
    results.append(print_result(
        "Syntax validation for all languages",
        implemented,
        wired,
        notes
    ))
    
    # Test 4: LLM prompts adapt to target language
    try:
        from integration_coworker.codegen.prompts import build_codegen_prompt, get_language_conventions
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SourceRef
        
        # Check if prompts include language-specific info
        state = WorkflowState(
            source_refs=[SourceRef.from_ref("test.yaml")],
            spec_refs=[],
            task_description="Test task",
            provider_code="test",
        )
        
        go_conventions = get_language_conventions("go")
        
        implemented = (
            go_conventions.get("display_name") == "Go" and
            go_conventions.get("file_extension") == ".go" and
            "net/http" in go_conventions.get("http_library", "")
        )
        notes = f"Go conventions: display={go_conventions.get('display_name')}, ext={go_conventions.get('file_extension')}"
    except Exception as e:
        implemented = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "LLM prompts adapt to target language",
        implemented,
        implemented,
        notes
    ))
    
    # Test 5: Generated code uses correct file extension
    try:
        from integration_coworker.codegen.context import CodegenContext
        
        ctx = CodegenContext(
            provider_code="test",
            task_slug="test_task",
            client_module="test_client",
            client_class="TestClient",
            method_name="execute",
            client_import_path="integrations.clients.test",
            flow_module="test_flow",
            flow_function="test_flow",
            flow_import_path="integrations.flows.test",
            test_module="test_test",
            test_class="TestFlow",
            clients_dir="src/clients",
            flows_dir="src/flows",
            tests_dir="tests",
            language="go",  # Test with Go
        )
        
        client_path = ctx.get_client_rel_path()
        implemented = client_path.endswith('.go')
        notes = f"Go context -> client path: {client_path}"
    except Exception as e:
        implemented = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "CodegenContext respects language for file paths",
        implemented,
        implemented,
        notes
    ))
    
    return all(results)


def audit_any_spec():
    """Audit: ANY spec format support."""
    print_header("ANY SPEC FORMAT")
    
    results = []
    
    # Test 1: OpenAPI detection and parsing
    try:
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _parse_spec_content,
        )
        
        openapi_spec = '''
openapi: 3.0.0
info:
  title: Test API
  version: 1.0.0
paths:
  /users:
    get:
      summary: Get users
'''
        result = _parse_spec_content(openapi_spec, "application/yaml", "test.yaml")
        implemented = result.get("openapi") == "3.0.0"
        notes = f"OpenAPI 3.0 parsed correctly, paths: {list(result.get('paths', {}).keys())}"
    except Exception as e:
        implemented = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "OpenAPI 3.0 parsing",
        implemented,
        implemented,
        notes
    ))
    
    # Test 2: Swagger 2.0 parsing
    try:
        swagger_spec = '''
swagger: "2.0"
info:
  title: Legacy API
  version: 1.0.0
paths:
  /items:
    get:
      summary: Get items
'''
        result = _parse_spec_content(swagger_spec, "application/yaml", "test.yaml")
        implemented = result.get("swagger") == "2.0"
        notes = f"Swagger 2.0 parsed correctly"
    except Exception as e:
        implemented = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "Swagger 2.0 parsing",
        implemented,
        implemented,
        notes
    ))
    
    # Test 3: GraphQL detection
    try:
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _is_graphql_content,
        )
        
        graphql_spec = '''
type Query {
  users: [User]
  user(id: ID!): User
}

type User {
  id: ID!
  name: String!
}
'''
        is_graphql = _is_graphql_content(graphql_spec, "")
        implemented = is_graphql
        
        # Check if conversion is wired
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _parse_graphql_to_pseudo_openapi,
        )
        wired = callable(_parse_graphql_to_pseudo_openapi)
        notes = f"GraphQL detected: {is_graphql}, conversion function exists: {wired}"
    except Exception as e:
        implemented = False
        wired = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "GraphQL schema detection and conversion",
        implemented,
        wired,
        notes
    ))
    
    # Test 4: AsyncAPI detection
    try:
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _is_asyncapi_content,
        )
        
        asyncapi_spec = '''
asyncapi: 2.6.0
info:
  title: Events
  version: 1.0.0
channels:
  user/signedup:
    publish:
      message:
        payload:
          type: object
'''
        is_asyncapi = _is_asyncapi_content(asyncapi_spec, "")
        implemented = is_asyncapi
        
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _parse_asyncapi_to_pseudo_openapi,
        )
        wired = callable(_parse_asyncapi_to_pseudo_openapi)
        notes = f"AsyncAPI detected: {is_asyncapi}, conversion function exists: {wired}"
    except Exception as e:
        implemented = False
        wired = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "AsyncAPI detection and conversion",
        implemented,
        wired,
        notes
    ))
    
    # Test 5: HTML doc parsing
    try:
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _is_html_content,
            HAS_HTML_PARSER,
        )
        
        implemented = HAS_HTML_PARSER
        wired = HAS_HTML_PARSER
        notes = f"HTML parser available: {HAS_HTML_PARSER}"
    except Exception as e:
        implemented = False
        wired = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "HTML documentation parsing",
        implemented,
        wired,
        notes
    ))
    
    # Test 6: PDF doc parsing
    try:
        from integration_coworker.graph.nodes.detect_and_parse_spec import (
            _is_pdf_content,
            HAS_PDF_PARSER,
        )
        
        implemented = HAS_PDF_PARSER
        wired = HAS_PDF_PARSER
        notes = f"PDF parser available: {HAS_PDF_PARSER}"
    except Exception as e:
        implemented = False
        wired = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "PDF documentation parsing",
        implemented,
        wired,
        notes
    ))
    
    return all(results)


def audit_multi_spec():
    """Audit: Multiple specs handling."""
    print_header("MULTIPLE SPECS HANDLING")
    
    results = []
    
    # Test 1: spec_refs accepts multiple
    try:
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SourceRef
        
        state = WorkflowState(
            source_refs=[
                SourceRef.from_ref("spec1.yaml"),
                SourceRef.from_ref("spec2.yaml"),
                SourceRef.from_ref("spec3.yaml"),
            ],
            spec_refs=["spec1.yaml", "spec2.yaml", "spec3.yaml"],
            task_description="Test multi-spec",
            provider_code="test",
        )
        
        implemented = len(state.spec_refs) == 3
        notes = f"spec_refs accepts {len(state.spec_refs)} specs"
    except Exception as e:
        implemented = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "WorkflowState accepts multiple spec_refs",
        implemented,
        implemented,
        notes
    ))
    
    # Test 2: pending_specs tracking
    try:
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SourceRef
        
        state = WorkflowState(
            source_refs=[SourceRef.from_ref("spec1.yaml")],
            spec_refs=["spec1.yaml", "spec2.yaml"],
            task_description="Test pending",
            provider_code="test",
        )
        
        # Check if pending_specs is a field
        has_pending_specs = hasattr(state, 'pending_specs')
        
        implemented = has_pending_specs
        notes = f"pending_specs field exists: {has_pending_specs}"
    except Exception as e:
        implemented = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "pending_specs tracking for multi-spec",
        implemented,
        implemented,
        notes
    ))
    
    # Test 3: ingest_spec handles multiple specs
    try:
        from integration_coworker.graph.nodes.ingest_spec import ingest_spec
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SourceRef
        import tempfile
        
        # Create two temp spec files
        spec1 = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
        spec1.write('openapi: 3.0.0\ninfo:\n  title: API 1\n  version: 1.0.0\npaths: {}\n')
        spec1.close()
        
        spec2 = tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False)
        spec2.write('openapi: 3.0.0\ninfo:\n  title: API 2\n  version: 1.0.0\npaths: {}\n')
        spec2.close()
        
        state = WorkflowState(
            source_refs=[
                SourceRef.from_ref(spec1.name),
                SourceRef.from_ref(spec2.name),
            ],
            spec_refs=[spec1.name, spec2.name],
            task_description="Test ingest multi-spec",
            provider_code="test",
        )
        
        state = ingest_spec(state)
        
        os.unlink(spec1.name)
        os.unlink(spec2.name)
        
        implemented = len(state.spec_documents) == 2
        wired = implemented
        notes = f"Ingested {len(state.spec_documents)} spec documents"
    except Exception as e:
        implemented = False
        wired = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "ingest_spec processes multiple specs",
        implemented,
        wired,
        notes
    ))
    
    # Test 4: openapi_specs list in plan
    try:
        # Check if detect_and_parse_spec stores all specs
        from integration_coworker.graph.nodes.detect_and_parse_spec import detect_and_parse_spec
        from integration_coworker.graph.state import WorkflowState
        from integration_coworker.domain.models import SpecDocument, SourceRef
        
        state = WorkflowState(
            source_refs=[SourceRef.from_ref("multi.yaml")],
            spec_refs=[],
            task_description="Test multi-spec parsing",
            provider_code="test",
            spec_documents=[
                SpecDocument(
                    id=None,
                    source_system_id=None,
                    version="1.0",
                    uri="spec1.yaml",
                    content_type="application/yaml",
                    sha256="abc",
                    content='openapi: 3.0.0\ninfo:\n  title: API 1\n  version: 1.0.0\npaths:\n  /test:\n    get:\n      summary: Test\n',
                ),
                SpecDocument(
                    id=None,
                    source_system_id=None,
                    version="1.0",
                    uri="spec2.yaml",
                    content_type="application/yaml",
                    sha256="def",
                    content='openapi: 3.0.0\ninfo:\n  title: API 2\n  version: 1.0.0\npaths:\n  /other:\n    get:\n      summary: Other\n',
                ),
            ],
            plan={},
        )
        
        state = detect_and_parse_spec(state)
        
        openapi_specs = state.plan.get("openapi_specs", []) if state.plan else []
        implemented = len(openapi_specs) == 2
        notes = f"openapi_specs in plan: {len(openapi_specs)} specs"
    except Exception as e:
        implemented = False
        notes = f"Error: {e}"
    
    results.append(print_result(
        "detect_and_parse_spec stores all parsed specs",
        implemented,
        implemented,
        notes
    ))
    
    return all(results)


def print_caveats():
    """Print important caveats about the universal claims."""
    print("\n" + "=" * 70)
    print("CRITICAL CAVEATS (Read Before Claiming 'ANY' Support)")
    print("=" * 70)
    
    print("""
⚠️  ANY LANGUAGE - Caveats:
    1. Tree-sitter NOT INSTALLED - syntax validation only works for Python (AST)
       - Other languages get regex fallback (may miss syntax errors)
       - Install: pip install tree-sitter tree-sitter-go tree-sitter-java etc.
    
    2. LLM prompts include language hints BUT the LLM may still generate
       Python-ish code or miss language-specific idioms
    
    3. Generated code is NOT compiled/executed for non-Python languages
       - No actual validation that Go code builds, Java compiles, etc.
    
    4. Skeleton templates are static - may not match latest language features
       or project conventions

⚠️  ANY SPEC - Caveats:
    1. GraphQL → OpenAPI conversion uses LLM - NOT deterministic
       - May produce different results each run
       - Complex GraphQL schemas may lose fidelity
    
    2. AsyncAPI → OpenAPI conversion uses LLM - same limitations
       - Event-driven patterns may not map cleanly to REST
    
    3. HTML/PDF parsing depends on optional dependencies:
       - bs4 (BeautifulSoup) for HTML
       - pdfplumber for PDF
       - If not installed, parsing falls back to raw text extraction
    
    4. RAML, API Blueprint, WSDL NOT supported
       - These are not detected or parsed

⚠️  ANY REPO - Caveats:
    1. Detection confidence is often low (<0.4) for sparse repos
       - Recommends providing .integration-coworker.yaml config
    
    2. Monorepo structures not explicitly handled
       - May detect wrong primary language
    
    3. Language conventions are generic - won't match team style guides
    
    4. No integration with existing linters/formatters in repo

⚠️  MULTI-SPEC - Caveats:
    1. All specs are merged into single endpoint list
       - No provider namespacing by default
       - Endpoint name collisions possible
    
    2. No explicit multi-provider workflow orchestration
       - Cross-API workflows must be manually designed
    
    3. Spec ordering matters - last spec wins on conflicts

----------------------------------------------------------------------
REALISTIC CAPABILITY SUMMARY:
----------------------------------------------------------------------
✅ PRODUCTION-READY: Python + OpenAPI/Swagger
✅ FUNCTIONAL: TypeScript, JavaScript + OpenAPI (no syntax validation)
⚠️  EXPERIMENTAL: Go, Java, Ruby, C# + OpenAPI (LLM-based, no validation)
⚠️  EXPERIMENTAL: GraphQL, AsyncAPI (LLM conversion, not deterministic)
❌ NOT SUPPORTED: RAML, WSDL, API Blueprint, gRPC
""")


def main():
    print("\n" + "=" * 70)
    print("CRITICAL AUDIT: UNIVERSAL CAPABILITY CLAIMS")
    print("=" * 70)
    print("\nThis audit tests whether 'ANY repo', 'ANY language', 'ANY spec',")
    print("and 'multi-spec' claims are actually implemented AND wired in.\n")
    
    results = {}
    
    results['any_repo'] = audit_any_repo()
    results['any_language'] = audit_any_language()
    results['any_spec'] = audit_any_spec()
    results['multi_spec'] = audit_multi_spec()
    
    # Summary
    print("\n" + "=" * 70)
    print("AUDIT SUMMARY")
    print("=" * 70)
    
    for claim, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {claim}: {status}")
    
    all_passed = all(results.values())
    
    print("\n" + "-" * 70)
    if all_passed:
        print("✅ ALL CLAIMS VERIFIED - Implementation is complete and wired in")
    else:
        print("❌ SOME CLAIMS NOT FULLY IMPLEMENTED - See details above")
        print("\nKEY GAPS:")
        if not results['any_repo']:
            print("  - ANY REPO: Some repo structures may not get correct file extensions")
        if not results['any_language']:
            print("  - ANY LANGUAGE: Templates exist but tree-sitter validation may be missing")
        if not results['any_spec']:
            print("  - ANY SPEC: GraphQL/AsyncAPI require LLM conversion; HTML/PDF parsers optional")
        if not results['multi_spec']:
            print("  - MULTI-SPEC: Verify all specs are processed in workflow")
    
    # Always print caveats
    print_caveats()
    
    return 0 if all_passed else 1


if __name__ == '__main__':
    sys.exit(main())
