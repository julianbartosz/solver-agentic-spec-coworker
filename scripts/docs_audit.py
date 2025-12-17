#!/usr/bin/env python3
"""Generate an evidence-backed docs audit deliverable.

This script is documentation-only tooling. It must not import or depend on the
application runtime.

Reproducible output:
- Inventory filesystem paths are repo-root relative and start with `docs/` (e.g., `docs/index.md`).
- MkDocs navigation paths are relative to `docs_dir` (default `docs`) (e.g., `index.md`).
- Inventory is exactly one row per physical file under docs/** with extensions
    {md,rst,txt}, plus optional generator scripts explicitly listed.
- Row ordering is deterministic.

Usage (from repo root):
    python scripts/docs_audit.py

Output:
    docs/development/docs_audit_deliverable.md
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Iterable

try:
    import yaml  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "Missing dependency 'pyyaml'. It's already used in this repo for docs tooling/tests. "
        "Install dev deps and retry.\n\nError: %s" % (e,)
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = REPO_ROOT / "docs"
MKDOCS_YML = REPO_ROOT / "mkdocs.yml"
OUTPUT_MD = DOCS_DIR / "development" / "docs_audit_deliverable.md"

INCLUDE_EXTS = {".md", ".rst", ".txt"}
IGNORE_DIRS = {
    DOCS_DIR / "decisions",
    DOCS_DIR / "design doc",
}

# Optional “explicit generator scripts” (must be clearly labeled in inventory)
EXPLICIT_EXTRA_FILES = [REPO_ROOT / "docs" / "gen_cli_reference.py"]

LINK_RE = re.compile(r"\[[^\]]+\]\(([^\)]+)\)")
H1_RE = re.compile(r"^#\s+(.+?)\s*$")
H2H3_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")


@dataclasses.dataclass(frozen=True)
class DocRow:
    fs_path: str  # repo-root relative filesystem path; starts with docs/
    nav_path: str | None  # mkdocs nav path relative to docs_dir (e.g., index.md)
    abs_path: Path
    ext: str
    bytes: int
    mtime_date: str  # YYYY-MM-DD (local filesystem time)
    h1: str
    headings_h1_h3: tuple[str, ...]
    outbound_links: tuple[str, ...]  # normalized internal doc paths only
    inbound_count: int
    out_count: int
    in_nav: bool
    flags: tuple[str, ...]


def _is_under_ignored_dir(p: Path) -> bool:
    for d in IGNORE_DIRS:
        try:
            p.relative_to(d)
            return True
        except ValueError:
            pass
    return False


def normalize_repo_rel_path(p: Path) -> str:
    """Return repo-root relative filesystem path.

    For doc files, this should naturally start with `docs/`.
    """
    rel = p.resolve().relative_to(REPO_ROOT)
    return rel.as_posix()


def normalize_nav_path(p: str) -> str:
    """Normalize a MkDocs nav path string (relative to docs_dir).

    - Strip anchors.
    - Normalize POSIX separators.
    - Ensure no leading slash.
    """
    raw = p.split("#", 1)[0].strip()
    raw = raw.lstrip("/")
    return Path(raw).as_posix()


def read_text_lossy(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="replace")


def extract_h1(text: str) -> str:
    for line in text.splitlines():
        m = H1_RE.match(line)
        if m:
            return m.group(1).strip()
    return "---"


def extract_h1_h3(text: str) -> tuple[str, ...]:
    out: list[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            m1 = H1_RE.match(line)
            if m1:
                out.append(f"# {m1.group(1).strip()}")
                continue
            m = H2H3_RE.match(line)
            if m:
                hashes, title = m.groups()
                out.append(f"{hashes} {title.strip()}")
    return tuple(out)


def parse_internal_links(src_fs_path: str, docs_dir: Path, text: str) -> tuple[str, ...]:
    """Extract internal markdown links and normalize to docs/... filesystem paths.

    Rules:
    - Only count links that resolve inside docs/ and end in .md/.rst/.txt.
    - Normalize relative paths against the source file.
    - Strip anchors.
    """
    src_abs = REPO_ROOT / src_fs_path
    src_dir = src_abs.parent

    links: list[str] = []
    for raw in LINK_RE.findall(text):
        # Skip external links
        if re.match(r"^[a-zA-Z]+://", raw):
            continue

        raw_no_anchor = raw.split("#", 1)[0].strip()
        if not raw_no_anchor:
            continue

        # Resolve relative to the source file
        cand = (src_dir / raw_no_anchor).resolve()

        # Also try resolving relative to docs_dir, since many intra-site links are
        # written as docs-root-relative paths (e.g., index.md).
        if raw_no_anchor.endswith(tuple(INCLUDE_EXTS)) and not raw_no_anchor.startswith("/"):
            docs_style = (docs_dir / raw_no_anchor).resolve()
            if docs_style.exists() and docs_style.is_file():
                cand = docs_style

        if not cand.exists() or not cand.is_file():
            continue

        if _is_under_ignored_dir(cand):
            continue

        rel = normalize_repo_rel_path(cand)
        if Path(rel).suffix.lower() not in INCLUDE_EXTS:
            continue

        links.append(rel)

    # De-dupe + deterministic
    return tuple(sorted(set(links)))


@dataclasses.dataclass(frozen=True)
class MkdocsNav:
    docs_dir: Path
    nav_paths: set[str]
    fs_paths: set[str]
    nav_to_fs: dict[str, str]


def parse_mkdocs_nav() -> MkdocsNav:
    data = yaml.safe_load(MKDOCS_YML.read_text(encoding="utf-8"))
    docs_dir_raw = data.get("docs_dir", "docs")
    docs_dir = (REPO_ROOT / str(docs_dir_raw)).resolve()
    nav = data.get("nav", [])

    nav_paths: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, str):
            nav_paths.add(normalize_nav_path(node))
            return
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, dict):
            for _, v in node.items():
                walk(v)
            return

    walk(nav)

    nav_to_fs: dict[str, str] = {}
    fs_paths: set[str] = set()
    for nav_path in sorted(nav_paths):
        fs_path = normalize_repo_rel_path((docs_dir / nav_path).resolve())
        nav_to_fs[nav_path] = fs_path
        fs_paths.add(fs_path)

    return MkdocsNav(docs_dir=docs_dir, nav_paths=nav_paths, fs_paths=fs_paths, nav_to_fs=nav_to_fs)


def iter_doc_files() -> list[Path]:
    files: list[Path] = []
    for p in DOCS_DIR.rglob("*"):
        if p.is_dir():
            continue
        if _is_under_ignored_dir(p):
            continue
        if p.suffix.lower() not in INCLUDE_EXTS:
            continue
        files.append(p)

    # Explicit extras are allowed even if not in INCLUDE_EXTS
    for extra in EXPLICIT_EXTRA_FILES:
        if extra.exists() and extra.is_file():
            files.append(extra)

    # Deterministic order by normalized path
    files_sorted = sorted(files, key=lambda x: normalize_repo_rel_path(x))
    # Ensure uniqueness by absolute path
    uniq: list[Path] = []
    seen: set[Path] = set()
    for p in files_sorted:
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        uniq.append(p)
    return uniq


def file_mtime_date(p: Path) -> str:
    ts = p.stat().st_mtime
    # Local time; stable enough for reproducibility expectations in this repo
    return dt.datetime.fromtimestamp(ts).date().isoformat()


def canonicalize_text_for_hash(text: str) -> str:
    """Return a deterministic canonical form for hashing across platforms."""
    # Normalize newlines first
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Strip trailing whitespace line-by-line
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    return text


def compute_signature_for_duplicate_detection(text: str) -> str:
    """Content-hash duplicate detector.

    Deterministic across platforms by canonicalizing newlines and stripping
    trailing whitespace.
    """
    canon = canonicalize_text_for_hash(text)
    return hashlib.sha256(canon.encode("utf-8", errors="replace")).hexdigest()


def classify_flags(fs_path: str, ext: str, bytes_: int, in_nav: bool) -> list[str]:
    flags: list[str] = []

    if fs_path.startswith("docs/history/"):
        flags.append("archive")

    if bytes_ == 0 and ext.lower() in INCLUDE_EXTS:
        flags.append("deprecated(empty)")

    if fs_path.endswith("docs/gen_cli_reference.py") or fs_path.endswith("docs/user-guide/cli-reference.md"):
        flags.append("generated")

    flags.append("authoritative" if in_nav else "supporting")

    return flags


def choose_entrypoint(paths: list[str], in_nav: dict[str, bool], inbound: dict[str, int]) -> str:
    """Pick entrypoint from within the group only."""

    def key(p: str) -> tuple[int, int, str]:
        # in_nav=yes first → sort key smaller
        nav_rank = 0 if in_nav.get(p, False) else 1
        # higher inbound first
        inbound_rank = -inbound.get(p, 0)
        return (nav_rank, inbound_rank, p)

    return sorted(paths, key=key)[0]


def choose_canonical_duplicate(paths: list[str], in_nav: dict[str, bool]) -> str:
    """Pick canonical file deterministically for duplicate-of flags.

    Priority:
    1) in_nav=yes
    2) shortest path
    3) lexicographic
    """

    def key(p: str) -> tuple[int, int, str]:
        nav_rank = 0 if in_nav.get(p, False) else 1
        return (nav_rank, len(p), p)

    return sorted(paths, key=key)[0]


def main() -> None:
    if not DOCS_DIR.exists():
        raise SystemExit(f"docs/ directory not found at {DOCS_DIR}")

    mk = parse_mkdocs_nav()
    docs_dir = mk.docs_dir

    # Stage 1: read files and compute outbound links + headings
    raw_rows: list[dict] = []
    for abs_p in iter_doc_files():
        fs_path = normalize_repo_rel_path(abs_p)
        ext = abs_p.suffix.lower()
        bytes_ = abs_p.stat().st_size
        mtime = file_mtime_date(abs_p)
        text = read_text_lossy(abs_p) if abs_p.suffix.lower() in INCLUDE_EXTS else read_text_lossy(abs_p)

        h1 = extract_h1(text) if abs_p.suffix.lower() in INCLUDE_EXTS else abs_p.name
        headings = extract_h1_h3(text) if abs_p.suffix.lower() in INCLUDE_EXTS else (f"# {abs_p.name}",)
        outbound = (
            parse_internal_links(fs_path, docs_dir=docs_dir, text=text)
            if abs_p.suffix.lower() in INCLUDE_EXTS
            else tuple()
        )

        nav_path = None
        if fs_path in mk.fs_paths:
            # Prefer stable reconstruction of nav path via mkdocs.yml mapping.
            for np, fp in mk.nav_to_fs.items():
                if fp == fs_path:
                    nav_path = np
                    break

        raw_rows.append(
            {
                "fs_path": fs_path,
                "nav_path": nav_path,
                "abs_path": abs_p,
                "ext": ext,
                "bytes": bytes_,
                "mtime_date": mtime,
                "text": text,
                "h1": h1,
                "headings": headings,
                "outbound": outbound,
                "in_nav": fs_path in mk.fs_paths,
            }
        )

    # Stage 2: inbound counts
    inbound_map: dict[str, int] = defaultdict(int)
    for r in raw_rows:
        for dst in r["outbound"]:
            inbound_map[dst] += 1

    # Stage 3: duplicate detection (content-hash based)
    sig_to_paths: dict[str, list[str]] = defaultdict(list)
    for r in raw_rows:
        if r["ext"] in INCLUDE_EXTS and r["bytes"] > 0:
            sig = compute_signature_for_duplicate_detection(r["text"])
            sig_to_paths[sig].append(r["fs_path"])

    duplicate_of: dict[str, str] = {}
    for sig, paths in sig_to_paths.items():
        if len(paths) <= 1:
            continue
        canonical = choose_canonical_duplicate(paths, in_nav={p: (p in mk.fs_paths) for p in paths})
        for p in paths:
            if p != canonical:
                duplicate_of[p] = canonical

    # Final rows
    docrows: list[DocRow] = []
    for r in raw_rows:
        fs_path = r["fs_path"]
        flags = classify_flags(fs_path, r["ext"], r["bytes"], r["in_nav"])
        if fs_path in duplicate_of:
            flags.append(f"duplicate-of:{duplicate_of[fs_path]}")

        # Deterministic ordering of flags
        flags_sorted = tuple(sorted(flags))

        dr = DocRow(
            fs_path=fs_path,
            nav_path=r["nav_path"],
            abs_path=r["abs_path"],
            ext=r["ext"],
            bytes=r["bytes"],
            mtime_date=r["mtime_date"],
            h1=r["h1"],
            headings_h1_h3=r["headings"],
            outbound_links=r["outbound"],
            inbound_count=inbound_map.get(fs_path, 0),
            out_count=len(r["outbound"]),
            in_nav=r["in_nav"],
            flags=flags_sorted,
        )
        docrows.append(dr)

    # Inventory strictness: exactly one row per physical file (unique normalized path)
    seen_paths: set[str] = set()
    uniq_rows: list[DocRow] = []
    for dr in sorted(docrows, key=lambda x: x.fs_path):
        if dr.fs_path in seen_paths:
            raise SystemExit(f"Duplicate filesystem path detected: {dr.fs_path}")
        if not dr.fs_path.startswith("docs/"):
            raise SystemExit(f"Invalid fs_path (must start with docs/): {dr.fs_path}")
        seen_paths.add(dr.fs_path)
        uniq_rows.append(dr)

    # Files of interest for overlap evidence appendix
    files_of_interest = [
        "docs/development/architecture.md",
        "docs/ARCHITECTURE.md",
        "docs/architecture_overview.md",
        "docs/development/code-tour.md",
        "docs/CODE_TOUR.md",
        "docs/GETTING_STARTED.md",
        "docs/development/testing.md",
        "docs/PROD_VALIDATION_PLAYBOOK.md",
        "docs/db_setup_postgres.md",
    ]

    # Add any detected duplicates (non-canonical) to evidence appendix
    for p in sorted(duplicate_of.keys()):
        if p not in files_of_interest:
            files_of_interest.append(p)
        canon = duplicate_of[p]
        if canon not in files_of_interest:
            files_of_interest.append(canon)

    # Also ensure every file named in overlap notes is included.
    overlap_named_files = [
        "docs/development/architecture.md",
        "docs/ARCHITECTURE.md",
        "docs/architecture_overview.md",
        "docs/development/code-tour.md",
        "docs/CODE_TOUR.md",
        "docs/GETTING_STARTED.md",
        "docs/getting-started/installation.md",
        "docs/getting-started/quickstart.md",
        "docs/getting-started/configuration.md",
        "docs/development/testing.md",
        "docs/PROD_VALIDATION_PLAYBOOK.md",
        "docs/db_setup_postgres.md",
    ]
    for p in overlap_named_files:
        if p not in files_of_interest:
            files_of_interest.append(p)

    # Helpers for conceptual groups (simple heuristic buckets)
    groups: dict[str, list[str]] = {
        "Documentation front door": ["docs/index.md"],
        "Getting Started": [
            "docs/getting-started/installation.md",
            "docs/getting-started/quickstart.md",
            "docs/getting-started/configuration.md",
        ],
        "User Guide": [
            "docs/user-guide/cli-reference.md",
            "docs/user-guide/web-ui.md",
            "docs/user-guide/specs.md",
            "docs/user-guide/workflows.md",
        ],
        "API Reference": [
            "docs/api-reference/entrypoint.md",
            "docs/api-reference/types.md",
            "docs/api-reference/workflow-state.md",
        ],
        "Features": [
            "docs/features/knowledge-graph.md",
            "docs/features/llm-cache.md",
            "docs/features/parallel-execution.md",
            "docs/features/recovery.md",
        ],
        "Development": [
            "docs/development/architecture.md",
            "docs/development/code-tour.md",
            "docs/development/contributing.md",
            "docs/development/testing.md",
            "docs/development/changelog.md",
            "docs/development/ARCHITECTURE_AUDIT_P0_REST.md",
            "docs/development/ARCHITECTURE_AUDIT_P1.md",
            "docs/development/ARCHITECTURE_AUDIT_P2.md",
            "docs/development/ARCHITECTURE_AUDIT_QUESTIONS.md",
            "docs/development/ARCHITECTURE_AUDIT_RUNTIME_DATA.md",
            "docs/development/ARCHITECTURE_REWRITE_PLAN.md",
            "docs/development/ASYNC_MIGRATION_PLAN.md",
            "docs/development/DEPRECATION_CLEANUP_AUDIT.md",
            "docs/development/PRODUCTION_SPEC_SWEEP.md",
        ],
        "Plans": [
            "docs/plans/07-llm-response-cache.md",
            "docs/plans/08-parallel-node-execution.md",
            "docs/plans/09-mkdocs-documentation.md",
        ],
        "History (Archive)": [dr.fs_path for dr in uniq_rows if dr.fs_path.startswith("docs/history/")],
    }

    # Top-level misc = in docs/ but not in nav and not in subfolders covered above
    grouped_paths = set(p for ps in groups.values() for p in ps)
    top_level_misc = [
        dr.fs_path
        for dr in uniq_rows
        if dr.fs_path.startswith("docs/")
        and not dr.fs_path.startswith(
            (
                "docs/getting-started/",
                "docs/user-guide/",
                "docs/api-reference/",
                "docs/features/",
                "docs/development/",
                "docs/plans/",
                "docs/history/",
            )
        )
        and dr.fs_path not in grouped_paths
        and dr.fs_path != "docs/index.md"
    ]
    groups["Top-level (misc/ops/audits)"] = sorted(top_level_misc)

    # Build quick maps for entrypoint selection
    in_nav_map = {dr.fs_path: dr.in_nav for dr in uniq_rows}
    inbound_map = {dr.fs_path: dr.inbound_count for dr in uniq_rows}

    # Render markdown
    OUTPUT_MD.parent.mkdir(parents=True, exist_ok=True)

    regen_cmd = "python scripts/docs_audit.py"
    header = (
        "# Docs audit deliverable (read-only)\n\n"
        "This file is generated by documentation tooling and should not be edited by hand.\n\n"
        f"**Regenerate:** `{regen_cmd}` (run from repo root)\n\n"
        "What changed / why (Dec 16, 2025):\n"
        "This deliverable is now generated reproducibly from the repo (no `/tmp`). Inventory paths are filesystem paths rooted at repo root, "
        "e.g. `docs/index.md`. MkDocs nav paths are relative to `docs_dir` (default `docs`), e.g. `index.md`. The generator enforces exactly one "
        "inventory row per physical doc file and ensures overlap claims are backed by headings evidence in the appendix.\n\n"
    )

    # Section 1: inventory
    inv_lines: list[str] = []
    inv_lines.append("## 1. Documentation inventory table\n\n")
    inv_lines.append(
        "| fs_path | nav_path | H1 | status flags | evidence (date, bytes, inbound, outbound, in_nav) |\n"
        "|---|---|---|---|\n"
    )

    for dr in uniq_rows:
        evidence = f"{dr.mtime_date}, {dr.bytes}B, in{dr.inbound_count}, out{dr.out_count}, nav:{'yes' if dr.in_nav else 'no'}"
        flags = ",".join(dr.flags) if dr.flags else "—"
        nav_path = dr.nav_path or "—"
        inv_lines.append(f"| {dr.fs_path} | {nav_path} | {dr.h1} | {flags} | {evidence} |\n")

    # Section 2: conceptual tree
    tree_lines: list[str] = []
    tree_lines.append("\n## 2. Conceptual tree of understanding\n\n")
    for group_name in sorted(groups.keys()):
        members = [p for p in groups[group_name] if p in inbound_map]
        if not members:
            continue
        entry = choose_entrypoint(members, in_nav=in_nav_map, inbound=inbound_map)
        tree_lines.append(f"- {group_name}\n")
        tree_lines.append(f"  - Entrypoint: {entry} (nav:{'yes' if in_nav_map.get(entry, False) else 'no'} in{inbound_map.get(entry, 0)})\n")
        tree_lines.append("  - Contributing files:\n")
        for p in sorted(members):
            tree_lines.append(f"    - {p}\n")
        tree_lines.append("\n")

    # Section 3: overlap notes + appendix
    overlap_lines: list[str] = []
    overlap_lines.append("\n## 3. Overlap and redundancy notes\n\n")
    overlap_lines.append(
        "The overlap notes below only reference headings that appear in the appendix (H1–H3 extracts) to keep claims evidence-backed.\n\n"
    )

    overlap_lines.append("### Architecture overlap\n")
    overlap_lines.append("- Files: `docs/development/architecture.md` vs `docs/ARCHITECTURE.md` vs `docs/architecture_overview.md`\n")

    overlap_lines.append("\n### Code tour overlap\n")
    overlap_lines.append("- Files: `docs/development/code-tour.md` vs `docs/CODE_TOUR.md`\n")

    overlap_lines.append("\n### Getting started overlap\n")
    overlap_lines.append(
        "- Files: `docs/GETTING_STARTED.md` vs `docs/getting-started/installation.md` vs `docs/getting-started/quickstart.md` vs `docs/getting-started/configuration.md`\n"
    )

    overlap_lines.append("\n### Testing overlap\n")
    overlap_lines.append("- Files: `docs/development/testing.md` vs `docs/GETTING_STARTED.md` vs `docs/PROD_VALIDATION_PLAYBOOK.md`\n")

    overlap_lines.append("\n### Postgres setup overlap\n")
    overlap_lines.append("- Files: `docs/db_setup_postgres.md` vs `docs/GETTING_STARTED.md` vs `docs/PROD_VALIDATION_PLAYBOOK.md`\n")

    overlap_lines.append("\n### Extracted headings used for evidence (H1–H3)\n\n```")

    # Appendix: include headings for every file of interest
    row_by_path = {dr.fs_path: dr for dr in uniq_rows}
    for p in files_of_interest:
        dr = row_by_path.get(p)
        if not dr:
            overlap_lines.append(f"\n=== {p} ===\n  (missing from inventory; check ignore rules)\n")
            continue
        overlap_lines.append(f"\n=== {p} ===\n")
        for h in dr.headings_h1_h3:
            overlap_lines.append(f"  {h}\n")

    overlap_lines.append("\n```\n")

    # Section 4: slim target structure (kept compact in generator)
    slim_lines: list[str] = []
    slim_lines.append("\n## 4. Proposed slim target structure + explicit mapping\n\n")
    slim_lines.append(
        "MkDocs nav is canonical. Anything not in nav should be explicitly added to nav (e.g., Operations), archived under `docs/history/**`, "
        "or removed if empty placeholders. See the implementation plan in the task for the concrete target structure.\n"
    )

    OUTPUT_MD.write_text(header + "".join(inv_lines) + "".join(tree_lines) + "".join(overlap_lines) + "".join(slim_lines), encoding="utf-8")

    print(f"Wrote {OUTPUT_MD.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
