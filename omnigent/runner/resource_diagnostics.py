"""Bounded, payload-free runner memory diagnostics.

The runner keeps several per-session caches and queues. This module reports
their ownership and approximate retained Python heap without serializing or
returning user content. It is intentionally cheap enough to query while a
large agent tree is live.
"""

from __future__ import annotations

import sys
from collections import deque
from collections.abc import Collection, Mapping
from dataclasses import dataclass

_DEFAULT_MAX_NODES = 100_000
_DEFAULT_TOP_SESSIONS = 50


@dataclass(frozen=True, slots=True)
class RetainedSize:
    """Approximate retained Python heap for a JSON-like object graph."""

    bytes: int
    nodes: int
    complete: bool

    def to_dict(self) -> dict[str, object]:
        return {"bytes": self.bytes, "nodes": self.nodes, "complete": self.complete}


def retained_size(value: object, *, max_nodes: int = _DEFAULT_MAX_NODES) -> RetainedSize:
    """Estimate unique shallow sizes in a bounded object-graph traversal."""
    if max_nodes < 1:
        raise ValueError("max_nodes must be positive")
    pending = [value]
    seen: set[int] = set()
    total = 0
    nodes = 0
    while pending and nodes < max_nodes:
        current = pending.pop()
        identity = id(current)
        if identity in seen:
            continue
        seen.add(identity)
        nodes += 1
        total += sys.getsizeof(current)
        if isinstance(current, Mapping):
            pending.extend(current.keys())
            pending.extend(current.values())
        elif isinstance(current, (list, tuple, set, frozenset, deque)):
            pending.extend(current)
    return RetainedSize(bytes=total, nodes=nodes, complete=not pending)


def _collection_length(value: object | None) -> int:
    try:
        return len(value) if value is not None else 0  # type: ignore[arg-type]
    except TypeError:
        return 0


def _queue_size(value: object | None) -> int:
    qsize = getattr(value, "qsize", None)
    if not callable(qsize):
        return 0
    result = qsize()
    return result if isinstance(result, int) else 0


def _queue_max_size(value: object | None) -> int | None:
    result = getattr(value, "maxsize", None)
    return result if isinstance(result, int) else None


def build_runner_resource_diagnostics(
    *,
    histories: Mapping[str, object],
    event_queues: Mapping[str, object],
    inboxes: Mapping[str, object],
    message_buffers: Mapping[str, object],
    async_tasks: Mapping[str, object],
    timers: Mapping[str, object],
    active_turns: Mapping[str, object],
    root_session_ids: Mapping[str, str | None],
    process_manager: Mapping[str, object] | None,
    hibernated_sessions: Collection[str] = (),
    max_nodes_per_value: int = _DEFAULT_MAX_NODES,
    top_sessions: int = _DEFAULT_TOP_SESSIONS,
) -> dict[str, object]:
    """Build a bounded ownership snapshot without exposing retained payloads."""
    if top_sessions < 1:
        raise ValueError("top_sessions must be positive")
    session_ids = set().union(
        histories,
        event_queues,
        inboxes,
        message_buffers,
        async_tasks,
        timers,
        active_turns,
        root_session_ids,
        hibernated_sessions,
    )
    rows: list[dict[str, object]] = []
    roots: dict[str, dict[str, int]] = {}
    total_estimated_bytes = 0
    estimates_complete = True
    for session_id in session_ids:
        history = histories.get(session_id)
        buffers = message_buffers.get(session_id)
        history_size = (
            retained_size(history, max_nodes=max_nodes_per_value)
            if history is not None
            else RetainedSize(bytes=0, nodes=0, complete=True)
        )
        buffer_size = (
            retained_size(buffers, max_nodes=max_nodes_per_value)
            if buffers is not None
            else RetainedSize(bytes=0, nodes=0, complete=True)
        )
        estimated_bytes = history_size.bytes + buffer_size.bytes
        complete = history_size.complete and buffer_size.complete
        total_estimated_bytes += estimated_bytes
        estimates_complete = estimates_complete and complete
        root_id = root_session_ids.get(session_id) or session_id
        root = roots.setdefault(
            root_id,
            {"sessions": 0, "active_turns": 0, "estimated_python_bytes": 0},
        )
        root["sessions"] += 1
        root["active_turns"] += int(session_id in active_turns)
        root["estimated_python_bytes"] += estimated_bytes
        rows.append(
            {
                "session_id": session_id,
                "root_session_id": root_id,
                "active_turn": session_id in active_turns,
                "hibernated": session_id in hibernated_sessions,
                "history_items": _collection_length(history),
                "message_buffer_items": _collection_length(buffers),
                "event_queue_items": _queue_size(event_queues.get(session_id)),
                "event_queue_max_items": _queue_max_size(event_queues.get(session_id)),
                "inbox_items": _queue_size(inboxes.get(session_id)),
                "inbox_max_items": _queue_max_size(inboxes.get(session_id)),
                "async_tasks": _collection_length(async_tasks.get(session_id)),
                "timers": _collection_length(timers.get(session_id)),
                "estimated_python_bytes": estimated_bytes,
                "estimate_complete": complete,
            }
        )
    rows.sort(key=lambda row: (-int(row["estimated_python_bytes"]), str(row["session_id"])))
    root_rows = [
        {"root_session_id": root_id, **values}
        for root_id, values in sorted(
            roots.items(),
            key=lambda item: (-item[1]["estimated_python_bytes"], item[0]),
        )
    ]
    return {
        "session_count": len(rows),
        "root_count": len(root_rows),
        "active_turn_count": len(active_turns),
        "hibernated_session_count": len(hibernated_sessions),
        "estimated_python_bytes": total_estimated_bytes,
        "estimate_complete": estimates_complete,
        "top_sessions": rows[:top_sessions],
        "top_sessions_truncated": len(rows) > top_sessions,
        "roots": root_rows,
        "harness_processes": dict(process_manager) if process_manager is not None else None,
    }
