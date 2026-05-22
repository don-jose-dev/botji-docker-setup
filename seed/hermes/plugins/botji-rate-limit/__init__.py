"""botji-rate-limit plugin: per-user message throttle via pre_gateway_dispatch.

In-memory sliding-window counter per Telegram user. Configurable via env:
``BOTJI_RATE_LIMIT_PER_MIN`` (default 30), ``BOTJI_RATE_LIMIT_PER_HOUR``
(default 200).

Fail-open: any error in the limiter is swallowed and the message is allowed
through. Operators tune the threshold at deploy time per tenant; defaults are
generous enough that legitimate users never hit them.

The hook returns ``{"action": "skip", "reason": "..."}`` when blocking, which
matches the ``botji-allowlist`` contract — Hermes drops the message silently.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from threading import RLock
from typing import Any

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


class _RateLimiter:
    """Sliding-window per-user counter for per-minute and per-hour thresholds."""

    def __init__(self, per_min: int, per_hour: int) -> None:
        self._per_min = per_min
        self._per_hour = per_hour
        self._minute_windows: dict[str, deque[float]] = defaultdict(deque)
        self._hour_windows: dict[str, deque[float]] = defaultdict(deque)
        self._lock = RLock()

    @staticmethod
    def _prune(window: "deque[float]", horizon: float, now: float) -> None:
        while window and window[0] < now - horizon:
            window.popleft()

    def check(self, user_id: str) -> tuple[bool, str | None]:
        """Record a call and return (allowed, reason_if_blocked)."""
        now = time.time()
        with self._lock:
            mw = self._minute_windows[user_id]
            hw = self._hour_windows[user_id]
            self._prune(mw, 60.0, now)
            self._prune(hw, 3600.0, now)
            if len(mw) >= self._per_min:
                return False, f"rate_limit_per_minute ({self._per_min}/min)"
            if len(hw) >= self._per_hour:
                return False, f"rate_limit_per_hour ({self._per_hour}/hour)"
            mw.append(now)
            hw.append(now)
            return True, None


_limiter: _RateLimiter | None = None


def _limiter_get() -> _RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = _RateLimiter(
            _int_env("BOTJI_RATE_LIMIT_PER_MIN", 30),
            _int_env("BOTJI_RATE_LIMIT_PER_HOUR", 200),
        )
    return _limiter


def _pre_gateway_dispatch(event: Any, **_: Any) -> dict[str, Any] | None:
    """Block over-quota Telegram users. Fail-open on any error."""
    try:
        platform = getattr(event, "platform", None) or getattr(event, "source", None)
        if platform != "telegram":
            return None
        sender = (
            getattr(event, "sender_id", None)
            or getattr(event, "user_id", None)
            or getattr(event, "from_id", None)
        )
        if sender is None:
            return None
        allowed, reason = _limiter_get().check(str(sender))
        if not allowed:
            logger.info("rate-limit: blocked %s (%s)", sender, reason)
            return {"action": "skip", "reason": reason}
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("rate-limit: fail-open due to error: %s", exc)
        return None


def register(ctx) -> None:
    """Plugin entry point."""
    ctx.register_hook("pre_gateway_dispatch", _pre_gateway_dispatch)
    limiter = _limiter_get()
    logger.info(
        "botji-rate-limit: registered (per_min=%d, per_hour=%d)",
        limiter._per_min,
        limiter._per_hour,
    )
