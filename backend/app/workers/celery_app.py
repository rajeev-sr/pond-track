"""Celery application. Tasks are registered from M6 onward (HLD ADR-2)."""

from __future__ import annotations

from celery import Celery

from app.config import get_settings

_s = get_settings()

celery_app = Celery("contour", broker=_s.REDIS_URL, backend=_s.REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,  # needed for the RUNNING state (HLD 3.7)
    task_time_limit=15 * 60,
    task_soft_time_limit=13 * 60,
    worker_max_tasks_per_child=50,  # bounds any leak in the geo stack
    worker_prefetch_multiplier=1,  # long tasks: fair dispatch beats batching
)
