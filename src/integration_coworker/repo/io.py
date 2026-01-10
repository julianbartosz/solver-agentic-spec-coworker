"""
Canonical Repo IO Surface - Single point for all repository file operations.

P1 Security + Audit + Determinism:
- All repo file operations MUST go through this module
- Enforces path policy (allow/deny, symlink policy)
- Enforces size limits (configurable)
- Provides audit logging with per-run counters
- Future: single point to add storage backends or sandbox rules

Usage:
    from integration_coworker.repo.io import RepoIO, get_repo_io
    
    # Create IO context for a run
    io = RepoIO(repo_root="/path/to/repo", run_id="run_123")
    
    # Safe operations
    content = io.read_text("src/main.py")
    io.write_text("src/generated.py", "# Generated code")
    
    # Get audit summary
    summary = io.get_audit_summary()
"""
import fnmatch
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterator,
    List,
    Literal,
    Optional,
    Set,
    Tuple,
    Union,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class RepoIOConfig:
    """
    Configuration for repo IO operations.
    
    All limits are configurable and can be overridden per-run.
    """
    # Size limits
    max_read_size_bytes: int = 10 * 1024 * 1024  # 10MB default
    max_write_size_bytes: int = 1 * 1024 * 1024  # 1MB default per file
    max_total_write_bytes: int = 50 * 1024 * 1024  # 50MB total per run
    max_files_written: int = 100  # Max files created/modified per run
    
    # Path policy
    allow_dotfiles: bool = False  # Allow reading/writing .dotfiles
    allow_hidden_dirs: bool = False  # Allow traversing hidden directories
    
    # Symlink policy
    symlink_policy: Literal["deny", "follow_internal", "follow_all"] = "follow_internal"
    # deny: Never follow symlinks
    # follow_internal: Follow symlinks only if target is inside repo root
    # follow_all: Follow all symlinks (dangerous, not recommended)
    
    # Allowlist roots (relative to repo root)
    # Empty list means entire repo is allowed
    allowlist_roots: List[str] = field(default_factory=list)
    
    # Denylist patterns (glob patterns)
    denylist_patterns: List[str] = field(default_factory=lambda: [
        ".git/**",
        ".git",
        "**/.git/**",
        "node_modules/**",
        "__pycache__/**",
        "*.pyc",
        ".env",
        ".env.*",
        "**/*.key",
        "**/*.pem",
        "**/secrets/**",
        "**/credentials/**",
    ])
    
    # Audit settings
    audit_reads: bool = True
    audit_writes: bool = True
    audit_to_log: bool = True


def get_default_config() -> RepoIOConfig:
    """Get default repo IO configuration, potentially from settings."""
    try:
        from integration_coworker.config import get_settings
        settings = get_settings()
        
        # Allow config override via settings if present
        config = RepoIOConfig()
        
        # Future: read from settings.repo_io_* if we add those fields
        # For now, use defaults
        
        return config
    except ImportError:
        return RepoIOConfig()


# =============================================================================
# Exceptions
# =============================================================================

class RepoIOError(Exception):
    """Base exception for repo IO errors."""
    pass


class PathPolicyViolation(RepoIOError):
    """Raised when a path violates security policy."""
    def __init__(self, path: str, reason: str, run_id: Optional[str] = None):
        self.path = path
        self.reason = reason
        self.run_id = run_id
        super().__init__(f"Path policy violation for '{path}': {reason}")


class SizeLimitExceeded(RepoIOError):
    """Raised when a size limit is exceeded."""
    def __init__(self, limit_type: str, actual: int, limit: int, run_id: Optional[str] = None):
        self.limit_type = limit_type
        self.actual = actual
        self.limit = limit
        self.run_id = run_id
        super().__init__(f"{limit_type} exceeded: {actual} > {limit}")


class SymlinkPolicyViolation(RepoIOError):
    """Raised when a symlink violates policy."""
    def __init__(self, path: str, target: str, run_id: Optional[str] = None):
        self.path = path
        self.target = target
        self.run_id = run_id
        super().__init__(f"Symlink policy violation: '{path}' -> '{target}'")


class WorkspaceBoundaryViolation(RepoIOError):
    """
    G-02: Raised when a path violates workspace boundaries.
    
    In monorepos, writes must be constrained to the selected workspace root.
    This prevents cross-workspace contamination and accidental writes to
    unrelated packages.
    """
    def __init__(
        self,
        path: str,
        workspace_root: str,
        run_id: Optional[str] = None,
    ):
        self.path = path
        self.workspace_root = workspace_root
        self.run_id = run_id
        super().__init__(
            f"Workspace boundary violation: '{path}' is outside workspace root '{workspace_root}'. "
            f"All writes must be within the selected workspace."
        )


# =============================================================================
# Audit Record
# =============================================================================

@dataclass
class AuditRecord:
    """Record of a single IO operation."""
    operation: str  # "read", "write", "list", "glob", "exists", "stat"
    path: str
    success: bool
    bytes_transferred: int = 0
    error: Optional[str] = None
    timestamp: float = field(default_factory=lambda: __import__("time").time())


@dataclass  
class AuditSummary:
    """Summary of IO operations for a run."""
    run_id: str
    repo_root: str
    bytes_read: int = 0
    bytes_written: int = 0
    files_read: int = 0
    files_written: int = 0
    files_created: int = 0
    operations_count: int = 0
    policy_violations: int = 0
    errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "repo_root": self.repo_root,
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
            "files_read": self.files_read,
            "files_written": self.files_written,
            "files_created": self.files_created,
            "operations_count": self.operations_count,
            "policy_violations": self.policy_violations,
            "errors": self.errors,
        }


# =============================================================================
# Core RepoIO Class
# =============================================================================

class RepoIO:
    """
    Canonical interface for all repository file operations.
    
    Thread-safe, audited, policy-enforcing IO layer.
    All graph nodes MUST use this for repo file access.
    """
    
    def __init__(
        self,
        repo_root: Union[str, Path],
        run_id: str,
        config: Optional[RepoIOConfig] = None,
        workspace_root: Optional[str] = None,
    ):
        """
        Initialize RepoIO context.
        
        Args:
            repo_root: Absolute path to repository root
            run_id: Run ID for audit tracking
            config: Optional configuration override
            workspace_root: G-02: Optional workspace root for monorepo boundary enforcement.
                           If set, all writes must be within this workspace.
                           Example: "apps/api" or "packages/backend"
        """
        self.repo_root = Path(repo_root).resolve()
        self.run_id = run_id
        self.config = config or get_default_config()
        
        # G-02: Workspace boundary enforcement
        self.workspace_root = workspace_root  # Relative path within repo
        if workspace_root:
            self._workspace_abs = (self.repo_root / workspace_root).resolve()
            # Validate workspace root exists
            if not self._workspace_abs.exists():
                logger.warning(f"Workspace root does not exist: {self._workspace_abs}")
        else:
            self._workspace_abs = None
        
        # Audit state (thread-safe)
        self._lock = threading.Lock()
        self._audit_records: List[AuditRecord] = []
        self._bytes_read = 0
        self._bytes_written = 0
        self._files_read: Set[str] = set()
        self._files_written: Set[str] = set()
        self._files_created: Set[str] = set()
        self._policy_violations = 0
        
        logger.debug(f"RepoIO initialized: root={self.repo_root}, run_id={run_id}, workspace={workspace_root}")
    
    # =========================================================================
    # Path Validation
    # =========================================================================
    
    def _normalize_path(self, path: Union[str, Path]) -> Path:
        """
        Normalize a path relative to repo root.
        
        Handles:
        - Relative paths (resolved against repo_root)
        - Absolute paths (must be within repo_root)
        - Path traversal prevention (.. normalization)
        
        Note: This does NOT follow symlinks. Use _check_symlink_policy for that.
        """
        path = Path(path)
        
        if path.is_absolute():
            # Absolute path - normalize without following symlinks
            # Use resolve(strict=False) but only for .. resolution
            normalized = path.resolve()
        else:
            # Relative path - join with repo root, then normalize
            normalized = (self.repo_root / path).resolve()
        
        return normalized
    
    def _get_unresolved_path(self, path: Union[str, Path]) -> Path:
        """
        Get path joined with repo_root without resolving symlinks.
        
        Used for symlink detection before path policy checks.
        """
        path = Path(path)
        if path.is_absolute():
            return path
        return self.repo_root / path
    
    def _check_workspace_boundary(self, path: Path, operation: str) -> None:
        """
        G-02: Check if a write operation is within workspace boundaries.
        
        Only enforced for write operations when workspace_root is set.
        Reads are allowed anywhere in the repo.
        
        Raises WorkspaceBoundaryViolation if write is outside workspace.
        """
        if not self._workspace_abs:
            return  # No workspace boundary enforcement
        
        if operation == "read":
            return  # Reads allowed anywhere
        
        # Check if resolved path is within workspace
        resolved = path.resolve()
        try:
            resolved.relative_to(self._workspace_abs)
        except ValueError:
            self._record_violation(str(path), f"Write outside workspace: {self.workspace_root}")
            raise WorkspaceBoundaryViolation(
                str(path),
                self.workspace_root,
                self.run_id
            )
    
    def _check_path_policy(self, path: Path, operation: str) -> None:
        """
        Check if a path is allowed by policy.
        
        Raises PathPolicyViolation if policy is violated.
        """
        resolved = path.resolve()
        
        # 1. Check if path is within repo root (prevent traversal)
        try:
            resolved.relative_to(self.repo_root)
        except ValueError:
            self._record_violation(str(path), f"Path escapes repo root: {resolved}")
            raise PathPolicyViolation(
                str(path),
                f"Path escapes repository root (attempted: {resolved})",
                self.run_id
            )
        
        # G-02: Check workspace boundary for write operations
        self._check_workspace_boundary(path, operation)
        
        # 2. Check allowlist (if configured)
        if self.config.allowlist_roots:
            relative_path = resolved.relative_to(self.repo_root)
            allowed = False
            for root in self.config.allowlist_roots:
                root_path = Path(root)
                try:
                    relative_path.relative_to(root_path)
                    allowed = True
                    break
                except ValueError:
                    # Also check if path IS the root
                    if str(relative_path) == root or str(relative_path).startswith(f"{root}/"):
                        allowed = True
                        break
            
            if not allowed:
                self._record_violation(str(path), f"Path not in allowlist: {relative_path}")
                raise PathPolicyViolation(
                    str(path),
                    f"Path not in allowlist roots: {self.config.allowlist_roots}",
                    self.run_id
                )
        
        # 3. Check denylist patterns
        relative_str = str(resolved.relative_to(self.repo_root))
        for pattern in self.config.denylist_patterns:
            if fnmatch.fnmatch(relative_str, pattern):
                self._record_violation(str(path), f"Path matches denylist: {pattern}")
                raise PathPolicyViolation(
                    str(path),
                    f"Path matches denylist pattern: {pattern}",
                    self.run_id
                )
        
        # 4. Check dotfiles policy
        if not self.config.allow_dotfiles:
            if resolved.name.startswith(".") and resolved.name not in (".", ".."):
                self._record_violation(str(path), "Dotfiles not allowed")
                raise PathPolicyViolation(
                    str(path),
                    "Dotfiles are not allowed (config: allow_dotfiles=False)",
                    self.run_id
                )
        
        # 5. Check hidden directories policy
        if not self.config.allow_hidden_dirs:
            for part in resolved.relative_to(self.repo_root).parts[:-1]:  # Exclude filename
                if part.startswith(".") and part not in (".", ".."):
                    self._record_violation(str(path), f"Hidden directory not allowed: {part}")
                    raise PathPolicyViolation(
                        str(path),
                        f"Hidden directory not allowed: {part}",
                        self.run_id
                    )
    
    def _check_symlink_policy(self, path: Path) -> Path:
        """
        Check symlink policy and return the real path if allowed.
        
        Returns the path to use (either original or resolved target).
        Raises SymlinkPolicyViolation if policy is violated.
        """
        if not path.is_symlink():
            return path
        
        if self.config.symlink_policy == "deny":
            self._record_violation(str(path), "Symlinks denied by policy")
            raise SymlinkPolicyViolation(str(path), str(path.resolve()), self.run_id)
        
        target = path.resolve()
        
        if self.config.symlink_policy == "follow_internal":
            # Check if target is within repo root
            try:
                target.relative_to(self.repo_root)
                return target
            except ValueError:
                self._record_violation(str(path), f"Symlink escapes repo: {target}")
                raise SymlinkPolicyViolation(str(path), str(target), self.run_id)
        
        # follow_all - allow any symlink (dangerous)
        return target
    
    def _record_violation(self, path: str, reason: str) -> None:
        """Record a policy violation."""
        with self._lock:
            self._policy_violations += 1
        
        logger.warning(
            f"RepoIO policy violation: run_id={self.run_id}, path={path}, reason={reason}"
        )
    
    def _record_audit(
        self,
        operation: str,
        path: str,
        success: bool,
        bytes_transferred: int = 0,
        error: Optional[str] = None,
    ) -> None:
        """Record an audit entry."""
        record = AuditRecord(
            operation=operation,
            path=path,
            success=success,
            bytes_transferred=bytes_transferred,
            error=error,
        )
        
        with self._lock:
            self._audit_records.append(record)
        
        if self.config.audit_to_log:
            if success:
                logger.debug(
                    f"RepoIO {operation}: run_id={self.run_id}, "
                    f"path={path}, bytes={bytes_transferred}"
                )
            else:
                logger.warning(
                    f"RepoIO {operation} FAILED: run_id={self.run_id}, "
                    f"path={path}, error={error}"
                )
    
    # =========================================================================
    # Read Operations
    # =========================================================================
    
    def read_text(
        self,
        path: Union[str, Path],
        encoding: str = "utf-8",
        errors: str = "strict",
    ) -> str:
        """
        Read text content from a file.
        
        Args:
            path: Path relative to repo root or absolute within repo
            encoding: Text encoding (default: utf-8)
            errors: Error handling ('strict', 'ignore', 'replace')
        
        Returns:
            File content as string
        
        Raises:
            PathPolicyViolation: If path violates policy
            SizeLimitExceeded: If file exceeds max read size
            FileNotFoundError: If file doesn't exist
        """
        # Check symlinks BEFORE resolution (so we can detect them)
        unresolved = self._get_unresolved_path(path)
        resolved = self._check_symlink_policy(unresolved)
        
        # Now check path policy on the final resolved path
        self._check_path_policy(resolved, "read")
        
        try:
            # Check size before reading
            size = resolved.stat().st_size
            if size > self.config.max_read_size_bytes:
                raise SizeLimitExceeded(
                    "max_read_size_bytes",
                    size,
                    self.config.max_read_size_bytes,
                    self.run_id
                )
            
            content = resolved.read_text(encoding=encoding, errors=errors)
            
            with self._lock:
                self._bytes_read += len(content.encode(encoding))
                self._files_read.add(str(resolved))
            
            self._record_audit("read_text", str(path), True, len(content))
            return content
            
        except (PathPolicyViolation, SizeLimitExceeded, SymlinkPolicyViolation):
            raise
        except Exception as e:
            self._record_audit("read_text", str(path), False, error=str(e))
            raise
    
    def read_bytes(self, path: Union[str, Path]) -> bytes:
        """
        Read binary content from a file.
        
        Args:
            path: Path relative to repo root or absolute within repo
        
        Returns:
            File content as bytes
        
        Raises:
            PathPolicyViolation: If path violates policy
            SizeLimitExceeded: If file exceeds max read size
            FileNotFoundError: If file doesn't exist
        """
        # Check symlinks BEFORE resolution (so we can detect them)
        unresolved = self._get_unresolved_path(path)
        resolved = self._check_symlink_policy(unresolved)
        
        # Now check path policy on the final resolved path
        self._check_path_policy(resolved, "read")
        
        try:
            # Check size before reading
            size = resolved.stat().st_size
            if size > self.config.max_read_size_bytes:
                raise SizeLimitExceeded(
                    "max_read_size_bytes",
                    size,
                    self.config.max_read_size_bytes,
                    self.run_id
                )
            
            content = resolved.read_bytes()
            
            with self._lock:
                self._bytes_read += len(content)
                self._files_read.add(str(resolved))
            
            self._record_audit("read_bytes", str(path), True, len(content))
            return content
            
        except (PathPolicyViolation, SizeLimitExceeded, SymlinkPolicyViolation):
            raise
        except Exception as e:
            self._record_audit("read_bytes", str(path), False, error=str(e))
            raise
    
    # =========================================================================
    # Write Operations
    # =========================================================================
    
    def write_text(
        self,
        path: Union[str, Path],
        content: str,
        encoding: str = "utf-8",
        create_parents: bool = True,
    ) -> int:
        """
        Write text content to a file.
        
        Args:
            path: Path relative to repo root or absolute within repo
            content: Text content to write
            encoding: Text encoding (default: utf-8)
            create_parents: Create parent directories if needed
        
        Returns:
            Number of bytes written
        
        Raises:
            PathPolicyViolation: If path violates policy
            SizeLimitExceeded: If content exceeds limits
        """
        resolved = self._normalize_path(path)
        self._check_path_policy(resolved, "write")
        
        content_bytes = content.encode(encoding)
        size = len(content_bytes)
        
        # Check per-file size limit
        if size > self.config.max_write_size_bytes:
            raise SizeLimitExceeded(
                "max_write_size_bytes",
                size,
                self.config.max_write_size_bytes,
                self.run_id
            )
        
        # Check total write limit
        with self._lock:
            if self._bytes_written + size > self.config.max_total_write_bytes:
                raise SizeLimitExceeded(
                    "max_total_write_bytes",
                    self._bytes_written + size,
                    self.config.max_total_write_bytes,
                    self.run_id
                )
            
            # Check files written limit
            if str(resolved) not in self._files_written:
                if len(self._files_written) >= self.config.max_files_written:
                    raise SizeLimitExceeded(
                        "max_files_written",
                        len(self._files_written) + 1,
                        self.config.max_files_written,
                        self.run_id
                    )
        
        try:
            is_new = not resolved.exists()
            
            if create_parents:
                resolved.parent.mkdir(parents=True, exist_ok=True)
            
            resolved.write_text(content, encoding=encoding)
            
            with self._lock:
                self._bytes_written += size
                self._files_written.add(str(resolved))
                if is_new:
                    self._files_created.add(str(resolved))
            
            self._record_audit("write_text", str(path), True, size)
            return size
            
        except (PathPolicyViolation, SizeLimitExceeded):
            raise
        except Exception as e:
            self._record_audit("write_text", str(path), False, error=str(e))
            raise
    
    def write_bytes(
        self,
        path: Union[str, Path],
        content: bytes,
        create_parents: bool = True,
    ) -> int:
        """
        Write binary content to a file.
        
        Args:
            path: Path relative to repo root or absolute within repo
            content: Binary content to write
            create_parents: Create parent directories if needed
        
        Returns:
            Number of bytes written
        
        Raises:
            PathPolicyViolation: If path violates policy
            SizeLimitExceeded: If content exceeds limits
        """
        resolved = self._normalize_path(path)
        self._check_path_policy(resolved, "write")
        
        size = len(content)
        
        # Check per-file size limit
        if size > self.config.max_write_size_bytes:
            raise SizeLimitExceeded(
                "max_write_size_bytes",
                size,
                self.config.max_write_size_bytes,
                self.run_id
            )
        
        # Check total write limit
        with self._lock:
            if self._bytes_written + size > self.config.max_total_write_bytes:
                raise SizeLimitExceeded(
                    "max_total_write_bytes",
                    self._bytes_written + size,
                    self.config.max_total_write_bytes,
                    self.run_id
                )
            
            if str(resolved) not in self._files_written:
                if len(self._files_written) >= self.config.max_files_written:
                    raise SizeLimitExceeded(
                        "max_files_written",
                        len(self._files_written) + 1,
                        self.config.max_files_written,
                        self.run_id
                    )
        
        try:
            is_new = not resolved.exists()
            
            if create_parents:
                resolved.parent.mkdir(parents=True, exist_ok=True)
            
            resolved.write_bytes(content)
            
            with self._lock:
                self._bytes_written += size
                self._files_written.add(str(resolved))
                if is_new:
                    self._files_created.add(str(resolved))
            
            self._record_audit("write_bytes", str(path), True, size)
            return size
            
        except (PathPolicyViolation, SizeLimitExceeded):
            raise
        except Exception as e:
            self._record_audit("write_bytes", str(path), False, error=str(e))
            raise
    
    def copy_file(
        self,
        src: Union[str, Path],
        dst: Union[str, Path],
        create_parents: bool = True,
    ) -> int:
        """
        Copy a file within the repo (policy-enforced).
        
        Both source and destination must pass policy checks.
        Uses read_bytes + write_bytes internally for audit trail.
        
        Args:
            src: Source path relative to repo root
            dst: Destination path relative to repo root
            create_parents: Create parent directories for dst if needed
        
        Returns:
            Number of bytes copied
        
        Raises:
            PathPolicyViolation: If either path violates policy
            SizeLimitExceeded: If file exceeds limits
            FileNotFoundError: If source doesn't exist
        """
        # Read source (enforces read policy + size limit)
        content = self.read_bytes(src)
        
        # Write to destination (enforces write policy + limits)
        return self.write_bytes(dst, content, create_parents=create_parents)
    
    # =========================================================================
    # Directory Operations
    # =========================================================================
    
    def list_files(
        self,
        path: Union[str, Path] = ".",
        recursive: bool = False,
    ) -> List[Path]:
        """
        List files in a directory.
        
        Args:
            path: Directory path relative to repo root
            recursive: Whether to list recursively
        
        Returns:
            List of Path objects (relative to repo root)
        """
        resolved = self._normalize_path(path)
        self._check_path_policy(resolved, "list")
        
        try:
            files = []
            if recursive:
                for item in resolved.rglob("*"):
                    if item.is_file():
                        try:
                            self._check_path_policy(item, "list")
                            files.append(item.relative_to(self.repo_root))
                        except PathPolicyViolation:
                            continue  # Skip denied paths
            else:
                for item in resolved.iterdir():
                    if item.is_file():
                        try:
                            self._check_path_policy(item, "list")
                            files.append(item.relative_to(self.repo_root))
                        except PathPolicyViolation:
                            continue
            
            self._record_audit("list_files", str(path), True, len(files))
            return files
            
        except Exception as e:
            self._record_audit("list_files", str(path), False, error=str(e))
            raise
    
    def glob(self, pattern: str) -> List[Path]:
        """
        Find files matching a glob pattern.
        
        Args:
            pattern: Glob pattern (e.g., "**/*.py")
        
        Returns:
            List of matching Path objects (relative to repo root)
        """
        try:
            matches = []
            for item in self.repo_root.glob(pattern):
                try:
                    self._check_path_policy(item, "glob")
                    if item.is_file():
                        matches.append(item.relative_to(self.repo_root))
                except PathPolicyViolation:
                    continue  # Skip denied paths
            
            self._record_audit("glob", pattern, True, len(matches))
            return matches
            
        except Exception as e:
            self._record_audit("glob", pattern, False, error=str(e))
            raise
    
    def exists(self, path: Union[str, Path]) -> bool:
        """Check if a path exists."""
        try:
            resolved = self._normalize_path(path)
            self._check_path_policy(resolved, "exists")
            result = resolved.exists()
            self._record_audit("exists", str(path), True)
            return result
        except PathPolicyViolation:
            # Policy violation means we deny access, report as not existing
            return False
        except Exception as e:
            self._record_audit("exists", str(path), False, error=str(e))
            return False
    
    def stat(self, path: Union[str, Path]) -> os.stat_result:
        """Get file stats."""
        resolved = self._normalize_path(path)
        self._check_path_policy(resolved, "stat")
        
        try:
            result = resolved.stat()
            self._record_audit("stat", str(path), True)
            return result
        except Exception as e:
            self._record_audit("stat", str(path), False, error=str(e))
            raise
    
    def is_file(self, path: Union[str, Path]) -> bool:
        """Check if path is a file."""
        try:
            resolved = self._normalize_path(path)
            self._check_path_policy(resolved, "is_file")
            return resolved.is_file()
        except PathPolicyViolation:
            return False
    
    def is_dir(self, path: Union[str, Path]) -> bool:
        """Check if path is a directory."""
        try:
            resolved = self._normalize_path(path)
            self._check_path_policy(resolved, "is_dir")
            return resolved.is_dir()
        except PathPolicyViolation:
            return False
    
    # =========================================================================
    # Grep (centralized search)
    # =========================================================================
    
    def grep(
        self,
        pattern: str,
        path: Union[str, Path] = ".",
        file_pattern: str = "*",
        recursive: bool = True,
        max_matches: int = 1000,
        context_lines: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Search for pattern in files.
        
        Args:
            pattern: Regex pattern to search for
            path: Directory to search in (relative to repo root)
            file_pattern: Glob pattern for files to search (e.g., "*.py")
            recursive: Whether to search recursively
            max_matches: Maximum number of matches to return
            context_lines: Number of context lines before/after match
        
        Returns:
            List of match dicts with keys: file, line, line_number, match
        """
        resolved = self._normalize_path(path)
        self._check_path_policy(resolved, "grep")
        
        try:
            regex = re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid regex pattern: {e}")
        
        matches = []
        
        try:
            if recursive:
                files = resolved.rglob(file_pattern)
            else:
                files = resolved.glob(file_pattern)
            
            for file_path in files:
                if not file_path.is_file():
                    continue
                
                try:
                    self._check_path_policy(file_path, "grep")
                except PathPolicyViolation:
                    continue
                
                try:
                    content = file_path.read_text(errors="ignore")
                    lines = content.splitlines()
                    
                    for i, line in enumerate(lines):
                        if regex.search(line):
                            match_info = {
                                "file": str(file_path.relative_to(self.repo_root)),
                                "line": line,
                                "line_number": i + 1,
                                "match": regex.search(line).group(),
                            }
                            
                            if context_lines > 0:
                                start = max(0, i - context_lines)
                                end = min(len(lines), i + context_lines + 1)
                                match_info["context"] = lines[start:end]
                            
                            matches.append(match_info)
                            
                            if len(matches) >= max_matches:
                                break
                    
                    if len(matches) >= max_matches:
                        break
                        
                except (OSError, UnicodeDecodeError):
                    continue
            
            self._record_audit("grep", f"{pattern} in {path}", True, len(matches))
            return matches
            
        except Exception as e:
            self._record_audit("grep", f"{pattern} in {path}", False, error=str(e))
            raise
    
    # =========================================================================
    # Audit & Summary
    # =========================================================================
    
    def get_audit_summary(self) -> AuditSummary:
        """Get summary of all IO operations for this run."""
        with self._lock:
            return AuditSummary(
                run_id=self.run_id,
                repo_root=str(self.repo_root),
                bytes_read=self._bytes_read,
                bytes_written=self._bytes_written,
                files_read=len(self._files_read),
                files_written=len(self._files_written),
                files_created=len(self._files_created),
                operations_count=len(self._audit_records),
                policy_violations=self._policy_violations,
                errors=[r.error for r in self._audit_records if r.error],
            )
    
    def get_audit_records(self) -> List[AuditRecord]:
        """Get all audit records for this run."""
        with self._lock:
            return list(self._audit_records)


# =============================================================================
# Global/Context Management
# =============================================================================

# Thread-local storage for current RepoIO context
_context = threading.local()


def get_repo_io() -> Optional[RepoIO]:
    """
    Get the current RepoIO context.
    
    Returns None if no context is set.
    """
    return getattr(_context, "repo_io", None)


def set_repo_io(repo_io: Optional[RepoIO]) -> None:
    """Set the current RepoIO context."""
    _context.repo_io = repo_io


class repo_io_context:
    """
    Context manager for RepoIO.
    
    Usage:
        with repo_io_context(repo_root, run_id) as io:
            content = io.read_text("file.py")
        
        # With workspace boundary enforcement (for monorepos):
        with repo_io_context(repo_root, run_id, workspace_root="packages/api") as io:
            io.write_text("packages/api/src/file.py", content)  # Allowed
            io.write_text("packages/web/src/file.py", content)  # Raises WorkspaceBoundaryViolation
    """
    
    def __init__(
        self,
        repo_root: Union[str, Path],
        run_id: str,
        config: Optional[RepoIOConfig] = None,
        workspace_root: Optional[str] = None,
    ):
        self.repo_io = RepoIO(repo_root, run_id, config, workspace_root=workspace_root)
        self._previous: Optional[RepoIO] = None
    
    def __enter__(self) -> RepoIO:
        self._previous = get_repo_io()
        set_repo_io(self.repo_io)
        return self.repo_io
    
    def __exit__(self, *args) -> None:
        # Log audit summary on exit
        summary = self.repo_io.get_audit_summary()
        logger.info(
            f"RepoIO summary: run_id={summary.run_id}, "
            f"reads={summary.files_read} ({summary.bytes_read}B), "
            f"writes={summary.files_written} ({summary.bytes_written}B), "
            f"violations={summary.policy_violations}"
        )
        
        set_repo_io(self._previous)


# =============================================================================
# Convenience Exports
# =============================================================================

__all__ = [
    # Core class
    "RepoIO",
    "RepoIOConfig",
    # Exceptions
    "RepoIOError",
    "PathPolicyViolation", 
    "SizeLimitExceeded",
    "SymlinkPolicyViolation",
    # Audit
    "AuditRecord",
    "AuditSummary",
    # Context management
    "get_repo_io",
    "set_repo_io",
    "repo_io_context",
    # Config
    "get_default_config",
]
