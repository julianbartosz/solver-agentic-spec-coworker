"""Built-in progress callbacks for common use cases.

This module provides ready-to-use callbacks for:
- Rich progress bar display in CLI
- File-based event logging
- Event collection for testing
"""

from pathlib import Path
from typing import List, Optional, TextIO
import json
import sys
import os

from integration_coworker.progress.events import ProgressEvent

# Conditional import for Rich (optional dependency for CLI display)
try:
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        TaskProgressColumn,
        TimeElapsedColumn,
        MofNCompleteColumn,
    )
    from rich.console import Console

    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    Progress = None  # type: ignore


class RichProgressCallback:
    """Rich-based progress bar callback for terminal display.

    This callback displays a live progress bar in the terminal using Rich.
    It tracks workflow progress and updates the display on each event.

    Example:
        >>> from integration_coworker.progress import ProgressEmitter
        >>> emitter = ProgressEmitter()
        >>> with RichProgressCallback() as callback:
        ...     emitter.register(callback)
        ...     # ... workflow runs ...
    """

    def __init__(
        self,
        console: Optional["Console"] = None,
        transient: bool = True,
        disable: bool = False,
    ) -> None:
        """Initialize the Rich progress callback.

        Args:
            console: Rich Console instance (creates new if None).
            transient: If True, progress bar disappears after completion.
            disable: If True, no output is shown (useful for non-TTY).

        Raises:
            ImportError: If Rich is not installed.
        """
        if not RICH_AVAILABLE:
            raise ImportError(
                "Rich is required for RichProgressCallback. "
                "Install with: pip install rich"
            )

        # Check if we should auto-disable (non-TTY or env var)
        if disable or os.getenv("PROGRESS_RICH_ENABLED", "true").lower() == "false":
            self._disabled = True
            self._progress = None
            self._task_id = None
            return

        self._disabled = False
        self._console = console or Console(stderr=True)
        self._transient = transient

        # Progress bar with custom columns
        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self._console,
            transient=transient,
            disable=not self._console.is_terminal,
        )
        self._task_id: Optional[int] = None
        self._started = False

    def __call__(self, event: ProgressEvent) -> None:
        """Handle a progress event by updating the progress bar.

        Args:
            event: The progress event to display.
        """
        if self._disabled or self._progress is None:
            return

        # Start progress on first event
        if not self._started:
            self._progress.start()
            self._started = True

        # Handle workflow-level events
        if event.event_type == "workflow.start":
            self._task_id = self._progress.add_task(
                description="Starting workflow...",
                total=event.total_nodes or 100,
            )
            return

        if event.event_type in ("workflow.end", "workflow.error"):
            if self._task_id is not None:
                if event.event_type == "workflow.error":
                    self._progress.update(
                        self._task_id,
                        description=f"[red]✗ Failed: {event.error or 'Unknown error'}[/red]",
                    )
                else:
                    self._progress.update(
                        self._task_id,
                        completed=event.total_nodes,
                        description="[green]✓ Workflow completed[/green]",
                    )
            return

        # Handle node-level events
        if self._task_id is None:
            # Create task if we missed workflow.start
            self._task_id = self._progress.add_task(
                description="Running...",
                total=event.total_nodes or 100,
            )

        if event.event_type == "node.start":
            self._progress.update(
                self._task_id,
                description=f"Running: {event.node_name or 'node'}",
                completed=event.node_index or 0,
            )
        elif event.event_type == "node.end":
            completed = (event.node_index or 0) + 1
            self._progress.update(
                self._task_id,
                completed=completed,
            )
        elif event.event_type == "node.skip":
            self._progress.update(
                self._task_id,
                description=f"[yellow]Skipped: {event.node_name}[/yellow]",
            )
        elif event.event_type in ("node.error", "node.timeout"):
            self._progress.update(
                self._task_id,
                description=f"[red]✗ {event.node_name}: {event.error or 'failed'}[/red]",
            )

    def __enter__(self) -> "RichProgressCallback":
        """Context manager entry."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit - stops the progress bar."""
        self.stop()

    def stop(self) -> None:
        """Stop the progress bar display."""
        if self._progress is not None and self._started:
            self._progress.stop()
            self._started = False


class FileProgressCallback:
    """Write progress events to a file as JSONL.

    This callback appends each event as a JSON line to a file,
    useful for logging and post-hoc analysis.

    Example:
        >>> callback = FileProgressCallback("/tmp/progress.jsonl")
        >>> callback(event)
        >>> callback.close()
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        file: Optional[TextIO] = None,
        flush: bool = True,
    ) -> None:
        """Initialize the file callback.

        Args:
            path: Path to write events to (creates/appends).
            file: Open file handle to write to (alternative to path).
            flush: If True, flush after each write.

        Raises:
            ValueError: If neither path nor file is provided.
        """
        if path is None and file is None:
            raise ValueError("Either path or file must be provided")

        self._path = Path(path) if path else None
        self._file: Optional[TextIO] = file
        self._flush = flush
        self._owns_file = path is not None

        if self._path:
            self._file = open(self._path, "a", encoding="utf-8")

    def __call__(self, event: ProgressEvent) -> None:
        """Write event to file as JSON line.

        Args:
            event: The progress event to write.
        """
        if self._file is None:
            return

        line = event.to_json() + "\n"
        self._file.write(line)

        if self._flush:
            self._file.flush()

    def close(self) -> None:
        """Close the file if we own it."""
        if self._owns_file and self._file is not None:
            self._file.close()
            self._file = None

    def __enter__(self) -> "FileProgressCallback":
        """Context manager entry."""
        return self

    def __exit__(self, *args) -> None:
        """Context manager exit."""
        self.close()


class CollectingCallback:
    """Collect events into a list for testing.

    This callback stores all received events in memory,
    useful for unit tests and assertions.

    Example:
        >>> callback = CollectingCallback()
        >>> emitter.register(callback)
        >>> # ... run workflow ...
        >>> assert len(callback.events) > 0
        >>> assert callback.events[0].event_type == "workflow.start"
    """

    def __init__(self, max_events: int = 10000) -> None:
        """Initialize the collecting callback.

        Args:
            max_events: Maximum events to store (oldest dropped when exceeded).
        """
        self._events: List[ProgressEvent] = []
        self._max_events = max_events

    def __call__(self, event: ProgressEvent) -> None:
        """Collect an event.

        Args:
            event: The progress event to store.
        """
        self._events.append(event)

        # Drop oldest events if limit exceeded
        if len(self._events) > self._max_events:
            self._events = self._events[-self._max_events :]

    @property
    def events(self) -> List[ProgressEvent]:
        """Get all collected events."""
        return list(self._events)

    def clear(self) -> None:
        """Clear all collected events."""
        self._events.clear()

    def get_by_type(self, event_type: str) -> List[ProgressEvent]:
        """Get events filtered by type.

        Args:
            event_type: Event type to filter by.

        Returns:
            List of matching events.
        """
        return [e for e in self._events if e.event_type == event_type]

    def get_by_node(self, node_name: str) -> List[ProgressEvent]:
        """Get events filtered by node name.

        Args:
            node_name: Node name to filter by.

        Returns:
            List of matching events.
        """
        return [e for e in self._events if e.node_name == node_name]


def create_default_callbacks(
    enable_rich: bool = True,
    enable_file: bool = False,
    file_path: Optional[Path] = None,
) -> List:
    """Create a list of default callbacks based on configuration.

    Args:
        enable_rich: Enable Rich progress bar if in TTY.
        enable_file: Enable file logging.
        file_path: Path for file logging.

    Returns:
        List of callback instances ready for registration.
    """
    callbacks = []

    # Rich progress bar (if available and enabled)
    if enable_rich and RICH_AVAILABLE:
        # Only enable if we're in a TTY
        if sys.stderr.isatty():
            try:
                callbacks.append(RichProgressCallback())
            except Exception:
                pass  # Skip if Rich fails to initialize

    # File logging
    if enable_file and file_path:
        callbacks.append(FileProgressCallback(path=file_path))

    return callbacks
