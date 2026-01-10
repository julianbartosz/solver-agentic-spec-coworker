"""
Memory Sampler for V22-011 Root Cause Analysis.

This module provides continuous memory telemetry during workflow execution to:
1. Track RSS growth (ground truth for process memory)
2. Track tracemalloc allocations (Python-only allocations)
3. Attribute memory growth to specific nodes/phases
4. Provide graceful abort when memory exceeds ceiling

The sampler runs as a background thread and emits samples to:
- MEMORY_TRACE.jsonl in the artifacts directory
- graph.memory.sample events in GRAPH_TRACE.jsonl

Environment Variables:
- IC_MEM_SAMPLER_ENABLED: Enable/disable sampler (default: "true" in production profile)
- IC_MEM_SAMPLER_INTERVAL_S: Sample interval in seconds (default: 1.0)
- IC_MAX_RSS_MB: RSS ceiling in MB - triggers graceful abort if exceeded (default: 4096)
- IC_MEM_TRACEMALLOC_TOP: Number of top allocators to log (default: 10)

V22-011 Investigation Plan:
- If RSS rises but tracemalloc stays flat → native allocations (psycopg, tokenizers, etc.)
- If both rise → Python object growth (graph trace bundles, state copies, LLM responses)

Usage:
    from integration_coworker.graph.memory_sampler import (
        start_memory_sampler, stop_memory_sampler, set_current_phase
    )
    
    sampler = start_memory_sampler(run_id="run-123", artifacts_dir="/tmp/traces")
    set_current_phase("ingest_spec")
    # ... workflow runs ...
    stop_memory_sampler()
"""

import atexit
import json
import logging
import os
import threading
import time
import traceback
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Configuration
# =============================================================================

@dataclass
class MemorySamplerConfig:
    """Configuration for the memory sampler."""
    
    # Enable/disable sampler
    enabled: bool = field(
        default_factory=lambda: os.getenv("IC_MEM_SAMPLER_ENABLED", "true").lower() in ("true", "1", "yes")
    )
    
    # Sample interval in seconds
    interval_s: float = field(
        default_factory=lambda: float(os.getenv("IC_MEM_SAMPLER_INTERVAL_S", "1.0"))
    )
    
    # RSS ceiling in MB - triggers graceful abort if exceeded
    max_rss_mb: float = field(
        default_factory=lambda: float(os.getenv("IC_MAX_RSS_MB", "4096"))  # 4GB default
    )
    
    # Number of top tracemalloc allocators to log
    tracemalloc_top: int = field(
        default_factory=lambda: int(os.getenv("IC_MEM_TRACEMALLOC_TOP", "10"))
    )
    
    # Minimum RSS delta (MB) to trigger a sample log (reduces noise)
    min_delta_mb: float = field(
        default_factory=lambda: float(os.getenv("IC_MEM_MIN_DELTA_MB", "10.0"))
    )
    
    # Rolling window size - only keep last N samples in memory (0 = unlimited)
    # All samples are still written to JSONL, this just limits in-memory storage
    rolling_window_size: int = field(
        default_factory=lambda: int(os.getenv("IC_MEM_ROLLING_WINDOW", "100"))
    )


def get_sampler_config() -> MemorySamplerConfig:
    """Get the current sampler configuration from environment."""
    return MemorySamplerConfig()


# =============================================================================
# RSS Ceiling Exception
# =============================================================================

class RSSCeilingExceeded(Exception):
    """
    Raised when RSS exceeds the configured ceiling (IC_MAX_RSS_MB).
    
    This is a graceful abort - the workflow should catch this, flush logs,
    save checkpoints, and exit cleanly. It's NOT a sudden SIGKILL from OOM.
    
    IMPORTANT: If a process exits with code 137, that is 128+9 (SIGKILL from OOM killer).
    This ceiling mechanism exists specifically to fail BEFORE the kernel OOM killer does.
    
    Attributes:
        current_rss_mb: The RSS when the ceiling was exceeded
        ceiling_mb: The configured ceiling
        node_name: The node that was running when ceiling was exceeded
    """
    
    def __init__(self, current_rss_mb: float, ceiling_mb: float, node_name: Optional[str] = None):
        self.current_rss_mb = current_rss_mb
        self.ceiling_mb = ceiling_mb
        self.node_name = node_name
        msg = (
            f"RSS ceiling exceeded: {current_rss_mb:.1f}MB > {ceiling_mb:.1f}MB "
            f"(node: {node_name or 'unknown'}). Graceful abort triggered. "
            f"(Exit code 137 = SIGKILL from OOM killer; this ceiling prevents that.)"
        )
        super().__init__(msg)


# =============================================================================
# Memory Metrics Collection
# =============================================================================

def get_rss_bytes() -> int:
    """
    Get actual process RSS in bytes.
    
    Uses psutil if available, falls back to /proc/self/statm on Linux.
    Returns -1 if unavailable.
    """
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except ImportError:
        pass
    except Exception:
        pass
    
    try:
        with open('/proc/self/statm', 'r') as f:
            parts = f.read().split()
            resident_pages = int(parts[1])
            page_size = 4096
            try:
                page_size = os.sysconf('SC_PAGE_SIZE')
            except (AttributeError, ValueError):
                pass
            return resident_pages * page_size
    except (FileNotFoundError, IOError, IndexError, ValueError):
        pass
    
    return -1


def get_rss_mb() -> float:
    """Get actual process RSS in MB. Returns -1.0 if unavailable."""
    rss_bytes = get_rss_bytes()
    if rss_bytes < 0:
        return -1.0
    return rss_bytes / (1024 * 1024)


def get_tracemalloc_stats() -> Dict[str, Any]:
    """
    Get tracemalloc memory stats.
    
    Returns dict with:
    - current_bytes: Current traced allocations
    - peak_bytes: Peak traced allocations
    - enabled: Whether tracemalloc is running
    """
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            return {"current_bytes": -1, "peak_bytes": -1, "enabled": False}
        
        current, peak = tracemalloc.get_traced_memory()
        return {
            "current_bytes": current,
            "peak_bytes": peak,
            "enabled": True,
        }
    except ImportError:
        return {"current_bytes": -1, "peak_bytes": -1, "enabled": False}
    except Exception:
        return {"current_bytes": -1, "peak_bytes": -1, "enabled": False}


def get_tracemalloc_top_allocators(limit: int = 10) -> List[Dict[str, Any]]:
    """
    Get the top tracemalloc allocators by size.
    
    Returns list of dicts with:
    - filename: Source file
    - lineno: Line number
    - size_bytes: Allocation size
    - count: Number of allocations from this location
    """
    try:
        import tracemalloc
        if not tracemalloc.is_tracing():
            return []
        
        snapshot = tracemalloc.take_snapshot()
        # Filter to only show our code, not stdlib/site-packages
        snapshot = snapshot.filter_traces([
            tracemalloc.Filter(True, "**/integration_coworker/**"),
            tracemalloc.Filter(True, "**/src/**"),
        ])
        
        stats = snapshot.statistics('lineno')[:limit]
        return [
            {
                "filename": str(stat.traceback[0].filename) if stat.traceback else "unknown",
                "lineno": stat.traceback[0].lineno if stat.traceback else 0,
                "size_bytes": stat.size,
                "count": stat.count,
            }
            for stat in stats
        ]
    except Exception:
        return []


def get_cgroup_memory_info() -> Dict[str, int]:
    """
    Get cgroup memory limit and usage (for container environments).
    
    Returns dict with:
    - limit_bytes: Memory limit (-1 if no limit or not in cgroup)
    - usage_bytes: Current cgroup usage (-1 if unavailable)
    """
    result = {"limit_bytes": -1, "usage_bytes": -1}
    
    # Try cgroups v2
    try:
        with open('/sys/fs/cgroup/memory.max', 'r') as f:
            limit = f.read().strip()
            result["limit_bytes"] = int(limit) if limit != "max" else -1
    except (FileNotFoundError, ValueError):
        # Try cgroups v1
        try:
            with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
                result["limit_bytes"] = int(f.read().strip())
        except (FileNotFoundError, ValueError):
            pass
    
    try:
        with open('/sys/fs/cgroup/memory.current', 'r') as f:
            result["usage_bytes"] = int(f.read().strip())
    except (FileNotFoundError, ValueError):
        try:
            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
                result["usage_bytes"] = int(f.read().strip())
        except (FileNotFoundError, ValueError):
            pass
    
    return result


# =============================================================================
# Memory Sample Data Structure
# =============================================================================

@dataclass
class MemorySample:
    """A single memory sample with all metrics."""
    
    timestamp: str  # ISO8601 UTC
    sample_number: int
    run_id: str
    node_name: Optional[str]
    phase: Optional[str]
    
    # RSS (ground truth for process memory)
    rss_bytes: int
    rss_mb: float
    rss_delta_mb: float  # Change since last sample
    
    # Tracemalloc (Python allocations only)
    tracemalloc_current_bytes: int
    tracemalloc_peak_bytes: int
    tracemalloc_enabled: bool
    
    # Cgroup info (for container environments)
    cgroup_limit_bytes: int
    cgroup_usage_bytes: int
    cgroup_usage_pct: float
    
    # Top allocators (optional, sampled periodically)
    top_allocators: Optional[List[Dict[str, Any]]] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        d = {
            "ts": self.timestamp,
            "sample_number": self.sample_number,
            "run_id": self.run_id,
            "node_name": self.node_name,
            "phase": self.phase,
            "rss_bytes": self.rss_bytes,
            "rss_mb": round(self.rss_mb, 2),
            "rss_delta_mb": round(self.rss_delta_mb, 2),
            "tracemalloc_current_bytes": self.tracemalloc_current_bytes,
            "tracemalloc_peak_bytes": self.tracemalloc_peak_bytes,
            "tracemalloc_enabled": self.tracemalloc_enabled,
            "cgroup_limit_bytes": self.cgroup_limit_bytes,
            "cgroup_usage_bytes": self.cgroup_usage_bytes,
            "cgroup_usage_pct": round(self.cgroup_usage_pct, 2) if self.cgroup_usage_pct >= 0 else -1,
        }
        if self.top_allocators:
            d["top_allocators"] = self.top_allocators
        return d


# =============================================================================
# Memory Sampler Thread
# =============================================================================

class MemorySampler:
    """
    Background thread that samples memory metrics at regular intervals.
    
    Thread-safe: All state access is protected by a lock.
    """
    
    def __init__(
        self,
        run_id: str,
        artifacts_dir: Optional[str] = None,
        config: Optional[MemorySamplerConfig] = None,
        ceiling_callback: Optional[Callable[[RSSCeilingExceeded], None]] = None,
    ):
        """
        Initialize the memory sampler.
        
        Args:
            run_id: The workflow run ID (for correlation)
            artifacts_dir: Directory for MEMORY_TRACE.jsonl output
            config: Sampler configuration (defaults from env vars)
            ceiling_callback: Called when RSS ceiling is exceeded (instead of raising)
        """
        self.run_id = run_id
        self.artifacts_dir = artifacts_dir
        self.config = config or get_sampler_config()
        self.ceiling_callback = ceiling_callback
        
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        
        # Ceiling exceeded event - main thread can poll this
        self._ceiling_exceeded_event = threading.Event()
        
        # Current context (set by workflow)
        self._current_node: Optional[str] = None
        self._current_phase: Optional[str] = None
        
        # Sampling state
        self._sample_number = 0
        self._total_samples_written = 0  # Track total even if rolling
        self._last_rss_mb = 0.0
        self._peak_rss_mb = 0.0
        self._peak_rss_node: Optional[str] = None  # Node when peak was hit
        
        # Rolling window of recent samples (bounded memory via deque maxlen)
        # All samples are written to JSONL, but we only keep recent ones in RAM
        # Using deque(maxlen=N) guarantees bounded memory - cannot regress
        self._rolling_window_size = self.config.rolling_window_size
        if self._rolling_window_size > 0:
            self._samples: Deque[MemorySample] = deque(maxlen=self._rolling_window_size)
        else:
            # Unlimited (not recommended for production)
            self._samples: Deque[MemorySample] = deque()
        
        # Per-node RSS tracking for summary
        self._node_rss_start: Dict[str, float] = {}  # Node -> starting RSS
        self._node_rss_delta: Dict[str, float] = {}  # Node -> cumulative delta
        self._node_sample_count: Dict[str, int] = {}  # Node -> sample count
        
        # Ceiling info (captured at breach time)
        self._ceiling_exceeded = False
        self._ceiling_breach_info: Optional[Dict[str, Any]] = None
        
        # Trace file handle for streaming writes
        self._trace_file: Optional[Path] = None
        self._trace_file_handle = None
        if artifacts_dir:
            self._trace_file = Path(artifacts_dir) / "MEMORY_TRACE.jsonl"
            self._trace_file.parent.mkdir(parents=True, exist_ok=True)
            # Open file handle for streaming (append mode, line-buffered)
            try:
                self._trace_file_handle = open(self._trace_file, "a", encoding="utf-8", buffering=1)
            except Exception as e:
                logger.warning(f"Failed to open memory trace file: {e}")
                self._trace_file_handle = None
    
    def start(self) -> None:
        """Start the sampler thread."""
        if not self.config.enabled:
            logger.debug("Memory sampler disabled via IC_MEM_SAMPLER_ENABLED")
            return
        
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Memory sampler already running")
            return
        
        # Initialize tracemalloc if not already running
        try:
            import tracemalloc
            if not tracemalloc.is_tracing():
                tracemalloc.start()
                logger.info("tracemalloc started for memory sampler")
        except ImportError:
            logger.warning("tracemalloc not available - Python allocation tracking disabled")
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._sample_loop,
            name=f"MemorySampler-{self.run_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            f"Memory sampler started: interval={self.config.interval_s}s, "
            f"ceiling={self.config.max_rss_mb}MB, run_id={self.run_id}"
        )
    
    def stop(self) -> List[MemorySample]:
        """
        Stop the sampler thread and return recent samples (rolling window).
        
        Uses join with timeout to ensure we don't block shutdown.
        Thread is daemon=True so it won't prevent process exit.
        
        Returns:
            List of recent memory samples (last N from rolling window)
        """
        if self._thread is None:
            return []
        
        self._stop_event.set()
        self._thread.join(timeout=2.0)  # Short timeout - thread is daemon anyway
        
        if self._thread.is_alive():
            logger.warning("Memory sampler thread did not stop cleanly (daemon will be killed on exit)")
        
        with self._lock:
            # Write final summary event to trace
            self._write_summary_event()
            
            # Close file handle
            if self._trace_file_handle:
                try:
                    self._trace_file_handle.close()
                except Exception:
                    pass
                self._trace_file_handle = None
            
            samples = list(self._samples)  # Only rolling window samples
            logger.info(
                f"Memory sampler stopped: {self._total_samples_written} total samples written, "
                f"{len(samples)} in rolling window, peak_rss={self._peak_rss_mb:.1f}MB "
                f"(node: {self._peak_rss_node or 'unknown'})"
            )
            return samples
    
    def check_ceiling(self) -> Optional[RSSCeilingExceeded]:
        """
        Check if RSS ceiling was exceeded (for main thread polling).
        
        Call this at safe points (node boundaries) to detect ceiling breach
        and raise in the main thread context.
        
        Returns:
            RSSCeilingExceeded exception if ceiling was breached, None otherwise
        """
        if self._ceiling_exceeded_event.is_set():
            with self._lock:
                if self._ceiling_breach_info:
                    return RSSCeilingExceeded(
                        current_rss_mb=self._ceiling_breach_info["rss_mb"],
                        ceiling_mb=self._ceiling_breach_info["ceiling_mb"],
                        node_name=self._ceiling_breach_info.get("node_name"),
                    )
        return None
    
    def get_summary(self) -> Dict[str, Any]:
        """
        Get a summary of memory usage by node.
        
        Returns:
            Dict with per-node memory stats and overall peak info
        """
        with self._lock:
            return {
                "total_samples": self._total_samples_written,
                "peak_rss_mb": self._peak_rss_mb,
                "peak_rss_node": self._peak_rss_node,
                "ceiling_exceeded": self._ceiling_exceeded,
                "node_stats": {
                    node: {
                        "rss_delta_mb": self._node_rss_delta.get(node, 0.0),
                        "sample_count": self._node_sample_count.get(node, 0),
                    }
                    for node in self._node_sample_count
                },
            }
    
    def set_node(self, node_name: Optional[str]) -> None:
        """Set the current node name for sample attribution."""
        with self._lock:
            # Record starting RSS for new node
            if node_name and node_name not in self._node_rss_start:
                self._node_rss_start[node_name] = self._last_rss_mb
                self._node_rss_delta[node_name] = 0.0
                self._node_sample_count[node_name] = 0
            self._current_node = node_name
    
    def set_phase(self, phase: Optional[str]) -> None:
        """Set the current phase for sample attribution."""
        with self._lock:
            self._current_phase = phase
    
    def get_peak_rss_mb(self) -> float:
        """Get the peak RSS observed during sampling."""
        with self._lock:
            return self._peak_rss_mb
    
    def is_ceiling_exceeded(self) -> bool:
        """Check if RSS ceiling was exceeded."""
        with self._lock:
            return self._ceiling_exceeded
    
    def _sample_loop(self) -> None:
        """Main sampling loop (runs in background thread)."""
        while not self._stop_event.is_set():
            try:
                self._take_sample()
            except Exception as e:
                logger.warning(f"Memory sample error: {e}")
            
            # Wait for next sample interval (interruptible)
            self._stop_event.wait(timeout=self.config.interval_s)
    
    def _take_sample(self) -> None:
        """Take a single memory sample."""
        ts = datetime.now(timezone.utc).isoformat()
        
        # Collect metrics
        rss_bytes = get_rss_bytes()
        rss_mb = rss_bytes / (1024 * 1024) if rss_bytes > 0 else -1.0
        
        tracemalloc_stats = get_tracemalloc_stats()
        cgroup_info = get_cgroup_memory_info()
        
        # Calculate cgroup usage percentage
        cgroup_usage_pct = -1.0
        if cgroup_info["limit_bytes"] > 0 and cgroup_info["usage_bytes"] > 0:
            cgroup_usage_pct = (cgroup_info["usage_bytes"] / cgroup_info["limit_bytes"]) * 100
        
        with self._lock:
            self._sample_number += 1
            
            # Calculate RSS delta
            rss_delta_mb = rss_mb - self._last_rss_mb if self._last_rss_mb > 0 else 0.0
            self._last_rss_mb = rss_mb
            
            # Track peak
            if rss_mb > self._peak_rss_mb:
                self._peak_rss_mb = rss_mb
            
            # Get top allocators periodically (every 10 samples) or on significant delta
            top_allocators = None
            if self._sample_number % 10 == 0 or abs(rss_delta_mb) > self.config.min_delta_mb:
                top_allocators = get_tracemalloc_top_allocators(self.config.tracemalloc_top)
            
            sample = MemorySample(
                timestamp=ts,
                sample_number=self._sample_number,
                run_id=self.run_id,
                node_name=self._current_node,
                phase=self._current_phase,
                rss_bytes=rss_bytes,
                rss_mb=rss_mb,
                rss_delta_mb=rss_delta_mb,
                tracemalloc_current_bytes=tracemalloc_stats["current_bytes"],
                tracemalloc_peak_bytes=tracemalloc_stats["peak_bytes"],
                tracemalloc_enabled=tracemalloc_stats["enabled"],
                cgroup_limit_bytes=cgroup_info["limit_bytes"],
                cgroup_usage_bytes=cgroup_info["usage_bytes"],
                cgroup_usage_pct=cgroup_usage_pct,
                top_allocators=top_allocators,
            )
            
            # Track per-node stats
            if self._current_node:
                self._node_rss_delta[self._current_node] = (
                    self._node_rss_delta.get(self._current_node, 0.0) + rss_delta_mb
                )
                self._node_sample_count[self._current_node] = (
                    self._node_sample_count.get(self._current_node, 0) + 1
                )
            
            # Track peak with node attribution
            if rss_mb > self._peak_rss_mb:
                self._peak_rss_mb = rss_mb
                self._peak_rss_node = self._current_node
            
            # Rolling window: deque(maxlen=N) automatically drops oldest
            # This is bulletproof - no manual pop needed, impossible to regress
            self._samples.append(sample)
            
            self._total_samples_written += 1
            
            # Write to trace file
            self._write_sample(sample)
            
            # Check ceiling - set event flag for main thread to poll
            if rss_mb > self.config.max_rss_mb and not self._ceiling_exceeded:
                self._ceiling_exceeded = True
                self._ceiling_breach_info = {
                    "rss_mb": rss_mb,
                    "ceiling_mb": self.config.max_rss_mb,
                    "node_name": self._current_node,
                    "timestamp": ts,
                    "sample_number": self._sample_number,
                }
                
                # Set event so main thread can detect via check_ceiling()
                self._ceiling_exceeded_event.set()
                
                exc = RSSCeilingExceeded(rss_mb, self.config.max_rss_mb, self._current_node)
                logger.error(str(exc))
                
                # Write ceiling breach event
                self._write_ceiling_breach(sample)
                
                # Still call callback for logging/telemetry (but main thread should poll)
                if self.ceiling_callback:
                    try:
                        self.ceiling_callback(exc)
                    except Exception as cb_err:
                        logger.error(f"Ceiling callback error: {cb_err}")
    
    def _write_sample(self, sample: MemorySample) -> None:
        """Write a sample to the trace file (streaming, already open)."""
        if self._trace_file_handle is None:
            return
        
        try:
            self._trace_file_handle.write(json.dumps(sample.to_dict(), separators=(",", ":")) + "\n")
            # Line-buffered mode ensures immediate write
        except Exception as e:
            logger.debug(f"Failed to write memory sample: {e}")
    
    def _write_ceiling_breach(self, sample: MemorySample) -> None:
        """Write a ceiling breach event."""
        if self._trace_file_handle is None:
            return
        
        try:
            event = {
                "ts": sample.timestamp,
                "event": "memory.ceiling_breach",
                "run_id": self.run_id,
                "node_name": sample.node_name,
                "rss_mb": sample.rss_mb,
                "ceiling_mb": self.config.max_rss_mb,
                "tracemalloc_current_bytes": sample.tracemalloc_current_bytes,
                "tracemalloc_peak_bytes": sample.tracemalloc_peak_bytes,
                "top_allocators": sample.top_allocators or [],
            }
            self._trace_file_handle.write(json.dumps(event, separators=(",", ":")) + "\n")
        except Exception as e:
            logger.debug(f"Failed to write ceiling breach event: {e}")
    
    def _write_summary_event(self) -> None:
        """Write a final summary event to the trace file."""
        if self._trace_file_handle is None:
            return
        
        try:
            summary = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "memory.summary",
                "run_id": self.run_id,
                "total_samples": self._total_samples_written,
                "peak_rss_mb": round(self._peak_rss_mb, 2),
                "peak_rss_node": self._peak_rss_node,
                "ceiling_exceeded": self._ceiling_exceeded,
                "ceiling_mb": self.config.max_rss_mb,
                "node_stats": {
                    node: {
                        "rss_delta_mb": round(self._node_rss_delta.get(node, 0.0), 2),
                        "sample_count": self._node_sample_count.get(node, 0),
                    }
                    for node in self._node_sample_count
                },
            }
            self._trace_file_handle.write(json.dumps(summary, separators=(",", ":")) + "\n")
        except Exception as e:
            logger.debug(f"Failed to write summary event: {e}")


# =============================================================================
# Global Sampler Instance (for easy access from runtime)
# =============================================================================

_active_sampler: Optional[MemorySampler] = None
_sampler_lock = threading.Lock()


def start_memory_sampler(
    run_id: str,
    artifacts_dir: Optional[str] = None,
    ceiling_callback: Optional[Callable[[RSSCeilingExceeded], None]] = None,
) -> Optional[MemorySampler]:
    """
    Start the global memory sampler for a workflow run.
    
    Args:
        run_id: The workflow run ID
        artifacts_dir: Directory for MEMORY_TRACE.jsonl
        ceiling_callback: Called when RSS ceiling is exceeded
        
    Returns:
        The sampler instance, or None if disabled
    """
    global _active_sampler
    
    config = get_sampler_config()
    if not config.enabled:
        return None
    
    with _sampler_lock:
        if _active_sampler is not None:
            logger.warning("Memory sampler already active - stopping previous")
            _active_sampler.stop()
        
        _active_sampler = MemorySampler(
            run_id=run_id,
            artifacts_dir=artifacts_dir,
            config=config,
            ceiling_callback=ceiling_callback,
        )
        _active_sampler.start()
        return _active_sampler


def stop_memory_sampler() -> Optional[List[MemorySample]]:
    """
    Stop the global memory sampler.
    
    Returns:
        List of collected samples, or None if no sampler was running
    """
    global _active_sampler
    
    with _sampler_lock:
        if _active_sampler is None:
            return None
        
        samples = _active_sampler.stop()
        _active_sampler = None
        return samples


def set_current_node(node_name: Optional[str]) -> None:
    """Set the current node for sample attribution."""
    with _sampler_lock:
        if _active_sampler:
            _active_sampler.set_node(node_name)


def set_current_phase(phase: Optional[str]) -> None:
    """Set the current phase for sample attribution."""
    with _sampler_lock:
        if _active_sampler:
            _active_sampler.set_phase(phase)


def get_sampler() -> Optional[MemorySampler]:
    """Get the active memory sampler instance."""
    with _sampler_lock:
        return _active_sampler


def is_ceiling_exceeded() -> bool:
    """Check if the RSS ceiling has been exceeded."""
    with _sampler_lock:
        if _active_sampler:
            return _active_sampler.is_ceiling_exceeded()
        return False


def check_ceiling() -> Optional[RSSCeilingExceeded]:
    """
    Check if RSS ceiling was exceeded and return exception for main thread to raise.
    
    Call this at safe points (node boundaries) to detect ceiling breach.
    The sampler thread sets a flag when ceiling is exceeded; this function
    returns an exception that the main thread can then raise.
    
    Returns:
        RSSCeilingExceeded exception if ceiling was breached, None otherwise
    """
    with _sampler_lock:
        if _active_sampler:
            return _active_sampler.check_ceiling()
        return None


def get_memory_summary() -> Optional[Dict[str, Any]]:
    """
    Get a summary of memory usage by node.
    
    Returns:
        Dict with per-node memory stats and overall peak info, or None
    """
    with _sampler_lock:
        if _active_sampler:
            return _active_sampler.get_summary()
        return None


@contextmanager
def memory_sampler_context(
    run_id: str,
    artifacts_dir: Optional[str] = None,
    ceiling_callback: Optional[Callable[[RSSCeilingExceeded], None]] = None,
):
    """
    Context manager for memory sampling during a workflow.
    
    Usage:
        with memory_sampler_context(run_id, artifacts_dir) as sampler:
            # Workflow runs here
            set_current_node("ingest_spec")
            ...
    """
    sampler = start_memory_sampler(run_id, artifacts_dir, ceiling_callback)
    try:
        yield sampler
    finally:
        stop_memory_sampler()


# =============================================================================
# In-Loop Ceiling Check (for long-running loops)
# =============================================================================

def check_ceiling_in_loop(iteration: int = 0, check_interval: int = 10) -> None:
    """
    Check memory ceiling inside long-running loops.
    
    This is a lightweight check designed to be called frequently in loops.
    Only actually checks every `check_interval` iterations to minimize overhead.
    
    IMPORTANT: Node boundaries alone can miss runaway allocations inside a
    single long node. This function should be called in:
    - embed_spec_chunks chunk iteration loop
    - Batching loops in persistence/embedding
    - Retry loops in LLM orchestration
    
    Args:
        iteration: Current loop iteration (used for interval check)
        check_interval: Only check every N iterations (default 10)
        
    Raises:
        RSSCeilingExceeded: If ceiling was breached
        
    Usage:
        for i, chunk in enumerate(chunks):
            check_ceiling_in_loop(i)  # Raises if ceiling exceeded
            process(chunk)
    """
    # Skip most iterations for efficiency
    if iteration % check_interval != 0:
        return
    
    exc = check_ceiling()
    if exc is not None:
        raise exc


def check_ceiling_or_raise() -> None:
    """
    Unconditionally check memory ceiling and raise if exceeded.
    
    Use this at critical points where you always want to check,
    unlike check_ceiling_in_loop which only checks periodically.
    
    Raises:
        RSSCeilingExceeded: If ceiling was breached
    """
    exc = check_ceiling()
    if exc is not None:
        raise exc


# Ensure sampler is stopped on process exit
@atexit.register
def _cleanup_sampler():
    """Clean up sampler on process exit."""
    stop_memory_sampler()
