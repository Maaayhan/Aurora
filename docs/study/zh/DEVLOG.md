# Aurora —— 开发日志（中文版）

> 本文档是 [`docs/DEVLOG.md`](../../DEVLOG.md) 的中文翻译，仅用于学习，内容以英文原版为准。文件路径、函数名、错误信息、命令行等保留英文原文。

已完成工作的审计记录，以及非显而易见决策背后的理由，按时间顺序排列（最新的在最后）。这不是给用户看的更新日志——而是给未来复盘用的记录，用来说明"某个东西为什么被这样构建"，这样一个设计选择就永远不需要靠反向推导 diff 才能搞明白。

每条记录包含：日期、改动了什么、为什么这么做（只有当理由不能从 `docs/` 里直接看出来时才写）、以及跟设计文档之间的任何偏差（应该很少见——如果真有偏差，对应的文档也应该同步更新）。

---

## 2026-07-14

**开始 Phase 1 的实现工作。** 在写任何代码之前，先完整读完了 Phase 0 的全部设计文档（`PRD.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`API_SPEC.md`、`ERROR_HANDLING.md`、`DEPLOYMENT.md`、`SEQUENCE_DIAGRAMS.md`、`ROADMAP.md`），这是 `ROADMAP.md` 里 Phase 0 退出标准的要求（"完成的标志是……能够从零开始、脱口而出地讲清楚"）。

搭建了两份构建阶段需要的追踪工具：
- `docs/KANBAN.md` —— 一个卡片对应一个交付物的看板，从 `ROADMAP.md` 里 Phase 1–4 的范围表格里生成，这样看板和路线图就不会悄悄地产生分歧。
- `docs/DEVLOG.md`（也就是本文件）—— 已完成工作和决策理由的审计日志。

这两份文档都是活文档，作为完成每一项工作的一部分同步更新，而不是事后补写。

**关于开发环境。** 这台机器上没有 Python 3.12，没有 `uv`/`poetry`，没有 Docker，没有 Ollama。就"要自主做多少环境搭建工作"（完整搭建，包括 Docker/Ollama；还是轻量搭建；还是只写代码）询问了用户，在当前这一轮里没有得到回应，于是先按轻量方案推进：通过 Homebrew 安装了 `uv`，并通过 `uv python install` 安装了 Python 3.12.13——足够写代码、并且用单元/集成测试真正跑起来了。刻意**没有**自主安装 Docker Desktop 或 Ollama——那些是更重的改动（涉及图形界面/带宽消耗，Ollama 还要拉取几个 GB 的模型权重），最好先确认一下再动手。这是 Phase 1 真正"完成"之前唯一还悬着的一个阻塞项：代码已经写好，并且针对 `MockProvider`/SQLite/fakeredis 测试通过了，但还没有针对一套真实运行的 Postgres/Redis/Ollama 环境验证过。

**构建顺序（按依赖关系）：** 项目脚手架（`pyproject.toml`、`.env.example`、`docker-compose.yml`、`docker/Dockerfile.{api,worker}`）→ `shared/`（错误码、跨进程边界共用的 Pydantic 模型）→ `providers/`（`ModelProvider` 协议、`OllamaProvider`、`MockProvider`、`factory.py`）→ Postgres 层（SQLAlchemy 2.0 异步模型 + 手写的 Alembic 迁移）→ `app/core/`（配置、认证、限流、Redis、日志、指标）→ `app/services/`（`session_service`、`queue_service`）→ `worker/`（`celery_app.py`、`tasks.py`、`reliability.py`）→ `app/api/`（`chat.py`、`auth.py`、`conversations.py`、`feedback.py`、`health.py`）→ 把这一切串起来的 `app/main.py` → Phase 1 的测试套件（39 个测试：针对独立模块的单元测试，以及针对真实 FastAPI 应用的集成测试——Postgres 换成 SQLite、Redis 换成 fakeredis、用一个 `dispatch_inference` 测试替身来代替一个真实的 Celery worker）。

**几个不那么显而易见的决策，以及为什么这么做：**

- **API key 用 SHA-256 哈希，而不是 bcrypt**（`app/core/auth.py:hash_api_key`）。`DATA_MODEL.md` 只规定了 `api_key_hash TEXT UNIQUE`，没有规定具体算法。Bcrypt 是应对"低熵密钥"（比如密码）场景的正确工具，它靠拖慢哈希速度来抵抗暴力破解，但它没法被当作一个唯一索引的查找键来用（验证时需要跟每一行都做一次比对）。API key 本身就已经是高熵的随机字符串了，所以一个支持 O(1) 索引查找的、快速的确定性哈希，才是这里正确的取舍——这也是 Stripe、GitHub 这类平台在处理 API key 认证时采用的方案。
- **Protocol 里 `ModelProvider.chat` 被标注成 `def`，而不是 `async def`**（`providers/base.py`），尽管 `ARCHITECTURE.md` 第 4 节的示意伪代码里写的是 `async def`。真实的实现都是异步*生成器*函数（`async def chat(...): yield ...`）——调用它会同步地、立即返回一个 `AsyncIterator`，调用这个动作本身从来不会被 await。如果标注成 `async def`，会让类型检查器以为需要 `await provider.chat(...)`，这跟每一个具体实现的实际用法都对不上——这个问题是被 `mypy` 抓出来的，不是靠肉眼检查发现的。
- **是 worker 把助手的回复持久化到 Postgres，而不是 API 来做这件事。** `SEQUENCE_DIAGRAMS.md` 第 1 节把这一步画成终态 SSE 事件之后的一个异步步骤，从结构上讲这样也说得通：worker 是唯一知道 `retry_count`（通过 Celery 自己的重试计数器）的一方，而且它内存里已经攒好了流式输出过程中累积的完整内容。这带来一个后果，值得单独指出，因为它曾经让一个测试感到意外：客户端有可能在助手消息被真正持久化落库*之前*，就先收到了 `event: done`（`worker/tasks.py:run_inference` 是先发布 `done`，然后才调用 `_persist_assistant_message`）——一个在收到 `done` 之后立刻重新拉取对话历史的客户端，理论上有可能跟这次写入产生竞态。`SEQUENCE_DIAGRAMS.md` 已经把这一步标注成"（异步）"了，所以这在 Phase 1 里被当作一个已经写进文档、可以接受的权衡取舍，而不是一个 bug——如果它在实践中真的表现为一次不稳定的读取，值得在 Phase 2/3 里认真看一下。
- **`app/services/queue_service.py` 派发 Celery 任务时，走的是 `worker/celery_app.py`（只有配置，不含任务定义），而不是直接依赖 `worker/tasks.py`。** 它按任务*名字*来派发（`celery_app.send_task("worker.tasks.run_inference", ...)`），这样做是刻意的，为了让 API 进程的导入依赖图永远不会牵涉到 `providers/`——即便 app/ 和 worker/ 共用同一个 Celery app 对象，也依然守住了 `ARCHITECTURE.md` 第 6 节"`app/` 永远不导入 provider 具体类"这条规则。（后续更新：这个设计在当天晚些时候被进一步收紧，见下方"真实环境验证"部分的第 11 条。）
- **最初那份 Alembic 迁移是手写的，不是自动生成的**——因为这台开发机上当时没有一个正在运行的 Postgres 实例可以拿来做 diff。它是 `DATA_MODEL.md` 第 1 节的直接照抄；这一点在迁移文件自己的 docstring 里也标注出来了，避免有人误以为它是经过机器校验的。
- **`GET /health` 里的队列深度、以及 `aurora_queue_depth` 这个指标，是一个近似值**（对 Celery broker 默认队列的 key 做 `LLEN`），而不是调用 Celery 的 `inspect()`——这样更便宜，不需要有一个正在运行的 worker 来响应，而且跟 `SEQUENCE_DIAGRAMS.md` 第 4 节里背压检查隐含的实现方式（"检查队列深度"，一次快速的同步读取）是一致的。

**测试套件在"造成尴尬"之前抓到的 bug：**

1. `session_service.append_message` 把 `conversations.updated_at` 设成了 `message.created_at`，但 `message.created_at` 这个字段在 Python 这一侧，在这一行真正被 flush 到数据库之前一直是 `None`（它是一个 `server_default=func.now()` 的字段）——这会导致一个对话里的第二条消息就会抛出 `IntegrityError: NOT NULL constraint failed`。修复方式是在这条 UPDATE 语句里直接用 `func.now()`，而不是依赖这个 ORM 对象上一个还没被刷新过的属性。
2. `worker/tasks.py:_run_inference_async` 会把一个"在还没有任何 token 被 yield 之前就被取消"的请求，误报成 `status="success"`——取消检查这行代码本来是写在 `async for` 循环体内部的，但一个内部自己也在处理 `cancel_check` 的 provider（`MockProvider` 和 `OllamaProvider` 都是这样实现的）有可能返回一个"零次迭代"的生成器，这种情况下那行检查代码根本没机会跑。修复方式是在循环结束之后，再多检查一次 `cancel_check()`，然后才判定成功。这是被 `tests/integration/test_worker_inference.py::test_cancellation_stops_the_stream_without_publishing_a_terminal_event` 这个测试抓到的。

**目前为止的验证情况：** `uv run pytest` —— 39 个测试全部通过。`uv run ruff check .` —— 干净。`uv run mypy app worker providers shared` —— 干净（36 个源文件）。手动确认了 `GET /health` 在 Postgres/Redis/Ollama 都没运行的情况下，会快速失败（143 毫秒）并给出带每个依赖项诊断信息的 `unhealthy` 状态，而不是卡住——这正是 `ERROR_HANDLING.md` 第 7 节规定的行为。

**还没做的事：** 针对真实 Postgres/Redis/Ollama 的端到端验证（被上面提到的环境搭建问题卡住了）、Phase 2（熔断器、背压强制执行、现场演示取消机制）、Phase 3（链路追踪/监控面板）、Phase 4（部署/回滚脚本）。

---

**同一天晚些时候：安装了 Docker 和 Ollama，把 Phase 1 真正端到端跑通了。** 用户确认了之前那个安装环境的问题。这又暴露出一个更紧迫的问题：本地磁盘只剩大约 2.7GB 可用空间，不够放下 Docker 的虚拟机镜像，更别提 Postgres/Redis/api/worker 这几个镜像了。

**在任何东西把本地磁盘填满之前，先把存储迁移到了一块外接硬盘（`/Volumes/Samsung870EVO`，剩余 155GB）：**
- Ollama 的模型（通过 `OLLAMA_MODELS` 环境变量，在 brew services 的 launchd plist 文件——`/opt/homebrew/Cellar/ollama/*/homebrew.mxcl.ollama.plist`——里的 `EnvironmentVariables` 字段设置，因为 `brew services` 本身没有暴露这个配置项的命令行参数）→ 迁移到 `/Volumes/Samsung870EVO/dev-storage/ollama-models`。
- Docker Desktop 的虚拟机磁盘（在 `~/Library/Group Containers/group.com.docker/settings-store.json` 里的 `DataFolder` 字段设置，并且是在第一次真正拉取容器*之前*就设置好的，所以 Docker 压根没有在默认位置分配过磁盘镜像）→ 迁移到 `/Volumes/Samsung870EVO/dev-storage/docker-data`。通过 `docker info` 里的 `Docker Root Dir` 字段，以及用 `du`（而不是 `ls`）检查 `Docker.raw` 文件的真实占用大小（而不是看起来的稀疏文件大小），确认了迁移生效。
- `uv` 和 Homebrew 的下载缓存（`~/.zshrc` 里的 `UV_CACHE_DIR`、`HOMEBREW_CACHE`）→ 同样指向这块外接硬盘，另外还跑了 `brew cleanup -s`，立刻回收了大约 700MB 已下载的安装包缓存。综合效果：本地磁盘从 2.7GB 涨到 5.5GB 可用空间，并且在整个 Docker 构建 + 镜像拉取 + 验证过程中都保持稳定，没有再往下掉。

**通过真实基础设施跑起来之后，才发现的 4 个真实 bug——按 Phase 1 的测试套件（单元测试 + SQLite/fakeredis/MockProvider）的结构，这几个 bug 本来就不可能被测出来：**

1. **Docker 构建直接失败**：`pyproject.toml` 声明了 `readme = "README.md"`（hatchling 构建 `aurora` 这个包的时候需要用到），但两个 Dockerfile（`docker/Dockerfile.api` 和 `Dockerfile.worker`）在执行 `uv sync --no-dev` 之前都没有把 `README.md` 拷进构建上下文。修复方式是在两个文件里，把 `README.md` 加进 `COPY pyproject.toml uv.lock* README.md ./` 这一行。
2. **Worker 压根就没真正跑过任何任务。** `celery -A worker.celery_app worker` 这条启动命令只会导入 `worker/celery_app.py`——它不会导入 `worker/tasks.py`，所以 `run_inference` 从来没有被注册过（worker 自己启动日志里的 `[tasks]` 列表是空的）。任何被派发的任务都会失败，报错是"Received unregistered task of type..."。修复方式是在 `celery_app.conf` 里加上 `imports=("worker.tasks",)`。顺手修这个问题的时候，也把 `app/services/queue_service.py` 重构成了自己构建一个最小化的 Celery 生产者客户端（而不是导入 `worker.celery_app`）——现在 API 镜像已经完全不需要在磁盘上包含 `worker/` 这个包了，这是比之前更严格的一版模块边界规则。
3. **Token 用量数字一直静默地是 0。** 针对一个真实运行的 Ollama 实例实测确认了这一点：它流式返回的 chunk 里根本不会带 `usage` 对象，除非请求里显式设置了 `stream_options: {"include_usage": true}`——而且即便设置了，usage 也是作为*一个单独的、`choices` 字段为空数组的最终 chunk*发过来的，如果真的走到这条路径，原来那版 `OllamaProvider.chat()` 会直接在 `chunk["choices"][0]` 这里崩掉。修复方式是在请求载荷里加上 `stream_options.include_usage`；把这种 `choices` 为空、只带 usage 的 chunk 单独处理成一种情况；并且加固了 `worker/tasks.py`，让一个后到达、但不带 usage 数据的 `finished=True` chunk，不能覆盖掉前一个 chunk 已经设置好的真实数字。
4. **助手的回复在第一次请求之后，就悄悄停止持久化到 Postgres 了**——报错是 `RuntimeError: Future ... attached to a different loop`。根因是：`app/core/db.py` 的异步 engine、以及 `app/core/redis.py` 的异步 Redis 客户端，都是进程级别的单例——这个设计对 `app/`（一个 Uvicorn 进程，一个事件循环，贯穿整个进程生命周期）是对的，但对 `worker/tasks.py` 是错的，因为它*每一个任务*都会调用一次全新的 `asyncio.run()`。asyncpg/redis-py 的异步连接是绑定在创建它们的那个事件循环上的，所以第二个任务在复用共享单例里那些连接池时，拿到的其实是绑定在第一个任务（已经关闭的）事件循环上的连接，因而崩溃。修复方式是让 `_persist_assistant_message` 自己开一个生命周期很短的 Redis 客户端，并且在每次用完之后调用 `await get_engine().dispose()`，这样下一个任务的 `asyncio.run()` 调用，拿到的连接就一定是绑定在*它自己*的事件循环上的。通过连续发送 3 次真实请求、确认全部 3 条助手回复都正确落库、且 worker 日志里没有任何报错，验证了这个修复。

**验证过程中顺手做的两处非 bug 优化：** `uv run` 在每次容器启动时都会重新同步一次 Python 环境，重新下载 `mypy`/`ruff` 这些开发依赖，尽管镜像构建时用的是 `--no-dev`——原因是 `uv run` 默认会在运行时重新解析并同步依赖，跟构建时用过什么参数无关。修复方式是在两个 Dockerfile、以及 `docker-compose.yml` 里 `flower` 服务的启动命令上，都加上 `--no-sync`。另外，Celery 默认会把 worker 的 stdout 重定向经过它自己的日志系统，并且把所有捕获到的内容一律打上 `WARNING` 标签，跟内容实际是什么级别无关——这样一来就把 structlog 自己那份 JSON payload 里的 `level` 字段给盖住了；修复方式是设置 `worker_redirect_stdouts=False`。

**端到端验证，全部针对真实堆栈（Docker Compose + 宿主机上运行的、承载 `llama3.2:3b` 的 Ollama）：**
- 针对真实 Postgres 执行 `alembic upgrade head`——表结构跟 `DATA_MODEL.md` 第 1 节完全一致（用 `psql` 里的 `\d messages` 核对过）。
- 用 `X-API-Key` 认证发起的 `POST /chat`，能通过 API → Redis pub/sub → Celery worker → Ollama 这一整条链路，流式返回真实的、逐 token 的 SSE 输出，最终以带真实 `promptTokens`/`completionTokens`/`latencyMs` 的 `event: done` 结束。
- 多轮对话验证通过：同一个对话里的一条后续消息，能够正确引用上一轮的内容，证明了 `session_service.get_context` 里"Redis 会话缓存 + Postgres 兜底"这套机制，确实能让真实的对话历史，完整地往返穿过一个真实的模型。
- 在所有依赖都健康的情况下，`GET /health` 报告 `healthy`，并带上每个依赖真实的延迟数字（Postgres 12.7 毫秒，Redis 0.7 毫秒，推理 71.8 毫秒）。
- 限流：临时把 `RATE_LIMIT_REQUESTS` 调低到 3，确认了真实的 `429` 响应，带 `Retry-After`，错误体格式跟 `API_SPEC.md` 完全一致，然后把配置改回了正常值。
- `GET /metrics` 在真实流量之后，7 个必需指标全部显示出真实的非零数值，包括根据一次真实模型生成计算出来的 `aurora_ttft_seconds`（约 0.2 秒）和 `aurora_tpot_seconds`（约每 token 12 毫秒）——不是拿假数据凑出来的。
- Flower（`:5555`）可以正常访问，并且是实时的。

**Phase 1 现在才算真正达到了 `ROADMAP.md` 里定义的"完成"标准**——不只是代码针对测试替身跑通了，而是针对它本来就是为其设计的那些真实依赖，演示过确实能正常工作。经过上面每一处修复之后，本地测试套件（39 个测试）、`ruff`、`mypy` 依然全部通过。

---

## 实施过程中遇到的问题和困难，以及怎么解决的——汇总整理（2026-07-14）

上面按时间顺序写的记录是按构建顺序展开的；这一节把这一整天遇到的每一个问题都汇总到一处，按"是怎么被发现的"来分组，因为这条轴线更能说明清楚：lint → 类型检查 → 单元测试 → 真实环境，这几层验证手段各自到底能防住什么、防不住什么。每一条都按 **问题 → 原因 → 解决办法** 的格式来写。

### A. 环境/流程类问题

1. **这台机器上没有 Python 3.12、`uv`、Docker、Ollama。**
   *原因*：全新的开发环境，Phase 0 只是设计阶段（还不需要任何代码、任何工具）。
   *解决办法*：立刻通过 Homebrew 安装了 `uv` + Python 3.12.13（风险低，纯命令行工具）。Docker/Ollama 先按下不表，问了用户之后再动手，因为这两个是更重的改动（图形界面应用、要下载几个 GB 的模型）——明确得到确认之后才继续。
2. **两次 `AskUserQuestion` 关于环境搭建的询问都没有得到回应**（60 秒超时，无响应）。
   *解决办法*：没有假设用户默许了那些风险更高的安装操作；只推进了轻量、容易撤销的那个选项（`uv`/Python），并且用明白话把这个阻塞点汇报回去，而不是靠猜测或者无限重试。等到明确得到"两个都装"的答复之后，才真正去动 Docker/Ollama。
3. **本地磁盘只剩大约 2.7GB 可用空间**——是在开始安装 Docker/Ollama 之后才发现的，而不是提前发现的。这远远不够放下 Docker 的虚拟机镜像，更别提 Postgres/Redis/api/worker 这几个镜像了。
   *原因*：机器上已经有一堆跟这个项目无关的大缓存（Spotify、Yarn、各种应用的 ShipIt 更新缓存），而且之前也没有把开发工具的存储迁移到机器外部过。
   *解决办法*：用户在任务中途发现了这个问题，要求把下载内容迁移到外接硬盘上。把 Ollama 的模型目录（通过 brew-services 的 launchd plist 设置 `OLLAMA_MODELS`）、Docker Desktop 整个虚拟机/数据目录（`settings-store.json` 里的 `DataFolder` 字段，在 Docker 分配它默认位置的磁盘镜像*之前*就设置好，所以不需要事后再做迁移）、以及 `uv`/Homebrew 的缓存，全部迁移到了 `/Volumes/Samsung870EVO`。另外还跑了 `brew cleanup -s`，立刻回收了大约 700MB。结果：本地磁盘从 2.7GB 涨到 5.5GB 可用空间，并且在整个构建+验证过程中都保持稳定。
4. **`brew install --cask docker` 安装失败**："It seems there is already an App at '/Applications/Docker.app'."（看起来 `/Applications/Docker.app` 这个位置已经有一个应用了）。
   *原因*：Docker Desktop 之前是手动安装的（不是通过 Homebrew），是在 6 月份装的，所以这次 cask 安装跟已有的应用产生了冲突。
   *解决办法*：没有跟这个 cask 安装器较劲——先检查了已有的安装能不能正常工作（`/Applications/Docker.app/Contents/Resources/bin/docker --version` 执行成功），然后直接启动并使用它，而不是强行重新安装一遍。

### B. 静态分析（ruff / mypy）在任何测试跑起来之前抓到的 bug

5. **`ModelProvider.chat` 在 Protocol 里被标注成了 `async def`**（`providers/base.py`），跟 `ARCHITECTURE.md` 第 4 节的示意伪代码保持一致。
   *原因*：真实的实现都是异步*生成器*函数（`async def chat(...): yield ...`）——调用它会同步地返回一个 `AsyncIterator`；调用这个动作本身从来不会被 await。Protocol 里的 `async def` 会让 mypy 以为需要 `await provider.chat(...)`，这跟每一个具体实现的实际情况都是矛盾的。
   *解决办法*：把 Protocol 里这个方法改成普通的 `def`，返回类型是 `AsyncIterator[ChatChunk]`。
6. **一些零零散散的 mypy 摩擦**，每一个都是已知的类型存根（stub）/类型标注上的小缺口，不是真正的 bug：redis-py 异步版的 `llen()` 被标注成 `Awaitable[int] | int`（一个类型存根的怪癖），在 `queue_service.py` 和 `health.py` 里触发了跟 `await` 相关的类型错误——用一个带注释说明理由的、针对性的 `# type: ignore[misc]` 解决。`Conversation.__table__.update()`——SQLAlchemy 把 `__table__` 标注成了更宽泛的 `FromClause`，而不是 `Table`——解决方式是改用 2.0 风格的 `update(Conversation)...` 写法，而不是直接去碰 `__table__`。`SSEEvent.data` 这个联合类型（`TokenEventData | DoneEventData | ErrorEventData`）没办法靠比较字符串 `event.event == "done"` 来做类型收窄——解决方式是改用 `isinstance(event.data, DoneEventData)` 这类判断，mypy *能够*靠这个做类型收窄。

### C. 单元/集成测试套件（SQLite + fakeredis + MockProvider）抓到的 bug

7. **同一个对话里的第二条消息，会报 `IntegrityError: NOT NULL constraint failed`。**
   *原因*：`session_service.append_message` 把 `conversations.updated_at` 设成了 `message.created_at`，但 `message.created_at` 在 Python 这一侧、在这一行真正被 flush 之前一直是 `None`（它是一个 `server_default=func.now()` 的字段）——在它被赋值之前就读取了它。
   *解决办法*：在这条 UPDATE 语句里直接使用 `func.now()`，而不是依赖这个 ORM 对象上一个还没被刷新过的属性。
8. **一个在还没有任何 token 流式输出之前就被取消的请求，被误报成了 `status="success"`。**
   *原因*：取消检查这行代码是写在 `worker/tasks.py:_run_inference_async` 的 `async for` 循环体内部的，但一个内部自己也在处理 `cancel_check` 的 provider（`MockProvider` 和 `OllamaProvider` 都是这样）有可能返回**零次**迭代的生成器——于是那行检查代码从来没机会跑，函数就落进了默认的"success"返回值。
   *解决办法*：在循环结束之后再多检查一次 `cancel_check()`，然后才判定成功。这是被 `test_cancellation_stops_the_stream_without_publishing_a_terminal_event` 这个测试抓到的。
9. **自己写的测试替身（fixture）本身也有一个 bug，不是应用代码的问题**：`tests/conftest.py` 里的 `fake_dispatch` 只模拟了 worker 往 pub/sub 发消息这部分，没有模拟它持久化到 Postgres 的那部分，所以断言"助手消息已经落库"的测试都失败了，报的还是 `StopIteration`（一个空生成器导致的）。
   *原因*：持久化本来就是真实 worker 的职责（`SEQUENCE_DIAGRAMS.md` 第 1 节），而这个测试替身只覆盖了一半。
   *解决办法*：给这个 fixture 补上一次对 `session_service.append_message` 的调用，模拟 `worker/tasks.py` 真正做的事情——并且把这一步排在发布 `done` 事件*之前*（这里刻意比真实系统更确定一些，真实系统是先发布 `done`、之后才持久化——这是一个已经写进文档、可以接受的竞态，见上面的决策列表）。

### D. 只有真正跑起真实堆栈（Docker Compose + 真实的 Postgres/Redis/Ollama）才抓到的 bug

以下这四个 bug，没有一个是上面 A-C 这几层能够触及到的；每一个都需要一次真实的容器启动、一个真实的 Celery worker 进程、或者一个真实的 Ollama 响应格式才会暴露。

10. **Docker 构建直接失败**：`uv sync --no-dev` 报错 `OSError: Readme file does not exist: README.md`。
    *原因*：`pyproject.toml` 给 hatchling 声明了 `readme = "README.md"`，但两个 Dockerfile 都没有把它拷进构建上下文。
    *解决办法*：在 `Dockerfile.api` 和 `Dockerfile.worker` 里，把 `README.md` 加进 `COPY pyproject.toml uv.lock* README.md ./` 这一行。
11. **Worker 容器干净地启动了，但从没真正跑过一个任务。** 它自己启动日志里的 `[tasks]` 是空的。
    *原因*：`celery -A worker.celery_app worker` 只会导入 `worker/celery_app.py`——即便 `worker/tasks.py` 在同一个包里，Celery 也不会自动导入它，所以 `run_inference` 从来没被注册过；任何被派发的任务都会失败，报错"Received unregistered task of type..."。
    *解决办法*：在 `celery_app.conf` 里加上 `imports=("worker.tasks",)`。顺手把 `app/services/queue_service.py` 重构成了自己构建一个最小化的 Celery 生产者客户端，完全不再导入 `worker.celery_app`——API 镜像现在完全不需要在磁盘上包含 `worker/` 这个包了，这是比之前更严格的一版模块边界规则。
12. **Token 用量（`promptTokens`/`completionTokens`）端到端地一直静默是 `0`。**
    *原因*：针对一个真实运行的 Ollama 实例实测确认——它流式返回的 chunk 根本不会带 `usage` 对象，除非请求里显式设置了 `stream_options: {"include_usage": true}`；而且即便设置了，usage 也是作为一个单独的、`choices` 为空数组的最终 chunk 发过来的，原来那版 `OllamaProvider.chat()` 一旦真的走到这条路径，会直接在 `chunk["choices"][0]` 崩掉。
    *解决办法*：在请求载荷里加上 `stream_options.include_usage`；把这种 `choices` 为空、只带 usage 的 chunk 单独处理；并且加固了 `worker/tasks.py`，让一个后到达但不带 usage 数据的 `finished=True` chunk，没法覆盖掉前一个 chunk 已经设置好的真实数字。
13. **助手的回复在第一次请求之后，就停止往 Postgres 落库了**——`RuntimeError: Future ... attached to a different loop`，只在一个刚启动的 worker 处理第二次及之后的聊天请求时才会出现。
    *原因*：`app/core/db.py` 的异步 engine、`app/core/redis.py` 的异步 Redis 客户端，都是进程级别的单例——这对 `app/`（一个 Uvicorn 进程、一个事件循环、贯穿整个进程生命周期）是对的，但对 `worker/tasks.py` 是错的，因为它*每一个任务*都会调用一次全新的 `asyncio.run()`。asyncpg/redis-py 的异步连接是绑定在创建它们的那个事件循环上的，所以第二个任务的 `asyncio.run()` 调用，复用到的是绑定在第一个任务（已经关闭的）事件循环上的连接池。
    *解决办法*：`_persist_assistant_message` 现在会自己开一个生命周期很短的 Redis 客户端，并且在每次用完之后调用 `await get_engine().dispose()`，强制让下一个任务拿到全新的、正确绑定在自己事件循环上的连接。通过连续发送 3 次真实请求、全部零报错，验证了这个修复（没修之前，第 2 次请求就会失败）。

### E. 验证过程中顺手发现的非 bug 优化（不是正确性问题，但值得修一下）

14. **每个容器每次启动都会重新同步一次 Python 环境**，重新下载 `mypy`/`ruff` 这些只在开发时用的依赖，尽管镜像构建时用的是 `--no-dev`。
    *原因*：`uv run` 默认会在运行时重新解析并同步依赖，跟构建时用过什么参数无关。
    *解决办法*：在两个 Dockerfile、以及 `flower` 服务的启动命令上都加上 `--no-sync`。
15. **Worker 打出来的结构化 JSON 日志行，全部都被标记成了 `WARNING`**，跟它们真实的 `"level"` 字段无关。
    *原因*：Celery 默认会把 worker 的 stdout/stderr 重定向经过它自己的日志系统，把任何不是从它自己 logger 里出来、但被它捕获到的内容，一律当作 `WARNING`。
    *解决办法*：在 `celery_app.conf` 里设置 `worker_redirect_stdouts=False`，让 structlog 那些已经带级别的 JSON 日志行原样透传。
