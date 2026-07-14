"""Celery application object — configuration only, no task definitions and no provider imports.

Kept separate from `worker/tasks.py` deliberately: this module never imports
`providers/`, so it stays safe for anything to import without pulling in a
model client. (`app/services/queue_service.py` no longer even imports this
module — it builds its own minimal producer client, see 五.二 — but the
separation is still what keeps `worker/tasks.py`'s provider import from ever
becoming reachable from `app/`.)

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

# 【卷 六.一.一】task_acks_late + task_reject_on_worker_lost 这两行必须显式打开，因为 Celery
# 【卷】默认行为是"worker 一收到任务就立刻 ack（确认完成），不管任务有没有真正执行完"。
# 【卷】想象一下：worker 刚接到一个推理任务，还没跑完，进程突然被杀（比如 OOM、部署时被重启）——
# 【卷】默认配置下这个任务已经被 ack 过了，Broker 认为它"成功了"，直接丢弃，永远不会有任何 worker
# 【卷】重新执行它，用户的请求就这样人间蒸发，没有任何报错、没有任何日志。开了这两个配置之后，
# 【卷】ack 会推迟到任务真正跑完（成功或者显式失败）才发生，worker 中途暴毙的话，任务会被放回
# 【卷】队列，由另一个 worker 重新捞起来执行。
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
    # 【卷 六.一.二】这是 DEVLOG.md 问题 #11 的真实现场，也是这次全部踩坑里最"隐蔽"的一个：
    # 【卷】少了这一行，worker 进程能正常启动、能连上 Redis、日志看起来一切正常，但它的任务列表
    # 【卷】永远是空的——因为 `celery -A worker.celery_app worker` 这条启动命令，只会加载
    # 【卷】celery_app.py 这一个模块，不会主动去 import worker/tasks.py（哪怕它们在同一个包里）。
    # 【卷】run_inference 这个任务函数因为没被 import，根本没机会执行 @celery_app.task 装饰器，
    # 【卷】也就从没在 Celery 内部注册过。此时派发一个任务，会得到 "Received unregistered task of
    # 【卷】type..." 这种错误——本地单元测试（六.三 用的都是直接 import worker.tasks 来测）完全
    # 【卷】测不出这个问题，因为测试代码本身就是在"手动做了这一行本该做的事"。只有真正起一个
    # 【卷】Celery worker 进程、观察它启动日志里的 [tasks] 列表是不是空的，才会暴露。
    # `celery -A worker.celery_app worker` only imports *this* module by default — without
    # `imports`, the worker process never imports worker/tasks.py, so `run_inference` is never
    # registered and every dispatched task fails with "Received unregistered task of type...".
    # Caught by running a real worker process in Docker Compose, not by unit tests (which import
    # worker.tasks directly and never exercise Celery's own module-loading path).
    imports=("worker.tasks",),
    # 【卷 六.一.三】这个是验证真实环境时顺手做的日志修复：Celery 默认会把 worker 的
    # 【卷】stdout/stderr 重定向到它自己的日志系统里，并且把所有捕获到的内容统一打上 WARNING
    # 【卷】标签——不管内容实际是什么级别。我们的 structlog 已经把日志级别写进了 JSON 本身
    # 【卷】（"level": "info"），如果不关掉这个重定向，终端里看到的每一行都会被 Celery 的
    # 【卷】WARNING 前缀"糊"住，掩盖了真实的日志级别。
    # Celery redirects worker stdout/stderr through its own logging by default, tagging
    # everything it captures as WARNING regardless of content — which stomps on our structlog
    # JSON lines' own `level` field (DEPLOYMENT.md §4: structured JSON to stdout). Disabling this
    # lets our already-leveled, already-JSON log lines pass through as written.
    worker_redirect_stdouts=False,
)
