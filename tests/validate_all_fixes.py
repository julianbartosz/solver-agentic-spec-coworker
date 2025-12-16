"""
Comprehensive validation of all 5 caveat fixes.

Fix Summary:
1. LANG-001: Tree-sitter installed for 7 languages
2. REPO-001: Enhanced repo detection with Dockerfile/Makefile/GH Actions/monorepo
3. LANG-002: Language-specific few-shot examples in prompts
4. SPEC-001: Hybrid GraphQL parser (deterministic with graphql-core)
5. MULTI-001: Provider namespacing for multi-spec endpoint keys

Run: python tests/validate_all_fixes.py
"""
import sys
import tempfile
import os

def test_fix_1_tree_sitter():
    """Validate tree-sitter is installed and working for all 7 languages."""
    print("\n" + "="*60)
    print("FIX 1: Tree-sitter Installation [LANG-001]")
    print("="*60)
    
    try:
        import tree_sitter
        print(f"✅ tree-sitter version: {tree_sitter.__version__}")
    except ImportError:
        print("❌ FAILED: tree-sitter not installed")
        return False
    
    languages = []
    lang_modules = [
        ('tree_sitter_python', 'python'),
        ('tree_sitter_typescript', 'typescript'),
        ('tree_sitter_javascript', 'javascript'),
        ('tree_sitter_go', 'go'),
        ('tree_sitter_java', 'java'),
        ('tree_sitter_ruby', 'ruby'),
        ('tree_sitter_c_sharp', 'csharp'),
    ]
    
    for module_name, lang_name in lang_modules:
        try:
            module = __import__(module_name)
            languages.append(lang_name)
            print(f"  ✅ {lang_name}")
        except ImportError:
            print(f"  ❌ {lang_name}")
    
    if len(languages) == 7:
        print(f"\n✅ PASS: All 7 languages available")
        return True
    else:
        print(f"\n❌ FAIL: Only {len(languages)}/7 languages available")
        return False


def test_fix_2_repo_detection():
    """Validate enhanced repo detection heuristics."""
    print("\n" + "="*60)
    print("FIX 2: Enhanced Repo Detection [REPO-001]")
    print("="*60)
    
    from integration_coworker.repo.detection import detect_repo_language
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Test 1: Dockerfile detection
        dockerfile_repo = os.path.join(tmpdir, "dockerfile_repo")
        os.makedirs(dockerfile_repo)
        with open(os.path.join(dockerfile_repo, "Dockerfile"), "w") as f:
            f.write("FROM golang:1.21\nCOPY . .\nRUN go build")
        
        result = detect_repo_language(dockerfile_repo)
        dockerfile_pass = result.get("language") == "go"
        print(f"  {'✅' if dockerfile_pass else '❌'} Dockerfile detection: {result.get('language')}")
        
        # Test 2: GitHub Actions detection
        actions_repo = os.path.join(tmpdir, "actions_repo")
        os.makedirs(os.path.join(actions_repo, ".github", "workflows"), exist_ok=True)
        with open(os.path.join(actions_repo, ".github", "workflows", "ci.yml"), "w") as f:
            f.write("name: CI\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/setup-python@v4")
        
        result = detect_repo_language(actions_repo)
        actions_pass = result.get("language") == "python"
        print(f"  {'✅' if actions_pass else '❌'} GitHub Actions detection: {result.get('language')}")
        
        # Test 3: Full Python stack with config
        python_repo = os.path.join(tmpdir, "python_repo")
        os.makedirs(python_repo)
        with open(os.path.join(python_repo, "pyproject.toml"), "w") as f:
            f.write("[project]\nname = 'test'\n")
        os.makedirs(os.path.join(python_repo, "src"))
        with open(os.path.join(python_repo, "src", "main.py"), "w") as f:
            f.write("print('hello')")
        
        result = detect_repo_language(python_repo)
        confidence = result.get("confidence", 0)
        python_pass = result.get("language") == "python" and confidence >= 0.8
        print(f"  {'✅' if python_pass else '❌'} Python with config: {result.get('language')} (conf={confidence:.2f})")
        
        all_pass = dockerfile_pass and actions_pass and python_pass
        print(f"\n{'✅ PASS' if all_pass else '❌ FAIL'}: Repo detection heuristics")
        return all_pass


def test_fix_3_language_examples():
    """Validate language-specific few-shot examples in prompts."""
    print("\n" + "="*60)
    print("FIX 3: Language-Specific Few-Shot Examples [LANG-002]")
    print("="*60)
    
    from integration_coworker.codegen.prompts import LANGUAGE_EXAMPLES, get_language_example
    
    required_languages = ["python", "go", "java", "typescript", "ruby", "csharp"]
    all_pass = True
    
    for lang in required_languages:
        example = get_language_example(lang)
        has_example = example and len(example) > 50
        
        # Check for idiomatic patterns
        idiomatic_patterns = {
            "python": "async def",
            "go": "func ",
            "java": "public class",
            "typescript": "async ",
            "ruby": "def ",
            "csharp": "async Task",
        }
        
        has_idiom = idiomatic_patterns[lang] in example if has_example else False
        passes = has_example and has_idiom
        
        if not passes:
            all_pass = False
        
        print(f"  {'✅' if passes else '❌'} {lang}: example={has_example}, idiomatic={has_idiom}")
    
    print(f"\n{'✅ PASS' if all_pass else '❌ FAIL'}: Language-specific examples")
    return all_pass


def test_fix_4_graphql_parser():
    """Validate hybrid GraphQL parser."""
    print("\n" + "="*60)
    print("FIX 4: Hybrid GraphQL Parser [SPEC-001]")
    print("="*60)
    
    from integration_coworker.graph.nodes.detect_and_parse_spec import _parse_graphql_to_pseudo_openapi
    
    schema = '''
type Query {
  users(limit: Int): [User!]!
  user(id: ID!): User
}

type Mutation {
  createUser(name: String!, email: String!): User!
}

type User {
  id: ID!
  name: String!
  email: String!
}

enum Status {
  ACTIVE
  INACTIVE
}
'''
    
    result = _parse_graphql_to_pseudo_openapi(schema, "test://schema")
    
    if result is None:
        print("  ❌ Parser returned None")
        return False
    
    # Check parse method
    parse_method = result.get("_parse_method", "unknown")
    is_deterministic = parse_method == "deterministic"
    print(f"  {'✅' if is_deterministic else '⚠️'} Parse method: {parse_method}")
    
    # Check paths extracted
    paths = result.get("paths", {})
    has_query_paths = any("/graphql/query/" in p for p in paths)
    has_mutation_paths = any("/graphql/mutation/" in p for p in paths)
    print(f"  {'✅' if has_query_paths else '❌'} Query paths extracted: {sum(1 for p in paths if '/query/' in p)}")
    print(f"  {'✅' if has_mutation_paths else '❌'} Mutation paths extracted: {sum(1 for p in paths if '/mutation/' in p)}")
    
    # Check schemas extracted
    schemas = result.get("components", {}).get("schemas", {})
    has_user = "User" in schemas
    has_status = "Status" in schemas
    print(f"  {'✅' if has_user else '❌'} User schema extracted")
    print(f"  {'✅' if has_status else '❌'} Status enum extracted")
    
    # Check reproducibility
    result2 = _parse_graphql_to_pseudo_openapi(schema, "test://schema")
    is_reproducible = set(result.get("paths", {}).keys()) == set(result2.get("paths", {}).keys())
    print(f"  {'✅' if is_reproducible else '❌'} Reproducible output")
    
    all_pass = is_deterministic and has_query_paths and has_mutation_paths and has_user and has_status and is_reproducible
    print(f"\n{'✅ PASS' if all_pass else '❌ FAIL'}: GraphQL parser")
    return all_pass


def test_fix_5_provider_namespacing():
    """Validate provider namespacing for multi-spec."""
    print("\n" + "="*60)
    print("FIX 5: Provider Namespacing [MULTI-001]")
    print("="*60)
    
    from integration_coworker.graph.nodes.persist_results import get_qualified_endpoint_key
    
    # Test 1: Key includes provider
    key = get_qualified_endpoint_key("POST", "/payments", "create", "stripe")
    has_provider = len(key) == 4 and key[0] == "stripe"
    print(f"  {'✅' if has_provider else '❌'} Key includes provider namespace")
    
    # Test 2: Different providers = different keys
    stripe_key = get_qualified_endpoint_key("POST", "/payments", "create", "stripe")
    paypal_key = get_qualified_endpoint_key("POST", "/payments", "create", "paypal")
    are_distinct = stripe_key != paypal_key
    print(f"  {'✅' if are_distinct else '❌'} Same path, different providers = distinct keys")
    
    # Test 3: Normalization
    key1 = get_qualified_endpoint_key("post", "/payments", None, "Stripe")
    key2 = get_qualified_endpoint_key("POST", "/payments", None, "stripe")
    normalized = key1 == key2
    print(f"  {'✅' if normalized else '❌'} Provider and method normalization")
    
    # Test 4: Dict uniqueness
    endpoint_map = {}
    endpoint_map[get_qualified_endpoint_key("POST", "/charge", "x", "stripe")] = 1
    endpoint_map[get_qualified_endpoint_key("POST", "/charge", "x", "paypal")] = 2
    dict_works = len(endpoint_map) == 2
    print(f"  {'✅' if dict_works else '❌'} Dict key uniqueness")
    
    all_pass = has_provider and are_distinct and normalized and dict_works
    print(f"\n{'✅ PASS' if all_pass else '❌ FAIL'}: Provider namespacing")
    return all_pass


def main():
    print("\n" + "="*70)
    print("COMPREHENSIVE VALIDATION OF ALL CAVEAT FIXES")
    print("="*70)
    
    results = {
        "LANG-001 (Tree-sitter)": test_fix_1_tree_sitter(),
        "REPO-001 (Repo Detection)": test_fix_2_repo_detection(),
        "LANG-002 (Few-shot Examples)": test_fix_3_language_examples(),
        "SPEC-001 (GraphQL Parser)": test_fix_4_graphql_parser(),
        "MULTI-001 (Provider Namespacing)": test_fix_5_provider_namespacing(),
    }
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    all_passed = True
    for fix_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}: {fix_name}")
        if not passed:
            all_passed = False
    
    print("\n" + "="*70)
    if all_passed:
        print("🎉 ALL 5 FIXES VALIDATED SUCCESSFULLY")
    else:
        print("⚠️  SOME FIXES NEED ATTENTION")
    print("="*70 + "\n")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
