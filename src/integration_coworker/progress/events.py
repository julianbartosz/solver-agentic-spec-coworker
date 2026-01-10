"""Progress event definitions.

This module defines the immutable ProgressEvent dataclass that represents
workflow execution progress. Events are designed to be:

1. Serializable to JSON for logging/SSE
2. Immutable (frozen=True) for thread safety
3. Self-contained with all context needed by subscribers
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Literal, get_args
import json

# Type alias for valid progress event types
ProgressEventType = Literal[
    "workflow.start",
    "workflow.end",
    "workflow.error",
    "node.start",
    "node.end",
    "node.skip",
    "node.error",
    "node.timeout",
]

# Tuple of all valid event types for validation
PROGRESS_EVENT_TYPES: tuple[str, ...] = get_args(ProgressEventType)


@dataclass(frozen=True)
class ProgressEvent:
    """Immutable progress event for streaming to subscribers.

    Attributes:
        run_id: Unique identifier for the workflow run.
        event_type: Type of progress event (workflow.* or node.*).
        timestamp: ISO 8601 UTC timestamp when event occurred.
        node_name: Name of the node (None for workflow-level events).
        node_index: 0-based position in workflow order (None for workflow events).
        total_nodes: Total number of nodes in the workflow.
        progress_pct: Progress percentage as float 0.0-1.0.
        duration_ms: Duration in milliseconds (set on end/error events).
        message: Human-readable message describing the event.
        metadata: Additional context-specific data.
        error: Error message if event_type is *.error.

    Example:
        >>> event = ProgressEvent.create(
        ...     run_id="abc-123",
        ...     event_type="node.start",
        ...     node_name="fetch_spec",
        ...     node_index=2,
        ...     total_nodes=15,
        ... )
        >>> event.progress_pct
        0.133...
        >>> event.to_dict()["event_type"]
        'node.start'
    """

    run_id: str
    event_type: ProgressEventType
    timestamp: str  # ISO 8601 UTC
    node_name: Optional[str] = None
    node_index: Optional[int] = None
    total_nodes: int = 0
    progress_pct: float = 0.0
    duration_ms: Optional[float] = None
    message: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = field(default=None, hash=False)
    error: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate event type after initialization."""
        if self.event_type not in PROGRESS_EVENT_TYPES:
            raise ValueError(
                f"Invalid event_type: {self.event_type}. "
                f"Must be one of: {PROGRESS_EVENT_TYPES}"
            )

    @classmethod
    def create(
        cls,
        run_id: str,
        event_type: ProgressEventType,
        *,
        node_name: Optional[str] = None,
        node_index: Optional[int] = None,
        total_nodes: int = 0,
        duration_ms: Optional[float] = None,
        message: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> "ProgressEvent":
        """Factory method to create a ProgressEvent with computed fields.

        Automatically sets timestamp and calculates progress_pct based on
        node_index and total_nodes.

        Args:
            run_id: Unique workflow run identifier.
            event_type: Type of progress event.
            node_name: Name of node (for node.* events).
            node_index: 0-based index of current node.
            total_nodes: Total nodes in workflow.
            duration_ms: Duration for end/error events.
            message: Human-readable description.
            metadata: Additional context data.
            error: Error message for error events.

        Returns:
            A new immutable ProgressEvent instance.
        """
        timestamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")

        # Calculate progress percentage
        progress_pct = 0.0
        if total_nodes > 0 and node_index is not None:
            # For start events, progress is at the beginning of the node
            # For end events, progress is at the end of the node
            if event_type.endswith(".end"):
                progress_pct = (node_index + 1) / total_nodes
            else:
                progress_pct = node_index / total_nodes

        # Clamp to [0.0, 1.0]
        progress_pct = max(0.0, min(1.0, progress_pct))

        # Generate default message if not provided
        if message is None:
            message = cls._generate_default_message(event_type, node_name)

        return cls(
            run_id=run_id,
            event_type=event_type,
            timestamp=timestamp,
            node_name=node_name,
            node_index=node_index,
            total_nodes=total_nodes,
            progress_pct=progress_pct,
            duration_ms=duration_ms,
            message=message,
            metadata=metadata,
            error=error,
        )

    @staticmethod
    def _generate_default_message(
        event_type: ProgressEventType, node_name: Optional[str]
    ) -> str:
        """Generate a human-readable default message."""
        if event_type == "workflow.start":
            return "Workflow started"
        elif event_type == "workflow.end":
            return "Workflow completed"
        elif event_type == "workflow.error":
            return "Workflow failed"
        elif node_name:
            action = event_type.split(".")[-1]  # start, end, skip, error, timeout
            return f"Node '{node_name}' {action}"
        return event_type

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary, excluding None values.

        Returns:
            Dictionary representation suitable for JSON serialization.
        """
        d = asdict(self)
        # Remove None values for cleaner JSON
        return {k: v for k, v in d.items() if v is not None}

    def to_json(self, indent: Optional[int] = None) -> str:
        """Serialize event to JSON string.

        Args:
            indent: Number of spaces for indentation (None for compact).

        Returns:
            JSON string representation of the event.
        """
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ProgressEvent":
        """Create a ProgressEvent from a dictionary.

        Args:
            data: Dictionary with event fields.

        Returns:
            ProgressEvent instance.

        Raises:
            TypeError: If required fields are missing.
        """
        return cls(**data)

    @classmethod
    def from_json(cls, json_str: str) -> "ProgressEvent":
        """Create a ProgressEvent from a JSON string.

        Args:
            json_str: JSON string representation.

        Returns:
            ProgressEvent instance.
        """
        return cls.from_dict(json.loads(json_str))
