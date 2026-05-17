"""Per-user fair-share queue for the gpt-image-2 provider slot.

When multiple users share a single upstream OAuth (our setup), naive FIFO
queueing lets one heavy user starve others. The pattern adopted here is
Temporal's weighted round-robin keyed on ``fairness_key`` (= user_id):
even if user A has 100 queued requests, user B's first request is
serviced before A's second.

The queue is in-memory per process. botji runs as a single container so
this is sufficient. A multi-container deployment would move this to
SQLite (the lease store already exists) or Redis with the same public
surface — :func:`enqueue_and_wait` is the only call site.

Concurrency control: a single semaphore caps the number of in-flight
provider calls (default 1, since one OAuth = one IPM bucket realistically
for gpt-image-2 at our tier). The semaphore is released by the ``with``
block in :func:`reserve_slot`.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# One concurrent gpt-image-2 call. Bump only if the OAuth tier permits more.
_DEFAULT_CONCURRENCY = int(os.environ.get("BOTJI_RENDER_CONCURRENCY", "1"))


class _FairShareScheduler:
    """Weighted round-robin scheduler keyed on user_id.

    Maintains a deque of (user_id, future) waiters. Each release picks the
    user whose last admitted request is oldest — ensures fair turn-taking
    without per-user queues that would need cleanup.

    Thread-safe via an asyncio.Lock; safe to call from multiple sessions
    in the same event loop.
    """

    def __init__(self, concurrency: int = _DEFAULT_CONCURRENCY):
        self._sem = asyncio.Semaphore(concurrency)
        self._waiters: collections.deque[tuple[str, asyncio.Future[None]]] = collections.deque()
        self._last_admitted_at: dict[str, float] = {}
        self._concurrency = concurrency
        self._lock = asyncio.Lock()

    async def acquire(self, user_id: str) -> None:
        """Wait for a turn under fair-share semantics.

        First-come-first-served *within* a user; weighted round-robin
        *across* users. Updates :attr:`_last_admitted_at` on entry so the
        next admission cycle naturally prefers users who haven't gone
        recently.
        """
        async with self._lock:
            if self._sem._value > 0 and not self._waiters:
                await self._sem.acquire()
                self._last_admitted_at[user_id] = time.time()
                return
            # Capacity full — get in line.
            waiter: asyncio.Future[None] = asyncio.get_event_loop().create_future()
            self._waiters.append((user_id, waiter))
        await waiter

    async def release(self) -> None:
        """Admit the next-fair user; called from :func:`reserve_slot` exit."""
        async with self._lock:
            self._sem.release()
            if not self._waiters:
                return
            # Pick the waiter whose user was admitted longest ago (or never).
            best_idx = 0
            best_age = float("inf")
            for idx, (uid, _fut) in enumerate(self._waiters):
                last = self._last_admitted_at.get(uid, 0.0)
                if last < best_age:
                    best_age = last
                    best_idx = idx
            uid, fut = self._waiters[best_idx]
            del self._waiters[best_idx]
            self._last_admitted_at[uid] = time.time()
            await self._sem.acquire()  # consume the slot we just released, hand it to the waiter
            fut.set_result(None)

    def depth(self) -> int:
        """Current number of waiters (for observability / metrics)."""
        return len(self._waiters)


_scheduler_lock = threading.Lock()
_scheduler: _FairShareScheduler | None = None


def _get_scheduler() -> _FairShareScheduler:
    """Lazy singleton — scheduler is created on first reserve_slot call.

    Lazy because the event loop may not exist at import time (the plugin
    is imported during plugin discovery, before the gateway boots).
    """
    global _scheduler
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = _FairShareScheduler()
    return _scheduler


@asynccontextmanager
async def reserve_slot(user_id: str) -> AsyncIterator[float]:
    """Reserve a provider slot under fair-share scheduling.

    Yields the wait time in seconds (callers may surface it as
    ``queue_wait_ms`` in metrics or in a "queued, ~Xs" user message).

    Usage::

        async with reserve_slot(user_id) as wait_seconds:
            result = await call_gpt_image_2(...)
    """
    sched = _get_scheduler()
    enq = time.monotonic()
    await sched.acquire(user_id)
    wait_seconds = time.monotonic() - enq
    if wait_seconds > 1.0:
        logger.info("render queue: user=%s waited=%.1fs depth=%d", user_id, wait_seconds, sched.depth())
    try:
        yield wait_seconds
    finally:
        await sched.release()


def queue_depth() -> int:
    """Current queue depth (for metrics)."""
    sched = _scheduler
    return sched.depth() if sched is not None else 0


__all__ = ["reserve_slot", "queue_depth"]
