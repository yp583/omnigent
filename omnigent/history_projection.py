"""Harness-neutral projection of compacted conversation history.

Native transcript rebuilds do not all share the SDK history loader.  Keeping
the compaction boundary logic here prevents one rebuild path from replaying the
entire pre-compaction transcript while another correctly uses the summary.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def compaction_replacement_messages(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return the compacted native messages, or a portable summary exchange.

    Some native forwarders can persist their exact replacement history in
    ``compacted_messages``.  Older and SDK-produced compactions only have a
    textual summary; represent that as a small user/assistant exchange so every
    harness still receives bounded, useful context.
    """
    snapshot = item.get("compacted_messages")
    if isinstance(snapshot, list):
        messages = [dict(value) for value in snapshot if isinstance(value, Mapping)]
        if messages:
            return messages

    summary = item.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return []
    return [
        {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Continue the conversation using the compacted summary.",
                }
            ],
        },
        {
            "type": "message",
            "role": "assistant",
            "content": [{"type": "output_text", "text": summary.strip()}],
        },
    ]


def expand_latest_compaction(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace history through the latest usable compaction with its baseline.

    The compaction row is appended after the history it supersedes.  Therefore
    its position is also the only safe compatibility boundary for an older
    fork whose ``last_item_id`` still names an item in the source session.
    """
    latest_index: int | None = None
    replacement: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if item.get("type") != "compaction":
            continue
        candidate = compaction_replacement_messages(item)
        if candidate:
            latest_index = index
            replacement = candidate
    if latest_index is None:
        return items
    return [*replacement, *items[latest_index + 1 :]]


__all__ = ["compaction_replacement_messages", "expand_latest_compaction"]
