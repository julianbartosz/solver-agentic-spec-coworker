#!/usr/bin/env python3
"""
Inspect specific node details from LangSmith traces.
"""
import os
from datetime import datetime, timedelta
from langsmith import Client

def main():
    client = Client()
    project_name = os.getenv('LANGCHAIN_PROJECT', 'pr-mundane-creche-14')
    
    # Find the most recent root LangGraph run
    runs = list(client.list_runs(
        project_name=project_name,
        start_time=datetime.now() - timedelta(hours=2),
        limit=50
    ))
    
    root_runs = [r for r in runs if not r.parent_run_id and r.name == 'LangGraph']
    if not root_runs:
        print('❌ No root LangGraph runs found')
        return
    
    latest = root_runs[0]
    print(f"Inspecting run: {latest.id}")
    print(f"Started at: {latest.start_time}\n")
    
    # Target nodes to inspect
    target_nodes = [
        'plan_run',
        'ingest_spec', 
        'build_silver_api_model',
        'plan_integration_flow',
        'generate_code_and_tests',
        'validate_integration_design',
        'persist_results',
        'build_report'
    ]
    
    # Get all child runs
    children = list(client.list_runs(
        project_name=project_name,
        filter=f'eq(parent_run_id, "{latest.id}")',
        limit=100
    ))
    
    print("="*80)
    print("🔍 Detailed Node Inspection")
    print("="*80)
    print()
    
    for node_name in target_nodes:
        # Find this node in children
        node_runs = [c for c in children if c.name == node_name]
        if not node_runs:
            print(f"⚠️  Node '{node_name}' not found in trace")
            print()
            continue
        
        node = node_runs[0]
        
        print(f"{'='*80}")
        print(f"📌 Node: {node_name}")
        print(f"{'='*80}")
        print(f"Status:      {node.status}")
        print(f"Run Type:    {node.run_type}")
        print(f"Run ID:      {node.id}")
        
        if node.start_time:
            print(f"Start Time:  {node.start_time.strftime('%H:%M:%S.%f')[:-3]}")
        if node.end_time and node.start_time:
            duration_ms = (node.end_time - node.start_time).total_seconds() * 1000
            print(f"Duration:    {duration_ms:.2f}ms")
        
        # Check for errors
        if node.error:
            print(f"\n⚠️  ERROR:")
            print(f"   {node.error}")
        
        # Check inputs (limited to avoid too much data)
        if node.inputs:
            print(f"\n📥 Inputs:")
            for key, value in list(node.inputs.items())[:3]:
                value_str = str(value)[:100]
                if len(str(value)) > 100:
                    value_str += "..."
                print(f"   {key}: {value_str}")
        
        # Check outputs (limited)
        if node.outputs:
            print(f"\n📤 Outputs:")
            for key, value in list(node.outputs.items())[:3]:
                value_str = str(value)[:100]
                if len(str(value)) > 100:
                    value_str += "..."
                print(f"   {key}: {value_str}")
        
        # Check metadata
        if hasattr(node, 'extra') and node.extra:
            print(f"\n🏷️  Metadata:")
            for key, value in list(node.extra.items())[:5]:
                print(f"   {key}: {value}")
        
        print()
    
    print("="*80)
    print("✅ Inspection Complete")
    print("="*80)

if __name__ == "__main__":
    main()
