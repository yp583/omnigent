"""Process-local serialization for session runner/host affinity mutations.

The database CAS operations remain authoritative across replicas. This lock
closes multi-step mutation/cleanup gaps on one server process; host-routed
traffic is expected to reach the replica that owns the host tunnel. Locks are
weakly held so inactive session ids do not accumulate for the server lifetime.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_AFFINITY_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


@asynccontextmanager
async def serialized_session_affinity_mutation(session_id: str) -> AsyncIterator[None]:
    """Serialize one session's affinity mutation within this process."""
    lock = _AFFINITY_LOCKS.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _AFFINITY_LOCKS[session_id] = lock
    async with lock:
        yield
