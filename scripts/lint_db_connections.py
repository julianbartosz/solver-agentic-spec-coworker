#!/usr/bin/env python3
"""
Database Connection Lint Script
===============================

Checks for improper database connection usage patterns that can lead to
connection leaks and "ConnectionWrapper was garbage collected" warnings.

This script enforces that:
1. get_connection() should be used with `with` statement
2. Cursors should be properly closed
3. The SQLite pattern `conn = db.get_connection(); try: ... finally: conn.close()`
   is acceptable but `with` is preferred

Run: python scripts/lint_db_connections.py
Run in CI: python scripts/lint_db_connections.py --strict

Per PRODUCTION_AUDIT_DEC12.md - Recommendation #3.
"""

import ast
import sys
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Iterator


@dataclass
class LintViolation:
    file: str
    line: int
    column: int
    message: str
    severity: str  # "error" or "warning"


class ConnectionPatternChecker(ast.NodeVisitor):
    """AST visitor to detect improper get_connection() usage."""
    
    def __init__(self, filename: str):
        self.filename = filename
        self.violations: list[LintViolation] = []
        self._in_with_statement = False
        self._in_try_finally = False
        self._assigned_connections: dict[str, int] = {}  # var_name -> line
    
    def visit_With(self, node: ast.With) -> None:
        """Track that we're inside a `with` statement."""
        old_in_with = self._in_with_statement
        self._in_with_statement = True
        self.generic_visit(node)
        self._in_with_statement = old_in_with
    
    def visit_Try(self, node: ast.Try) -> None:
        """Track try/finally blocks (acceptable pattern for SQLite)."""
        has_finally = len(node.finalbody) > 0
        old_in_try = self._in_try_finally
        self._in_try_finally = has_finally
        self.generic_visit(node)
        self._in_try_finally = old_in_try
    
    def visit_Assign(self, node: ast.Assign) -> None:
        """Detect assignments like `conn = get_connection()` outside `with`."""
        # Check if right side is a call to get_connection
        if isinstance(node.value, ast.Call):
            func = node.value.func
            func_name = None
            
            if isinstance(func, ast.Name):
                func_name = func.id
            elif isinstance(func, ast.Attribute):
                func_name = func.attr
            
            if func_name == "get_connection":
                # Record this assignment for later checking
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if not self._in_with_statement:
                            self._assigned_connections[target.id] = node.lineno
                            
                            # Check if we're NOT in try/finally (warning only)
                            if not self._in_try_finally:
                                self.violations.append(LintViolation(
                                    file=self.filename,
                                    line=node.lineno,
                                    column=node.col_offset,
                                    message=(
                                        f"get_connection() assigned to '{target.id}' outside `with` statement. "
                                        f"Use `with get_connection() as {target.id}:` for proper cleanup."
                                    ),
                                    severity="warning"
                                ))
        
        self.generic_visit(node)
    
    def visit_Call(self, node: ast.Call) -> None:
        """Check for cursor() calls that might leak."""
        func = node.func
        
        if isinstance(func, ast.Attribute) and func.attr == "cursor":
            # Check if this cursor call is inside a `with` context
            if not self._in_with_statement and not self._in_try_finally:
                self.violations.append(LintViolation(
                    file=self.filename,
                    line=node.lineno,
                    column=node.col_offset,
                    message=(
                        "cursor() called outside `with` statement. "
                        "Use `with conn.cursor() as cur:` or ensure cursor is closed in finally block."
                    ),
                    severity="warning"
                ))
        
        self.generic_visit(node)


def check_file(filepath: Path) -> list[LintViolation]:
    """Check a single Python file for connection pattern violations."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source, filename=str(filepath))
        checker = ConnectionPatternChecker(str(filepath))
        checker.visit(tree)
        return checker.violations
    
    except SyntaxError as e:
        return [LintViolation(
            file=str(filepath),
            line=e.lineno or 0,
            column=e.offset or 0,
            message=f"Syntax error: {e.msg}",
            severity="error"
        )]
    except Exception as e:
        return [LintViolation(
            file=str(filepath),
            line=0,
            column=0,
            message=f"Failed to parse: {e}",
            severity="error"
        )]


def find_python_files(root: Path, exclude_patterns: list[str] = None) -> Iterator[Path]:
    """Find all Python files under root, excluding certain patterns."""
    exclude_patterns = exclude_patterns or []
    
    for pyfile in root.rglob("*.py"):
        # Skip common exclude patterns
        path_str = str(pyfile)
        if any(pattern in path_str for pattern in exclude_patterns):
            continue
        yield pyfile


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Lint database connection patterns")
    parser.add_argument(
        "--strict", 
        action="store_true",
        help="Exit with error code if any warnings found"
    )
    parser.add_argument(
        "--fix-suggestions",
        action="store_true",
        help="Show suggested fixes"
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["src"],
        help="Paths to check (default: src)"
    )
    args = parser.parse_args()
    
    # Exclude patterns
    exclude = [
        "__pycache__",
        ".venv",
        ".git",
        "test_",  # Don't lint test files (they may intentionally test bad patterns)
        "conftest.py",
    ]
    
    all_violations: list[LintViolation] = []
    files_checked = 0
    
    for path_str in args.paths:
        path = Path(path_str)
        if path.is_file():
            files_checked += 1
            all_violations.extend(check_file(path))
        elif path.is_dir():
            for pyfile in find_python_files(path, exclude):
                files_checked += 1
                all_violations.extend(check_file(pyfile))
    
    # Print results
    errors = [v for v in all_violations if v.severity == "error"]
    warnings = [v for v in all_violations if v.severity == "warning"]
    
    if all_violations:
        print(f"\n{'='*60}")
        print(f"Database Connection Lint Results")
        print(f"{'='*60}\n")
        
        for v in sorted(all_violations, key=lambda x: (x.file, x.line)):
            icon = "❌" if v.severity == "error" else "⚠️"
            print(f"{icon} {v.file}:{v.line}:{v.column}")
            print(f"   {v.message}")
            
            if args.fix_suggestions:
                print("   Suggested fix:")
                print("   -  conn = get_connection()")
                print("   +  with get_connection() as conn:")
            print()
        
        print(f"{'='*60}")
        print(f"Files checked: {files_checked}")
        print(f"Errors: {len(errors)}")
        print(f"Warnings: {len(warnings)}")
        print(f"{'='*60}")
    else:
        print(f"✅ No connection pattern violations found in {files_checked} files")
    
    # Exit code
    if errors:
        sys.exit(1)
    elif warnings and args.strict:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
