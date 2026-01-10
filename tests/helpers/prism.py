"""Prism mock server helper for contract tests."""
from __future__ import annotations

import os
import socket
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest


def _is_port_open(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


@contextmanager
def prism_server(spec_path: Path, port: int = 4010) -> Iterator[str]:
    """Start a Prism mock server for the given OpenAPI spec."""
    if not spec_path.exists():
        raise FileNotFoundError(f"Spec not found: {spec_path}")

    if not subprocess.run(["which", "prism"], capture_output=True).returncode == 0:
        pytest.skip("Prism CLI is required: npm install -g @stoplight/prism-cli")

    env = os.environ.copy()
    cmd = ["prism", "mock", str(spec_path), "-p", str(port)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    try:
        deadline = time.time() + 15
        while time.time() < deadline:
            if _is_port_open("127.0.0.1", port):
                break
            time.sleep(0.2)
        else:
            stderr = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
            proc.kill()
            raise RuntimeError(f"Prism failed to start: {stderr}")

        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

        if proc.stderr:
            proc.stderr.close()
        if proc.stdout:
            proc.stdout.close()
