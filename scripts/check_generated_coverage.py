#!/usr/bin/env python
"""Validate per-module coverage for generated code.

Reads a Coverage.py JSON report and enforces a minimum coverage percentage
for files whose paths start with one or more prefixes (typically generated
artifacts). This is intended to gate generated modules separately from the
global coverage threshold.

Usage:
    python scripts/check_generated_coverage.py \
        --coverage-json coverage.json \
        --prefix packages/integration-coworker-runtime \
        --fail-under 60

Flags:
    --allow-empty       Do not fail if no files match the prefixes (default: False)
    --require-matches   Fail if no files match the prefixes (mutually exclusive with --allow-empty)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Per-module coverage gate for generated code")
    parser.add_argument("--coverage-json", required=True, help="Path to coverage JSON report")
    parser.add_argument(
        "--prefix",
        "--prefixes",
        dest="prefixes",
        action="append",
        required=True,
        help="File prefix to enforce (e.g., packages/integration-coworker-runtime). Can be passed multiple times",
    )
    parser.add_argument(
        "--fail-under",
        type=float,
        default=60.0,
        help="Minimum coverage percentage required per matched file (default: 60)",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--allow-empty",
        action="store_true",
        help="Do not fail if no files match the prefixes",
    )
    group.add_argument(
        "--require-matches",
        action="store_true",
        help="Fail if no files match the prefixes",
    )
    return parser.parse_args()


def load_coverage(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Coverage JSON not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    args = parse_args()
    coverage_data = load_coverage(Path(args.coverage_json))
    files = coverage_data.get("files", {})
    prefixes: List[str] = [Path(p).as_posix().rstrip("/") for p in args.prefixes]

    matched = []
    failing = []

    for filename, info in files.items():
        posix_name = Path(filename).as_posix()
        if any(posix_name.startswith(prefix) for prefix in prefixes):
            matched.append(posix_name)
            summary = info.get("summary", {})
            percent = summary.get("percent_covered", 0.0)
            if percent < args.fail_under:
                failing.append((posix_name, percent))

    if not matched:
        if args.require_matches:
            print(
                f"No files matched prefixes {prefixes}; use --allow-empty to skip or ensure generated modules are included.",
                file=sys.stderr,
            )
            return 1
        if not args.allow_empty:
            print(
                f"No files matched prefixes {prefixes}; skipping enforcement (use --require-matches to fail).",
                file=sys.stderr,
            )
        return 0

    if failing:
        print("Generated coverage check failed:")
        for name, percent in failing:
            print(f"  {name}: {percent:.1f}% < {args.fail_under}%", file=sys.stderr)
        return 1

    print(
        f"Generated coverage check passed for {len(matched)} file(s); min {args.fail_under}% met.",
        file=sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
