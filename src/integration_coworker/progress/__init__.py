"""Progress event streaming infrastructure.

This module provides real-time workflow progress visibility through
a callback-based event system.

Example usage:

    from integration_coworker.progress import ProgressEmitter, ProgressEvent

    emitter = ProgressEmitter()

    def my_callback(event: ProgressEvent) -> None:
        print(f"Progress: {event.progress_pct:.0%} - {event.node_name}")

    emitter.register(my_callback)

    # During workflow execution, emitter.emit() is called automatically
    # by the graph runtime when progress_emitter is set in context
"""

from integration_coworker.progress.events import (
    ProgressEvent,
    ProgressEventType,
    PROGRESS_EVENT_TYPES,
)
from integration_coworker.progress.emitter import (
    ProgressEmitter,
    ProgressCallback,
    ProgressCallbackFn,
    get_progress_emitter,
    set_progress_emitter,
)

__all__ = [
    # Event types
    "ProgressEvent",
    "ProgressEventType",
    "PROGRESS_EVENT_TYPES",
    # Emitter
    "ProgressEmitter",
    "ProgressCallback",
    "ProgressCallbackFn",
    # Context accessors
    "get_progress_emitter",
    "set_progress_emitter",
]
