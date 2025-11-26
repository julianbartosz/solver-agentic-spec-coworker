"""Quick visualization of the LangGraph structure."""

def visualize_graph():
    print("="*80)
    print("INTEGRATION COWORKER LANGGRAPH STRUCTURE")
    print("="*80)
    print()
    
    # Linear nodes (always execute)
    linear_nodes = [
        ("1", "plan_run", "Initialize run plan & state"),
        ("2", "ingest_spec", "Load spec file(s)"),
        ("3", "detect_and_parse_spec", "Parse OpenAPI/AsyncAPI"),
        ("4", "build_silver_api_model", "Extract endpoints, schemas, entities"),
        ("5", "embed_spec_chunks", "Create vector embeddings (1536-dim)"),
        ("6", "understand_task", "Interpret natural language task"),
        ("7", "align_task_with_kg", "Match task to API operations"),
        ("8", "plan_integration_flow", "Design workflow graph"),
        ("9", "attach_policies_and_patterns", "Add auth, retry, rate-limit policies"),
        ("10", "generate_code_and_tests", "Generate client, flow, test code"),
    ]
    
    print("📋 LINEAR FLOW (Always Executes)")
    print("-" * 80)
    for num, name, desc in linear_nodes:
        print(f"{num:>2}. {name:35s} → {desc}")
    
    print()
    print("🔀 CONDITIONAL BRANCH #1: Repo Integration")
    print("-" * 80)
    print("    Decision: state.plan.get('use_repo', False)")
    print()
    print("    Path A (with_repo):")
    print("    11a. attach_repo_context           → Load repo structure")
    print("    12a. analyze_repo_layout           → Detect framework")
    print("    13a. apply_repo_integration_changes → Wire code into files")
    print()
    print("    Path B (without_repo):")
    print("         [Skip directly to validation]")
    print()
    print("    [Both paths merge at validate_integration_design]")
    
    print()
    print("🔀 CONDITIONAL BRANCH #2: Error Handling")
    print("-" * 80)
    print("    14. validate_integration_design   → Check workflow integrity")
    print()
    print("    Decision: state.errors and not state.plan.get('failed', False)")
    print()
    print("    Path A (has_errors):")
    print("    15a. handle_error                  → Log errors, update state")
    print()
    print("    Path B (no_errors):")
    print("         [Skip directly to persist]")
    print()
    print("    [Both paths merge at persist_results]")
    
    print()
    print("📋 FINAL STEPS (Always Execute)")
    print("-" * 80)
    print("16. persist_results                → Save to Postgres (unless dry_run)")
    print("17. build_report                   → Generate markdown report")
    print()
    print("="*80)
    print("TOTAL NODES: 17")
    print("DRY-RUN (no repo): 14 nodes execute (skip 11a-13a)")
    print("WITH REPO: 17 nodes execute (all)")
    print("="*80)

if __name__ == "__main__":
    visualize_graph()
