"""Celery application object — configuration only, no task definitions and no provider imports.

Kept separate from `worker/tasks.py` deliberately: `app/services/queue_service.py`
imports *this* module to dispatch tasks by name (`celery_app.send_task(...)`),
never `worker/tasks.py` directly — so the API process never pulls in
`providers/` (ARCHITECTURE.md §6: "`app/` never imports a provider class").

`task_acks_late` + `task_reject_on_worker_lost` are set here, not left at
default — ARCHITECTURE.md §2 / ERROR_HANDLING.md §6: without these, Celery
acks (and discards) a task the moment a worker *receives* it, so a worker
that dies mid-inference would silently lose the job instead of requeuing it.
"""

from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "aurora",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=settings.celery_worker_concurrency,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # `celery -A worker.celery_app worker` only imports *this* module by default — without
    # `imports`, the worker process never imports worker/tasks.py, so `run_inference` is never
    # registered and every dispatched task fails with "Received unregistered task of type...".
    # Caught by running a real worker process in Docker Compose, not by unit tests (which import
    # worker.tasks directly and never exercise Celery's own module-loading path).
    imports=("worker.tasks",),
    # Celery redirects worker stdout/stderr through its own logging by default, tagging
    # everything it captures as WARNING regardless of content — which stomps on our structlog
    # JSON lines' own `level` field (DEPLOYMENT.md §4: structured JSON to stdout). Disabling this
    # lets our already-leveled, already-JSON log lines pass through as written.
    worker_redirect_stdouts=False,
)
