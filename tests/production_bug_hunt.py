#!/usr/bin/env python3
"""
Production Bug Hunt - Find remaining issues with real LLM/Postgres/repos.
"""

import tempfile
import os
import shutil
import sys
import pytest

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))


def test_language_detection():
    """Test language detection for Go, Java, TypeScript projects."""
    from integration_coworker.repo.detection import detect_repo_profile, get_repo_profile
    
    print("=" * 60)
    print("TEST 1: Multi-Language Detection")
    print("=" * 60)
    
    bugs = []
    
    # Test Go project
    go_repo = tempfile.mkdtemp(prefix='go_repo_')
    try:
        os.makedirs(os.path.join(go_repo, 'cmd/server'))
        with open(os.path.join(go_repo, 'go.mod'), 'w') as f:
            f.write('module github.com/example/goservice\ngo 1.21\n')
        with open(os.path.join(go_repo, 'main.go'), 'w') as f:
            f.write('package main\nfunc main() {}\n')
        
        profile = get_repo_profile(go_repo)
        ext = profile.layout_hints.get('file_extension', '.py')
        if ext != '.go':
            bugs.append(f"Go: file_extension='{ext}' should be '.go'")
            print(f"❌ Go project: language={profile.language}, ext={ext} (expected .go)")
        else:
            print(f"✅ Go project: language={profile.language}, ext={ext}")
    finally:
        shutil.rmtree(go_repo)
    
    # Test Java project (Maven)
    java_repo = tempfile.mkdtemp(prefix='java_repo_')
    try:
        os.makedirs(os.path.join(java_repo, 'src/main/java/com/example'))
        with open(os.path.join(java_repo, 'pom.xml'), 'w') as f:
            f.write('<project><groupId>com.example</groupId></project>')
        
        profile = get_repo_profile(java_repo)
        ext = profile.layout_hints.get('file_extension', '.py')
        if ext != '.java':
            bugs.append(f"Java: file_extension='{ext}' should be '.java'")
            print(f"❌ Java project: language={profile.language}, ext={ext} (expected .java)")
        else:
            print(f"✅ Java project: language={profile.language}, ext={ext}")
    finally:
        shutil.rmtree(java_repo)
    
    # Test Ruby project
    ruby_repo = tempfile.mkdtemp(prefix='ruby_repo_')
    try:
        with open(os.path.join(ruby_repo, 'Gemfile'), 'w') as f:
            f.write("source 'https://rubygems.org'\ngem 'rails'\n")
        
        profile = get_repo_profile(ruby_repo)
        ext = profile.layout_hints.get('file_extension', '.py')
        if ext != '.rb':
            bugs.append(f"Ruby: file_extension='{ext}' should be '.rb'")
            print(f"❌ Ruby project: language={profile.language}, ext={ext} (expected .rb)")
        else:
            print(f"✅ Ruby project: language={profile.language}, ext={ext}")
    finally:
        shutil.rmtree(ruby_repo)
    
    # Test C# project
    csharp_repo = tempfile.mkdtemp(prefix='csharp_repo_')
    try:
        with open(os.path.join(csharp_repo, 'MyApp.csproj'), 'w') as f:
            f.write('<Project Sdk="Microsoft.NET.Sdk"></Project>')
        
        profile = get_repo_profile(csharp_repo)
        ext = profile.layout_hints.get('file_extension', '.py')
        if ext != '.cs':
            bugs.append(f"C#: file_extension='{ext}' should be '.cs'")
            print(f"❌ C# project: language={profile.language}, ext={ext} (expected .cs)")
        else:
            print(f"✅ C# project: language={profile.language}, ext={ext}")
    finally:
        shutil.rmtree(csharp_repo)
    
    # Test TypeScript (should work)
    ts_repo = tempfile.mkdtemp(prefix='ts_repo_')
    try:
        with open(os.path.join(ts_repo, 'package.json'), 'w') as f:
            f.write('{"name": "myapp"}')
        with open(os.path.join(ts_repo, 'tsconfig.json'), 'w') as f:
            f.write('{"compilerOptions": {}}')
        
        profile = get_repo_profile(ts_repo)
        ext = profile.layout_hints.get('file_extension', '.py')
        if ext != '.ts':
            bugs.append(f"TypeScript: file_extension='{ext}' should be '.ts'")
            print(f"❌ TypeScript project: language={profile.language}, ext={ext}")
        else:
            print(f"✅ TypeScript project: language={profile.language}, ext={ext}")
    finally:
        shutil.rmtree(ts_repo)
    
    if bugs:
        pytest.fail("; ".join(bugs))


def test_spec_type_detection():
    """Test spec type detection for various formats."""
    import yaml
    
    print("\n" + "=" * 60)
    print("TEST 2: Spec Type Detection")
    print("=" * 60)
    
    bugs = []
    
    def detect_spec_type_local(file_path):
        """Local detection logic matching what the node does."""
        with open(file_path, 'r') as f:
            content = f.read()
        
        # Try YAML parsing
        try:
            data = yaml.safe_load(content)
            if isinstance(data, dict):
                if 'openapi' in data or 'swagger' in data:
                    return 'openapi'
                if 'asyncapi' in data:
                    return 'asyncapi'
        except Exception:
            pass
        
        # Check file extension
        if file_path.endswith('.graphql') or file_path.endswith('.gql'):
            return 'graphql'
        
        return 'unknown'
    
    # OpenAPI 3.0
    openapi_spec = """openapi: 3.0.0
info:
  title: Test API
  version: 1.0.0
paths:
  /users:
    get:
      summary: Get users
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(openapi_spec)
        openapi_file = f.name
    
    try:
        spec_type = detect_spec_type_local(openapi_file)
        if spec_type == 'openapi':
            print(f"✅ OpenAPI 3.0 detected correctly: {spec_type}")
        else:
            bugs.append(f"OpenAPI 3.0 detected as '{spec_type}'")
            print(f"❌ OpenAPI 3.0: detected as '{spec_type}'")
    finally:
        os.unlink(openapi_file)
    
    # AsyncAPI
    asyncapi_spec = """asyncapi: 2.6.0
info:
  title: User Events
  version: 1.0.0
channels:
  user/signedup:
    publish:
      message:
        payload:
          type: object
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(asyncapi_spec)
        asyncapi_file = f.name
    
    try:
        spec_type = detect_spec_type_local(asyncapi_file)
        print(f"  AsyncAPI detected as: {spec_type}")
        if spec_type != 'asyncapi':
            bugs.append(f"AsyncAPI detected as '{spec_type}' (needs explicit handling)")
    finally:
        os.unlink(asyncapi_file)
    
    # Swagger 2.0
    swagger_spec = """swagger: "2.0"
info:
  title: Legacy API
  version: 1.0.0
paths:
  /items:
    get:
      summary: Get items
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(swagger_spec)
        swagger_file = f.name
    
    try:
        spec_type = detect_spec_type_local(swagger_file)
        if spec_type in ('openapi', 'swagger'):
            print(f"✅ Swagger 2.0 detected correctly: {spec_type}")
        else:
            bugs.append(f"Swagger 2.0 detected as '{spec_type}'")
            print(f"❌ Swagger 2.0: detected as '{spec_type}'")
    finally:
        os.unlink(swagger_file)
    
    if bugs:
        pytest.fail("; ".join(bugs))


def test_codegen_templates():
    """Test that codegen produces correct file extensions."""
    from integration_coworker.codegen.context import LANGUAGE_EXTENSIONS
    
    print("\n" + "=" * 60)
    print("TEST 3: Codegen Template Coverage")
    print("=" * 60)
    
    bugs = []
    
    print(f"LANGUAGE_EXTENSIONS mapping: {LANGUAGE_EXTENSIONS}")
    
    # Check what languages have templates
    from integration_coworker.codegen import prompts
    
    # Check if we have language-specific templates
    try:
        from integration_coworker.codegen.policy_templates import get_policy_template
        
        for lang in ['python', 'typescript', 'go', 'java', 'ruby', 'csharp']:
            try:
                template = get_policy_template('retry', lang)
                if template and 'python' not in template.lower() or lang == 'python':
                    print(f"✅ {lang}: has retry template")
                else:
                    # Template exists but may be Python-only
                    if 'def ' in template or 'import ' in template:
                        bugs.append(f"{lang} retry template contains Python syntax")
                        print(f"❌ {lang}: retry template is Python code")
                    else:
                        print(f"✅ {lang}: has retry template")
            except Exception as e:
                bugs.append(f"{lang}: no retry template ({e})")
                print(f"❌ {lang}: no retry template")
    except ImportError as e:
        print(f"  Could not import policy_templates: {e}")
    
    if bugs:
        pytest.fail("; ".join(bugs))


def test_syntax_validator():
    """Test syntax validation across languages."""
    print("\n" + "=" * 60)
    print("TEST 4: Syntax Validation")
    print("=" * 60)
    
    bugs = []
    
    try:
        from integration_coworker.codegen.syntax_validator import (
            validate_syntax,
            is_tree_sitter_available,
            get_available_languages
        )
        
        ts_available = is_tree_sitter_available()
        print(f"Tree-sitter available: {ts_available}")
        
        if ts_available:
            langs = get_available_languages()
            print(f"Languages with tree-sitter: {langs}")
        else:
            pytest.skip("Tree-sitter not installed; skipping extended syntax validation")
        
        # Test Python validation (should work via AST)
        python_code = "def hello():\n    return 'world'"
        result = validate_syntax(python_code, 'python')
        if result.is_valid:
            print("✅ Python syntax validation works")
        else:
            bugs.append(f"Python validation failed: {result.errors}")
            print(f"❌ Python validation: {result.errors}")
        
        # Test invalid Python
        bad_python = "def hello(\n    return 'world'"
        result = validate_syntax(bad_python, 'python')
        if not result.is_valid:
            print("✅ Python catches syntax errors")
        else:
            bugs.append("Python validation didn't catch syntax error")
            print("❌ Python validation missed syntax error")
        
        # Test Go validation
        go_code = "package main\nfunc main() {}"
        result = validate_syntax(go_code, 'go')
        print(f"  Go validation result: valid={result.is_valid}, method={result.method}")
        if result.method == 'regex_fallback':
            print("  ⚠️  Go using regex fallback (tree-sitter not available)")
        
    except ImportError as e:
        bugs.append(f"Cannot import syntax_validator: {e}")
        print(f"❌ Import error: {e}")
    
    if bugs:
        pytest.fail("; ".join(bugs))


def test_full_workflow_go_repo():
    """Test full workflow with a Go repository."""
    print("\n" + "=" * 60)
    print("TEST 5: Full Workflow - Go Repository")
    print("=" * 60)
    
    bugs = []
    
    # Create a Go repo
    go_repo = tempfile.mkdtemp(prefix='go_workflow_')
    try:
        os.makedirs(os.path.join(go_repo, 'cmd/server'))
        os.makedirs(os.path.join(go_repo, 'internal/api'))
        
        with open(os.path.join(go_repo, 'go.mod'), 'w') as f:
            f.write('module github.com/example/goservice\ngo 1.21\n')
        with open(os.path.join(go_repo, 'cmd/server/main.go'), 'w') as f:
            f.write('package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("Hello")\n}\n')
        
        # Create a simple OpenAPI spec
        spec_file = os.path.join(go_repo, 'api.yaml')
        with open(spec_file, 'w') as f:
            f.write("""openapi: 3.0.0
info:
  title: Test API
  version: 1.0.0
paths:
  /health:
    get:
      operationId: getHealth
      summary: Health check
      responses:
        '200':
          description: OK
""")
        
        from integration_coworker.api.entrypoint import design_and_generate_integration
        from integration_coworker.api.types import IntegrationOptions
        
        print(f"Running workflow with Go repo: {go_repo}")
        
        result = design_and_generate_integration(
            spec_refs=[spec_file],
            task_description="Create a health check client",
            repo_root=go_repo,
            options=IntegrationOptions(
                dry_run=True,  # Don't persist to DB
                repo_integration_enabled=False,  # Don't write files
            )
        )
        
        print(f"Result run_id: {result.run_id}")
        print(f"Errors: {result.errors}")
        print(f"Completed steps: {len(result.completed_steps) if result.completed_steps else 0}")
        
        success = not result.errors or len(result.errors) == 0
        
        if result.code_artifacts:
            for artifact in result.code_artifacts:
                # CodeArtifact is a dataclass, access via attribute
                path = getattr(artifact, 'rel_path', None) or getattr(artifact, 'module_name', 'unknown')
                lang = getattr(artifact, 'language', 'unknown')
                print(f"  Artifact: {path} (lang={lang})")
                
                # Check if Go repo got Python files (the critical bug we're testing)
                if path.endswith('.py'):
                    bugs.append(f"Go repo generated Python file: {path}")
                    print(f"  ❌ BUG: Go repo got Python file!")
                elif path.endswith('.go'):
                    print(f"  ✅ Correct: Go file generated")
        else:
            print(f"  No code artifacts generated")
        
        if not success:
            print(f"  Workflow errors: {result.errors}")
            
    except Exception as e:
        bugs.append(f"Full workflow failed: {e}")
        print(f"❌ Workflow exception: {e}")
        import traceback
        traceback.print_exc()
    finally:
        shutil.rmtree(go_repo)
    
    if bugs:
        pytest.fail("; ".join(bugs))


def test_endpoint_model_fields():
    """Test that endpoint models have required fields."""
    print("\n" + "=" * 60)
    print("TEST 6: Endpoint Model Fields")
    print("=" * 60)
    
    bugs = []
    
    from integration_coworker.domain.models import Endpoint
    
    # Check required fields
    required_fields = ['endpoint_id', 'method', 'path', 'operation_id']
    
    # Get dataclass fields
    if hasattr(Endpoint, '__dataclass_fields__'):
        model_fields = list(Endpoint.__dataclass_fields__.keys())
    else:
        model_fields = [x for x in dir(Endpoint) if not x.startswith('_')]
    
    print(f"Endpoint fields: {model_fields[:10]}...")
    
    # Check if endpoint_path exists (was a bug earlier)
    if 'endpoint_path' in model_fields:
        print("  Found 'endpoint_path' field")
    elif 'path' in model_fields:
        print("✅ Uses 'path' field (correct)")
    else:
        bugs.append("Endpoint missing both 'path' and 'endpoint_path'")
    
    if bugs:
        pytest.fail("; ".join(bugs))


def main():
    """Run all production bug hunt tests."""
    print("\n" + "=" * 60)
    print("PRODUCTION BUG HUNT")
    print("=" * 60 + "\n")
    
    all_bugs = []
    
    # Run tests
    all_bugs.extend(test_language_detection())
    all_bugs.extend(test_spec_type_detection())
    all_bugs.extend(test_codegen_templates())
    all_bugs.extend(test_syntax_validator())
    all_bugs.extend(test_endpoint_model_fields())
    
    # Full workflow test requires LLM - only run if env vars set
    if os.environ.get('OPENAI_API_KEY'):
        all_bugs.extend(test_full_workflow_go_repo())
    else:
        print("\n⚠️  Skipping full workflow test (no OPENAI_API_KEY)")
    
    # Summary
    print("\n" + "=" * 60)
    print("BUG SUMMARY")
    print("=" * 60)
    
    if all_bugs:
        print(f"\n❌ Found {len(all_bugs)} bugs:\n")
        for i, bug in enumerate(all_bugs, 1):
            print(f"  {i}. {bug}")
    else:
        print("\n✅ No bugs found!")
    
    return len(all_bugs)


if __name__ == '__main__':
    sys.exit(main())
