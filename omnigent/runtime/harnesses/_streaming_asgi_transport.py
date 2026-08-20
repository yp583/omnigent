"""Streaming HTTPX transport for an in-process ASGI harness."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable

import httpx
from starlette.types import ASGIApp, Message, Scope


class _StreamingResponseStream(httpx.AsyncByteStream):
    """Queue-backed response body that preserves ASGI streaming."""

    def __init__(
        self,
        queue: asyncio.Queue[bytes | BaseException | None],
        app_task: asyncio.Task[None],
        disconnect: asyncio.Event,
        on_close: Callable[[_StreamingResponseStream], None],
    ) -> None:
        self._queue = queue
        self._app_task = app_task
        self._disconnect = disconnect
        self._on_close = on_close
        self._closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._disconnect.set()
        if not self._app_task.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._app_task), timeout=1.0)
            except asyncio.TimeoutError:
                self._app_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._app_task
            except Exception:
                # A stream consumer observes application errors through the
                # queue. Teardown only needs to ensure the task is reaped.
                pass
        self._on_close(self)


class StreamingASGITransport(httpx.AsyncBaseTransport):
    """Call an ASGI app directly while retaining SSE backpressure.

    HTTPX's stock ``ASGITransport`` buffers the complete response before
    returning it. Harness responses are long-lived SSE streams and must be
    consumed while the app is still running so tool results, steering, and
    cancellation can travel on concurrent requests.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        raise_app_exceptions: bool = False,
        queue_capacity: int = 16,
    ) -> None:
        self._app = app
        self._raise_app_exceptions = raise_app_exceptions
        self._queue_capacity = max(1, queue_capacity)
        self._streams: set[_StreamingResponseStream] = set()
        self._closed = False

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self._closed:
            raise RuntimeError("in-process harness transport is closed")
        if not isinstance(request.stream, httpx.AsyncByteStream):
            raise TypeError("StreamingASGITransport requires an async request stream")

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(key.lower(), value) for key, value in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": ("127.0.0.1", 0),
            "root_path": "",
        }
        request_chunks = request.stream.__aiter__()
        request_complete = False
        response_started = asyncio.Event()
        response_complete = asyncio.Event()
        disconnect = asyncio.Event()
        body_queue: asyncio.Queue[bytes | BaseException | None] = asyncio.Queue(
            maxsize=self._queue_capacity
        )
        status_code: int | None = None
        response_headers: list[tuple[bytes, bytes]] | None = None
        terminal_sent = False

        async def receive() -> Message:
            nonlocal request_complete
            if request_complete:
                await disconnect.wait()
                return {"type": "http.disconnect"}
            try:
                body = await request_chunks.__anext__()
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": body, "more_body": True}

        async def send(message: Message) -> None:
            nonlocal status_code, response_headers, terminal_sent
            message_type = message["type"]
            if message_type == "http.response.start":
                if response_started.is_set():
                    raise RuntimeError("ASGI app sent two response starts")
                status_code = int(message["status"])
                response_headers = list(message.get("headers", []))
                response_started.set()
                return
            if message_type != "http.response.body":
                return
            body = message.get("body", b"")
            if body and request.method != "HEAD":
                await body_queue.put(body)
            if not message.get("more_body", False) and not terminal_sent:
                terminal_sent = True
                response_complete.set()
                await body_queue.put(None)

        async def run_app() -> None:
            nonlocal status_code, response_headers, terminal_sent
            try:
                await self._app(scope, receive, send)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                if status_code is None:
                    status_code = 500
                    response_headers = []
                    response_started.set()
                if self._raise_app_exceptions:
                    await body_queue.put(exc)
            finally:
                response_complete.set()
                if status_code is None:
                    status_code = 500
                    response_headers = []
                    response_started.set()
                if not terminal_sent:
                    terminal_sent = True
                    await body_queue.put(None)

        app_task = asyncio.create_task(run_app(), name="in-process-harness-request")
        await response_started.wait()
        assert status_code is not None
        assert response_headers is not None
        stream = _StreamingResponseStream(
            body_queue,
            app_task,
            disconnect,
            self._streams.discard,
        )
        self._streams.add(stream)
        return httpx.Response(status_code, headers=response_headers, stream=stream)

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._streams:
            await asyncio.gather(
                *(stream.aclose() for stream in tuple(self._streams)),
                return_exceptions=True,
            )
        self._streams.clear()
