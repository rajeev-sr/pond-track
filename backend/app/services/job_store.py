"""Where a job's progress lives between the worker writing it and the client
polling it (M6-2, M6-5).

Redis rather than Postgres for the *live* record: progress is written on every
step boundary, read on every poll, and worthless an hour later. Postgres keeps
the finished result (`analyses.result`), which is the thing worth surviving a
restart.

The in-memory implementation is not only for tests. This project runs locally by
design, and a single-process run with no Redis should still be able to hand back
a job id and a status -- degrading to "async within one process" is far more
useful than refusing to start.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from app.core.logging import get_logger

log = get_logger("services.job_store")

#: Long enough that a client polling a slow analysis never loses it, short enough
#: that abandoned jobs do not accumulate. The durable copy is in Postgres.
DEFAULT_TTL_S = 24 * 3600

KEY_PREFIX = "contour:job:"


@dataclass
class JobRecord:
    """Everything a status or result response needs, in one serialisable blob."""

    job_id: str
    #: `JobProgress.as_dict()` -- state, percentage, per-step outcomes, warnings.
    progress: dict[str, Any]
    params: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    created_at: float = 0.0
    updated_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> JobRecord:
        known = {k: raw.get(k) for k in cls.__dataclass_fields__ if k in raw}
        return cls(**known)  # type: ignore[arg-type]

    @property
    def elapsed_s(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.finished_at if self.finished_at is not None else time.time()
        return max(0.0, end - self.started_at)


class JobStore(Protocol):
    """The contract both implementations satisfy."""

    def put(self, record: JobRecord) -> None: ...
    def get(self, job_id: str) -> JobRecord | None: ...
    def delete(self, job_id: str) -> bool: ...


class MemoryJobStore:
    """Process-local store. Thread-safe, because the worker and the request
    handler that reads it are different threads under uvicorn."""

    def __init__(self, ttl_s: float = DEFAULT_TTL_S) -> None:
        self._records: dict[str, tuple[float, JobRecord]] = {}
        self._lock = threading.Lock()
        self._ttl = ttl_s

    def put(self, record: JobRecord) -> None:
        with self._lock:
            self._records[record.job_id] = (time.time() + self._ttl, record)

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            entry = self._records.get(job_id)
            if entry is None:
                return None
            expires, record = entry
            if expires < time.time():
                del self._records[job_id]
                return None
            return record

    def delete(self, job_id: str) -> bool:
        with self._lock:
            return self._records.pop(job_id, None) is not None


class RedisJobStore:
    """Redis-backed store, so a worker process and the API can share a job."""

    def __init__(self, client: Any, ttl_s: int = DEFAULT_TTL_S) -> None:
        self._redis = client
        self._ttl = ttl_s

    @staticmethod
    def _key(job_id: str) -> str:
        return f"{KEY_PREFIX}{job_id}"

    def put(self, record: JobRecord) -> None:
        # A failed write must not take the analysis down with it: the worker's
        # job is to finish the work, and losing a progress tick only costs the
        # client one poll's worth of freshness.
        try:
            self._redis.setex(self._key(record.job_id), self._ttl, json.dumps(record.as_dict()))
        except Exception as exc:  # any client error here is non-fatal
            log.warning("job progress write failed", job_id=record.job_id, error=str(exc))

    def get(self, job_id: str) -> JobRecord | None:
        try:
            raw = self._redis.get(self._key(job_id))
        except Exception as exc:
            log.warning("job progress read failed", job_id=job_id, error=str(exc))
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except ValueError:
            log.warning("job record was not valid JSON", job_id=job_id)
            return None
        return JobRecord.from_dict(payload)

    def delete(self, job_id: str) -> bool:
        try:
            return bool(self._redis.delete(self._key(job_id)))
        except Exception as exc:
            log.warning("job delete failed", job_id=job_id, error=str(exc))
            return False


_store: JobStore | None = None
_store_lock = threading.Lock()


def get_store() -> JobStore:
    """The process's job store: Redis when reachable, memory when not.

    Reachability is tested once with a ping rather than assumed from the URL
    being configured, because a configured-but-down Redis is the common case and
    it would otherwise fail on every poll instead of once at startup.
    """
    global _store
    with _store_lock:
        if _store is not None:
            return _store
        _store = _build_store()
        return _store


def _build_store() -> JobStore:
    from app.config import get_settings

    url = getattr(get_settings(), "REDIS_URL", "")
    if url:
        try:
            import redis

            client = redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
            client.ping()
            log.info("job store: redis")
            return RedisJobStore(client)
        except Exception as exc:  # fall back rather than fail
            log.warning("redis unavailable, job store falls back to memory", error=str(exc))
    log.info("job store: in-process memory")
    return MemoryJobStore()


def reset_store() -> None:
    """Drop the cached store. For tests, and after a settings change."""
    global _store
    with _store_lock:
        _store = None
