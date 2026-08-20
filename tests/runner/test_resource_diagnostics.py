"""Tests for payload-free runner resource ownership diagnostics."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from omnigent.runner import create_runner_app
from omnigent.runner.app import _session_event_queues_ref, _session_histories_ref
from omnigent.runner.resource_diagnostics import (
    build_runner_resource_diagnostics,
    retained_size,
)
from tests.runner.helpers import NullServerClient


def test_retained_size_handles_cycles_and_bounds_work() -> None:
    cyclic: list[object] = ["payload"]
    cyclic.append(cyclic)
    complete = retained_size(cyclic)
    bounded = retained_size(list(range(100)), max_nodes=10)

    assert complete.complete
    assert complete.nodes == 2
    assert complete.bytes > 0
    assert not bounded.complete
    assert bounded.nodes == 10


def test_diagnostics_groups_sessions_by_root_without_payloads() -> None:
    events: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=16)
    events.put_nowait({"type": "secret", "text": "must-not-leak"})
    inbox: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    inbox.put_nowait({"output": "also-secret"})
    result = build_runner_resource_diagnostics(
        histories={"child": [{"role": "assistant", "content": "private"}]},
        event_queues={"child": events},
        inboxes={"child": inbox},
        message_buffers={"root": [{"content": "buffered-private"}]},
        async_tasks={"child": {"task": object()}},
        timers={},
        active_turns={"root": object()},
        root_session_ids={"root": "root", "child": "root"},
        process_manager={"resident_count": 2},
    )

    assert result["session_count"] == 2
    assert result["root_count"] == 1
    assert result["active_turn_count"] == 1
    root = result["roots"][0]  # type: ignore[index]
    assert root["sessions"] == 2
    child = next(
        row
        for row in result["top_sessions"]
        if row["session_id"] == "child"  # type: ignore[union-attr]
    )
    assert child["event_queue_items"] == 1
    assert child["event_queue_max_items"] == 16
    assert child["inbox_items"] == 1
    assert "private" not in repr(result)
    assert "secret" not in repr(result)


@pytest.mark.asyncio
async def test_runner_diagnostics_route_reports_session_ownership() -> None:
    session_id = "diagnostics-route-session"
    _session_histories_ref[session_id] = [{"role": "assistant", "content": "hidden"}]
    queue: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()
    queue.put_nowait({"type": "session.status", "status": "idle"})
    _session_event_queues_ref[session_id] = queue
    try:
        app = create_runner_app(server_client=NullServerClient())  # type: ignore[arg-type]
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://runner") as client:
            response = await client.get("/v1/diagnostics/resources")

        assert response.status_code == 200
        body = response.json()
        row = next(item for item in body["top_sessions"] if item["session_id"] == session_id)
        assert row["history_items"] == 1
        assert row["event_queue_items"] == 1
        assert "hidden" not in response.text
    finally:
        _session_histories_ref.pop(session_id, None)
        _session_event_queues_ref.pop(session_id, None)


def test_hibernation_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_HARNESS_CACHE_HIBERNATION", raising=False)
    session_id = "diagnostics-default-hibernation-session"
    app = create_runner_app(server_client=NullServerClient())  # type: ignore[arg-type]
    _session_histories_ref[session_id] = [{"role": "assistant", "content": "large"}]
    try:
        app.state.hibernate_session_caches(session_id)
        assert session_id in _session_histories_ref
    finally:
        _session_histories_ref.pop(session_id, None)


def test_hibernation_drops_history_only_after_live_work_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIGENT_HARNESS_CACHE_HIBERNATION", "true")
    session_id = "diagnostics-hibernation-session"
    app = create_runner_app(server_client=NullServerClient())  # type: ignore[arg-type]
    _session_histories_ref[session_id] = [{"role": "assistant", "content": "large"}]
    app.state.active_turns[session_id] = None
    try:
        app.state.hibernate_session_caches(session_id)
        assert session_id in _session_histories_ref

        app.state.active_turns.pop(session_id)
        app.state.hibernate_session_caches(session_id)
        assert session_id not in _session_histories_ref
        snapshot = app.state.resource_diagnostics()
        row = next(item for item in snapshot["top_sessions"] if item["session_id"] == session_id)
        assert row["hibernated"] is True
        assert row["estimated_python_bytes"] == 0
    finally:
        app.state.active_turns.pop(session_id, None)
        _session_histories_ref.pop(session_id, None)


def test_event_queue_is_unbounded_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OMNIGENT_SESSION_EVENT_QUEUE_MAX_ITEMS", raising=False)
    session_id = "diagnostics-unbounded-session"
    app = create_runner_app(server_client=NullServerClient())  # type: ignore[arg-type]
    try:
        for _ in range(4097):
            app.state.publish_event(session_id, {"type": "response.output_text.delta"})

        queue = _session_event_queues_ref[session_id]
        assert queue.maxsize == 0
        assert queue.qsize() == 4097
    finally:
        app.state.active_turns.pop(session_id, None)
        _session_event_queues_ref.pop(session_id, None)


def test_event_queue_overflow_is_bounded_and_explicit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OMNIGENT_SESSION_EVENT_QUEUE_MAX_ITEMS", "4096")
    session_id = "diagnostics-overflow-session"
    app = create_runner_app(server_client=NullServerClient())  # type: ignore[arg-type]
    try:
        for _ in range(4097):
            app.state.publish_event(session_id, {"type": "response.output_text.delta"})

        queue = _session_event_queues_ref[session_id]
        assert queue.qsize() == 1
        failure = queue.get_nowait()
        assert failure is not None
        assert failure["status"] == "failed"
        assert failure["error"]["code"] == "runner_event_queue_overflow"  # type: ignore[index]

        app.state.publish_event(session_id, {"type": "ignored-after-overflow"})
        assert queue.empty()
        app.state.begin_turn_slot(session_id)
        app.state.publish_event(session_id, {"type": "accepted-next-turn"})
        assert queue.qsize() == 1
    finally:
        app.state.active_turns.pop(session_id, None)
        _session_event_queues_ref.pop(session_id, None)
