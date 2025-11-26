#!/usr/bin/env python3
"""
Inspect LangSmith traces for the integration coworker project.
"""
import os
from datetime import datetime, timedelta
from langsmith import Client

def main():
    # Initialize client
    client = Client()
    project_name = os.getenv('LANGCHAIN_PROJECT', 'pr-mundane-creche-14')
    
    print(f"📊 Inspecting LangSmith Project: {project_name}")
    print(f"{'='*80}\n")
    
    # Get recent runs (last 2 hours)
    start_time = datetime.now() - timedelta(hours=2)
    
    print("🔍 Fetching 5 most recent runs...")
    runs = list(client.list_runs(
        project_name=project_name,
        start_time=start_time,
        limit=5
    ))
    
    if not runs:
        print("❌ No runs found in the last 2 hours")
        return
    
    print(f"✅ Found {len(runs)} recent runs\n")
    
    # Find the most recent LangGraph run
    langgraph_runs = [r for r in runs if r.name == "LangGraph"]
    
    if not langgraph_runs:
        print("⚠️  No LangGraph runs found. Showing all runs:")
        for i, run in enumerate(runs, 1):
            print(f"{i}. {run.name} ({run.run_type}) - {run.status}")
            print(f"   ID: {run.id}")
            print(f"   Start: {run.start_time}")
            print(f"   End: {run.end_time}")
            print()
        return
    
    # Get the latest LangGraph run
    latest_run = langgraph_runs[0]
    
    print("📦 Latest LangGraph Run Details")
    print(f"{'='*80}")
    print(f"Run ID:      {latest_run.id}")
    print(f"Name:        {latest_run.name}")
    print(f"Status:      {latest_run.status}")
    print(f"Run Type:    {latest_run.run_type}")
    print(f"Start Time:  {latest_run.start_time}")
    print(f"End Time:    {latest_run.end_time}")
    if latest_run.end_time and latest_run.start_time:
        duration = (latest_run.end_time - latest_run.start_time).total_seconds()
        print(f"Duration:    {duration:.2f}s")
    print()
    
    # Get child runs (graph nodes)
    print("🌳 Child Runs (Graph Nodes)")
    print(f"{'='*80}")
    
    child_runs = list(client.list_runs(
        project_name=project_name,
        filter=f'eq(parent_run_id, "{latest_run.id}")',
        limit=50
    ))
    
    if not child_runs:
        print("⚠️  No child runs found")
        return
    
    print(f"Found {len(child_runs)} child runs:\n")
    
    # Group by status
    successful = []
    failed = []
    other = []
    
    for i, child in enumerate(child_runs, 1):
        status_emoji = "✅" if child.status == "success" else "❌" if child.status == "error" else "⏸️"
        
        print(f"{i:2d}. {status_emoji} {child.name}")
        print(f"     Run Type: {child.run_type}")
        print(f"     Status: {child.status}")
        print(f"     ID: {child.id}")
        if child.start_time:
            print(f"     Start: {child.start_time.strftime('%H:%M:%S.%f')[:-3]}")
        if child.end_time and child.start_time:
            duration_ms = (child.end_time - child.start_time).total_seconds() * 1000
            print(f"     Duration: {duration_ms:.1f}ms")
        
        # Check for errors in outputs
        if child.error:
            print(f"     ⚠️  Error: {child.error}")
        
        print()
        
        if child.status == "success":
            successful.append(child.name)
        elif child.status == "error":
            failed.append(child.name)
        else:
            other.append(child.name)
    
    # Summary
    print(f"{'='*80}")
    print("📊 Summary")
    print(f"{'='*80}")
    print(f"Total child runs: {len(child_runs)}")
    print(f"✅ Successful: {len(successful)}")
    print(f"❌ Failed: {len(failed)}")
    print(f"⏸️  Other: {len(other)}")
    print()
    
    if successful:
        print("Successful nodes:")
        for name in successful:
            print(f"  • {name}")
        print()
    
    if failed:
        print("Failed nodes:")
        for name in failed:
            print(f"  • {name}")
        print()

if __name__ == "__main__":
    main()
