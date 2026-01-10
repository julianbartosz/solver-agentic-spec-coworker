"""Contract tests using Schemathesis against a Prism mock server."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
import schemathesis
import requests
from schemathesis import Case
from schemathesis.core.transport import Response as ShResponse

from tests.helpers.prism import prism_server

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "specs" / "mock_payments_openapi.yaml"

# Enforce deterministic replay by default; recording only when explicitly requested
pytestmark = [
    pytest.mark.contract,
    pytest.mark.vcr("tests/contract/cassettes/test_openapi_contract/test_openapi_contract.yaml"),
    pytest.mark.no_db,
    pytest.mark.allow_hosts(["127.0.0.1", "localhost"]),
]

schema = schemathesis.openapi.from_path(str(SCHEMA_PATH))


@pytest.fixture(scope="session")
def prism_base_url():
    profile = os.getenv("VALIDATION_PROFILE", "offline").strip().lower() or "offline"
    port = int(os.getenv("PRISM_PORT", "4010"))
    # In offline mode we rely on cassette replay; no need to spawn Prism
    if profile == "offline":
        yield f"http://127.0.0.1:{port}"
        return
    with prism_server(SCHEMA_PATH, port=port) as url:
        yield url


def test_openapi_contract(prism_base_url):
    operation = schema["/ping"]["get"]
    case = Case(operation=operation, method="GET", path="/ping")
    raw_response = requests.request(method=case.method, url=f"{prism_base_url}{case.path}")
    prepared_request = requests.Request(method=case.method, url=f"{prism_base_url}{case.path}").prepare()
    sh_response = ShResponse(
        status_code=raw_response.status_code,
        headers={"content-type": [raw_response.headers.get("Content-Type", "application/json")]},
        content=raw_response.content,
        request=prepared_request,
        elapsed=0.0,
        verify=True,
    )
    operation.validate_response(sh_response, case=case)
