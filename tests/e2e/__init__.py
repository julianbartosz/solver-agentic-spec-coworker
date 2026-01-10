"""
End-to-end tests for real repository generated code integration.

These tests clone real repositories, generate code using the full pipeline,
and validate with Docker-based gates. They require:
- Docker daemon running
- Network access for cloning (provision phase)
- Optional: LLM API keys for live tests

Run with:
    pytest tests/e2e/ -m e2e          # All E2E tests
    pytest tests/e2e/ -m "e2e and docker"  # Docker-based only
    pytest tests/e2e/ -m "e2e and live_llm"  # Tests that call real LLM
"""
