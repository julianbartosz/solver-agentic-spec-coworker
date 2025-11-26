#!/usr/bin/env python3
"""Quick script to verify LangSmith tracing is working."""

import os
import sys
from datetime import datetime

# Verify environment variables
print("=" * 60)
print("LangSmith Environment Variables Check")
print("=" * 60)
print(f"LANGCHAIN_TRACING_V2: {os.getenv('LANGCHAIN_TRACING_V2', 'NOT SET')}")
print(f"LANGCHAIN_PROJECT: {os.getenv('LANGCHAIN_PROJECT', 'NOT SET')}")
print(f"LANGCHAIN_API_KEY: {'SET' if os.getenv('LANGCHAIN_API_KEY') else 'NOT SET'}")
print(f"LANGCHAIN_ENDPOINT: {os.getenv('LANGCHAIN_ENDPOINT', 'NOT SET')}")
print()

# Test if langsmith client can initialize
try:
    from langsmith import Client
    client = Client()
    print(f"✓ LangSmith client initialized successfully")
    print(f"  API URL: {client.api_url}")
    print()
except Exception as e:
    print(f"✗ Failed to initialize LangSmith client: {e}")
    print()

# Run a simple LangGraph workflow with tracing
print("Running simple LangGraph workflow...")
print(f"Timestamp: {datetime.now().isoformat()}")
print()

from langgraph.graph import StateGraph, END
from typing import TypedDict

class SimpleState(TypedDict):
    count: int

def increment(state: SimpleState) -> SimpleState:
    """Simple increment function."""
    return {"count": state["count"] + 1}

# Build and run workflow
workflow = StateGraph(SimpleState)
workflow.add_node("increment", increment)
workflow.set_entry_point("increment")
workflow.add_edge("increment", END)
app = workflow.compile()

result = app.invoke({"count": 0})
print(f"✓ Workflow executed: {result}")
print()
print("Check your LangSmith project for the trace!")
print(f"Project: {os.getenv('LANGCHAIN_PROJECT', 'default')}")
print("URL: https://smith.langchain.com/public/7b13eeb1-e1b6-4186-8b89-2b54d8e0f370/r")
