"""SQLite-backed lease + idempotency store for botji_render.

Solves two failure modes observed in production on 2026-05-17:

1. **Silent stall.** A hung gpt-image-2 stream had no upper bound; the
   user's turn waited indefinitely with no error surface. Every lease has
   a TTL; when the TTL expires the next attempt can proceed regardless
   of whether the original holder ever finishes.

2. **Cross-user preemption.** Two users sharing one Codex OAuth contended
   for the same provider slot; when one user's hung request was abandoned
   the other's results could collide. The composite key
   ``(user_id, request_hash)`` namespaces leases per-user so no caller
   ever overwrites another's outcome.

The store also persists results under the composite key so that when a
hung request finally completes (or a duplicate request fires), the
already-computed result is returned without re-spending an image-gen
quota slot.

The schema is intentionally minimal — single-file SQLite, no migrations,
WAL-mode for concurrent readers. Bumping to Redis later is a drop-in
replacement: the public API (``acquire`` / ``release`` / ``record_result``
/ ``lookup_result``) is store-agnostic.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_DEFAULT_DB = Path(os.environ.get("BOTJI_LEASE_DB", "/opt/data/leases.db"))
_DEFAULT_TTL_SECONDS = 200  # gpt-image-2 hard ceiling is ~180 s; lease > ceiling
_RESULT_RETENTION_SECONDS = 24 * 3600  # keep result entries for 24 h (idempotency window)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS leases (
    user_id      TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    acquired_at  REAL NOT NULL,
    expires_at   REAL NOT NULL,
    holder       TEXT NOT NULL,
    PRIMARY KEY (user_id, request_hash)
);

CREATE TABLE IF NOT EXISTS results (
    user_id      TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    completed_at REAL NOT NULL,
    payload      TEXT NOT NULL,
    PRIMARY KEY (user_id, request_hash)
);

CREATE INDEX IF NOT EXISTS idx_leases_expires ON leases (expires_at);
CREATE INDEX IF NOT EXISTS idx_results_completed ON results (completed_at);
"""

_init_lock = threading.Lock()
_initialized_paths: set[Path] = set()


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection with WAL + safe defaults; initialise the schema once."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    if db_path not in _initialized_paths:
        with _init_lock:
            if db_path not in _initialized_paths:
                conn.executescript(_SCHEMA)
                _initialized_paths.add(db_path)
    return conn


def compute_request_hash(*parts: Any) -> str:
    """Stable hash for idempotency keys.

    Any json-serialisable parts can be passed; lists/dicts are canonicalised
    so the same logical request always hashes the same way.
    """
    canonical = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def lookup_result(
    user_id: str,
    request_hash: str,
    *,
    db_path: Path = _DEFAULT_DB,
) -> dict[str, Any] | None:
    """Return a previously-recorded result if one exists, else None.

    Cheap pre-flight: callers check this before doing any work. Hitting it
    on a duplicate request avoids a provider call entirely.
    """
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT payload, completed_at FROM results WHERE user_id = ? AND request_hash = ?",
            (user_id, request_hash),
        ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["payload"])
        except json.JSONDecodeError:
            logger.warning("lease store: corrupt result payload for (%s, %s)", user_id, request_hash)
            return None


def record_result(
    user_id: str,
    request_hash: str,
    payload: dict[str, Any],
    *,
    db_path: Path = _DEFAULT_DB,
) -> None:
    """Persist a completed result under the idempotency key.

    Replaces any prior entry (last write wins; in practice we only write
    once per (user, hash) — the same key cannot succeed twice).
    """
    with contextlib.closing(_connect(db_path)) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO results (user_id, request_hash, completed_at, payload) "
            "VALUES (?, ?, ?, ?)",
            (user_id, request_hash, time.time(), json.dumps(payload, default=str)),
        )


@contextlib.contextmanager
def acquire(
    user_id: str,
    request_hash: str,
    *,
    holder: str,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    db_path: Path = _DEFAULT_DB,
) -> Iterator[bool]:
    """Acquire a lease and run the protected work in a ``with`` block.

    Returns True if the lease was newly acquired (caller should do the work
    and persist the result via :func:`record_result`). Returns False if
    another holder has a live lease (caller should poll :func:`lookup_result`
    or surface a "queued" message to the user — see :mod:`_queue`).

    The lease is automatically released on context exit so a Python crash
    or uncaught exception cannot leave a stale lock. Expired leases are
    swept eagerly on every acquire attempt to keep the table small.
    """
    acquired = False
    with contextlib.closing(_connect(db_path)) as conn:
        now = time.time()
        # Sweep expired leases first — cheap (indexed on expires_at) and
        # required so an abandoned holder can't block forever.
        conn.execute("DELETE FROM leases WHERE expires_at <= ?", (now,))
        try:
            conn.execute(
                "INSERT INTO leases (user_id, request_hash, acquired_at, expires_at, holder) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, request_hash, now, now + ttl_seconds, holder),
            )
            acquired = True
        except sqlite3.IntegrityError:
            # Lease held by another holder — caller can poll lookup_result.
            acquired = False
        # Sweep stale result rows opportunistically — keeps the table bounded
        # without needing a separate vacuum job.
        cutoff = now - _RESULT_RETENTION_SECONDS
        conn.execute("DELETE FROM results WHERE completed_at <= ?", (cutoff,))
    try:
        yield acquired
    finally:
        if acquired:
            with contextlib.closing(_connect(db_path)) as conn:
                conn.execute(
                    "DELETE FROM leases WHERE user_id = ? AND request_hash = ?",
                    (user_id, request_hash),
                )


def lease_holder(
    user_id: str,
    request_hash: str,
    *,
    db_path: Path = _DEFAULT_DB,
) -> dict[str, Any] | None:
    """Return the current (un-expired) lease holder, or None."""
    with contextlib.closing(_connect(db_path)) as conn:
        row = conn.execute(
            "SELECT holder, acquired_at, expires_at FROM leases "
            "WHERE user_id = ? AND request_hash = ? AND expires_at > ?",
            (user_id, request_hash, time.time()),
        ).fetchone()
        return dict(row) if row else None


__all__ = [
    "acquire",
    "compute_request_hash",
    "lookup_result",
    "record_result",
    "lease_holder",
]
