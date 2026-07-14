# Aurora Phase 1 学习指南

这是专门为你梳理的 Phase 1 代码学习路径，目标是让你在有限的时间内，按顺序看完之后能够：
独立讲清楚**核心请求链路**、**架构分层设计**、**关键技术选型的理由**、**工程上做过的权衡取舍**——达到能在设计评审 / 面试场景里从容复盘的中高级工程师水平。

## 怎么用这份指南

- 代码里所有以 `# 【卷】` 开头的注释都是本次专门为你写的"导读"，用紫色高亮标出（已经帮你配置好 VS Code 的 Highlight 插件，`.vscode/settings.json` 已提交，插件装好后打开项目会自动生效）。
- 原有的英文 docstring / 注释是给"未来审计者"看的技术引用（对应 `docs/` 里的具体条款），保留即可，不必细抠；**优先看紫色高亮的中文导读**，它们才是专门讲给你听的"为什么"。
- 每个小节给出：要看哪个文件、看哪几个函数/类、核心问题是什么。不需要逐行读完整个文件，跟着导读走即可。
- 建议节奏：一个大标题（一、二、三...）大致对应半天到一天，不要在某个小节卡太久——先囫囵吞枣建立全局链路认知，细节可以之后回头精读。
- 第一节里引用到的 7 份原始设计文档（`README.md`、`PRD.md`、`ARCHITECTURE.md`、`DATA_MODEL.md`、`ERROR_HANDLING.md`、`SEQUENCE_DIAGRAMS.md`、`DEVLOG.md`）都在 [`docs/study/zh/`](zh/) 目录下有对应的中文翻译版本，文件名完全一致——阅读吸收更快的话可以直接看中文版，代码块/命令行/Mermaid 图表保留了英文原文，不影响对照代码看。

---

## 一、全局认知与架构总览

**目标**：不看任何代码，先建立"这个系统长什么样、为什么长这样"的心智模型。这是后面所有代码都要挂靠的骨架。

### 一.一 项目定位与设计哲学

看：`README.md` 全文 + `docs/PRD.md` 第 1-3 节和第 7 节。

核心问题：Aurora 要解决什么问题？为什么明确排除 RAG / Agent / 业务逻辑？第 7 节"explicit tradeoffs"里，为什么选 Python/FastAPI 而不是作者更熟悉的 TypeScript？为什么本地用 Ollama 而不是 vLLM？这些"被拒绝的选项"和给出的理由，是评审时最容易被追问、也最能体现工程判断力的地方。

### 一.二 四层架构与请求生命周期

看：`docs/ARCHITECTURE.md` 第 1-3 节 + `docs/SEQUENCE_DIAGRAMS.md` 第 1 节（mermaid 时序图）。

核心问题：为什么"应用层永远不直接调模型"这条规则是整个架构的灵魂？没有队列会导致哪两件事做不到（并发上限、请求接受与执行解耦）？为什么 worker 要是独立进程而不是同进程里的一个函数调用？这一节看完，你应该能不看图، 口头画出请求从客户端到模型再回到客户端的完整路径。

### 一.三 目录结构与模块边界

看：`docs/ARCHITECTURE.md` 第 6-7 节（模块职责表）+ `README.md` 的 repo layout 表格。

核心问题：`app/`、`worker/`、`providers/`、`shared/` 四个包各自"能做什么"和"明确不能做什么"？记住这张职责表，后面看每个文件时对照它，会很容易看出"这段代码为什么放在这里而不是别处"。

---

## 二、共享契约层 `shared/`

**目标**：理解 `app/` 和 `worker/` 这两个独立进程是怎么"在不共享内存的情况下，保证互相理解对方"的。

### 二.一 错误码体系 — `shared/errors.py`

一个枚举，被 API 的 JSON 错误体、SSE 的 `error` 事件、日志行三处同时使用。看懂 `AuroraError` 这个基类怎么把"HTTP 状态码"、"是否可重试"这两个决策都收敛到一个地方，而不是让每个调用点各自判断。

### 二.二 跨进程数据模型 — `shared/schemas.py`

这是全项目引用最多的文件之一。重点看 `InferenceTaskPayload`（Celery 任务的完整载荷）、`StreamMessage`（Redis pub/sub 上跑的消息格式）、`SSEEvent`（最终吐给客户端的格式）——这三层格式为什么不是同一个，各自解决什么问题。

---

## 三、模型 Provider 抽象层 `providers/`

**目标**：理解"可插拔的模型后端"这个核心设计是怎么落地的，以及 Protocol 类型系统的一个真实陷阱。

### 三.一 Protocol 接口设计 — `providers/base.py`

只有两个方法的一个 `Protocol`。看懂它为什么故意不写 `async def chat(...)` 而是 `def chat(...) -> AsyncIterator[...]`——这是这次 mypy 实测抓出来的一个真实类型陷阱，值得精读。

### 三.二 Ollama 真实实现 — `providers/ollama_provider.py`

对照 `docs/DEVLOG.md` 里的问题 #12（token usage 一直是 0 的那个 bug）来看这个文件，理解"为什么单元测试测不出这个问题，只有接真实 Ollama 才发现"。

### 三.三 Mock 实现 — `providers/mock_provider.py`

`MockProvider` 为什么能让 worker 的测试完全不依赖真实模型？留意 `fail_after_chunks` 参数怎么精确模拟"发了几个 token 之后才失败"。

### 三.四 工厂模式 — `providers/factory.py`

`match` 语句是全项目唯一一处读取 `MODEL_PROVIDER` 环境变量、决定用哪个实现的地方——为什么只能有这一处？

---

## 四、数据与基础设施层 `app/core/`

**目标**：理解支撑上层业务逻辑的几块基础设施，尤其是后面会在 worker 里"炸掉"的那个并发陷阱在这里已经埋下了伏笔。

### 四.一 配置管理 — `app/core/config.py`

一个 `pydantic-settings` 的 `Settings` 类。为什么要求进程启动时校验配置、缺失就拒绝启动，而不是运行时才报错？

### 四.二 数据库连接层

#### 四.二.一 — `app/core/db.py`

全局唯一的 `engine`/`session_factory`。**先记住这里是"单例、模块加载时创建一次"**——这个设计对 `app/`（单一事件循环、长期存活的进程）是对的，但后面在 `worker/tasks.py`（六.三.三）会看到为什么同一个单例对 worker 是错的，一个真实踩过的坑，留到那里细讲。

#### 四.二.二 — `app/core/models.py`

`DATA_MODEL.md` 表结构的直接 ORM 映射，扫一眼字段对应关系即可，不用精读。

### 四.三 认证机制 — `app/core/auth.py`

重点看 `hash_api_key` 为什么用 SHA-256 而不是 bcrypt——这是一个容易被面试官追问"你不是应该用 bcrypt 吗"的点，想清楚"高熵随机密钥"和"低熵人类密码"的本质区别。

### 四.四 限流算法 — `app/core/rate_limit.py`

滑动窗口日志（sliding window log）算法，用 Redis 有序集合实现。想清楚：为什么不用固定窗口计数器（会在窗口边界允许 2 倍突发）？为什么不用令牌桶（那是解决"允许突发"的问题，这里的需求恰恰是不允许突发）？

### 四.五 Redis 连接管理 — `app/core/redis.py`

两个连接：`get_redis()` 和 `get_broker_redis()`。为什么要分开？

### 四.六 日志与指标

#### 四.六.一 — `app/core/logging.py`

`bind_request_id` / `clear_request_context` 这一对函数，是怎么让"同一个请求在 API 和 worker 两个进程里打的所有日志"都能用同一个 `request_id` 串起来的？

#### 四.六.二 — `app/core/metrics.py`

7 个指标，尤其留意 `ttft_seconds`（首字延迟）和 `tpot_seconds`（每 token 耗时）——这两个是"这是一个真正的推理服务监控系统"而不是"随便套了个 /metrics 的普通 API"的关键证据。

---

## 五、业务编排层 `app/services/`

**目标**：这一层是"胶水层"，把上面的基础设施组合成真正的业务能力。理解两个核心设计：缓存策略、发布订阅桥接。

### 五.一 会话服务与缓存策略 — `app/services/session_service.py`

看 `get_context()` 和 `_cache_append()`：Redis 缓存的是什么？Postgres 又存的是什么？为什么不是"只用一个存储"？（对照 `docs/DATA_MODEL.md` 第 4 节"Why not just one store"）

### 五.二 队列桥接设计 — `app/services/queue_service.py`

**全项目最值得精读的一个文件之一**。看 `subscribe()` 和 `dispatch_inference()` 两个函数的调用顺序为什么不能颠倒（先订阅、再派发任务）——颠倒了会发生什么？再看 `forward_events()` 为什么用带超时的轮询而不是阻塞的 `pubsub.listen()`（提示：要支持检测客户端断线）。

---

## 六、Worker 推理引擎 `worker/`

**目标**：这是整个系统里踩坑最多、也最能体现"工程判断力"的一层。三个文件都建议精读。

### 六.一 Celery 配置与任务注册陷阱 — `worker/celery_app.py`

看 `task_acks_late` / `task_reject_on_worker_lost` 这两个配置为什么必须显式打开（对照 `docs/ERROR_HANDLING.md` 第 6 节）。然后看 `imports=("worker.tasks",)` 这一行——这是一个真实踩过的坑：不写这行，Celery worker 进程根本不会注册任何任务，本地单元测试还测不出来，只有起了真实的 Celery worker 进程才会暴露。

### 六.二 可靠性机制 — `worker/reliability.py`

`compute_backoff_seconds` 和 `CancellationChecker`。想清楚 `CancellationChecker` 为什么必须是一个"同步"的可调用对象，而不是 `async` 函数（跟第三节 Protocol 类型陷阱是同一类问题）。

### 六.三 核心任务流程 — `worker/tasks.py`

**这是全项目最核心、最复杂的一个文件，建议花最多时间在这里**。文件内代码本身按此顺序排列，跟着 `【卷 六.三.X】` 编号走即可：

#### 六.三.一 — `_run_inference_async`

为什么要在 `async for` 循环结束之后再多做一次 `cancel_check()`（对照 `docs/DEVLOG.md` 问题 #8，一个测试抓出来的真实 bug）。

#### 六.三.二 — `run_inference`（Celery task 本体）

"重试只在还没有任何 token 发给客户端之前才安全"这条规则是怎么用代码实现的？为什么用 `self.retry(countdown=...)` 而不是自己写一个 while 循环重试？

#### 六.三.三 — `_persist_assistant_message`

**这是接真实 Postgres + Redis 之后才炸出来的一个并发 bug**，强烈建议对照 `docs/DEVLOG.md` 问题 #13 精读这个函数的注释——理解"为什么 `app/core/db.py`（四.二.一）里那个全局单例 engine，在 worker 这种『每个任务都是一次全新 `asyncio.run()`』的执行模型下会出问题"。这是这次全部踩坑里最有技术深度的一个，值得讲给别人听。

---

## 七、API 网关层：核心链路串联 `app/api/` + `app/main.py`

**目标**：前面六节看的都是"零件"，这一节看这些零件是怎么被组装成一次完整的 `POST /chat` 请求的——也就是你要能脱口而出的"核心链路"。

### 七.一 核心链路：`POST /chat` — `app/api/chat.py`

**通读整个文件，这是终点站**。对照 `docs/SEQUENCE_DIAGRAMS.md` 第 1 节的时序图，逐行核对代码：认证 → 限流检查 → 找/建会话 → 存用户消息 → 取上下文 → 订阅 Redis 频道 → 派发 Celery 任务 → 开始转发 SSE。再看 `_sse_generator` 里断线检测和取消逻辑是怎么接住第五节 `queue_service.forward_events` 的 `is_disconnected` 回调的。看完这个文件，你应该能把第二到第六节看过的所有零件在脑子里串成一条线。

### 七.二 应用组装与横切关注点 — `app/main.py`

中间件怎么给每个请求打 `request_id` 并统一记一条结构化日志？`AuroraError` 的全局异常处理器怎么把第二节的错误码枚举变成正确的 HTTP 状态码和 `Retry-After` 头？

### 七.三 其余路由 — `auth.py` / `conversations.py` / `feedback.py` / `health.py`

这几个文件相对简单，扫一遍即可，不用精读。`health.py` 值得留意的是它是全项目唯一一处"`app/` 破例直接依赖 `providers/`"的地方，以及为什么这个例外是合理的。

---

## 八、测试体系与真实环境验证复盘

**目标**：理解"怎么测试一个没有真实模型/数据库/Redis 也能跑的系统"，以及"单元测试的边界在哪里"——这是这次经历里含金量最高的一课。

### 八.一 测试设计思路 — `tests/conftest.py`

看 `fake_dispatch` 这个 fixture：它怎么在完全没有真实 Celery worker 的情况下，伪造一个"worker 完成了任务"的场景？再看它旁边那段中文都没写但英文注释很长的说明——这个 fixture 本身也踩过一个坑（一开始漏掉了持久化那部分，被自己的测试抓出来了）。

可以顺手扫一眼 `tests/integration/test_worker_inference.py` 和 `tests/unit/test_mock_provider.py`，感受一下"用 Mock/fake 隔离外部依赖"这个思路在不同层是怎么落地的。

### 八.二 DEVLOG 踩坑复盘 — `docs/DEVLOG.md`

**这是整个学习计划的收官，也是最重要的一节**。通读 "Issues encountered and how they were resolved" 这个板块（15 条），按 A-E 五组分类过一遍：

- A 组（环境/流程问题）：跟代码无关，但体现的是工程判断——什么时候该自己拿主意，什么时候该停下来问。
- B 组（静态检查抓到的）：类型系统能帮你在写测试之前就拦住的错误类型。
- C 组（单元测试抓到的）：SQLite/fakeredis/Mock 这一层测试的真实价值和边界。
- D 组（只有真实基础设施才能抓到的，4条）：**这四条是全部踩坑里最有含金量的，因为它们精确地划出了"单元测试测不出什么"这条线**——每一条都值得你能讲出"问题现象、根因、为什么测试没抓到、怎么修的"这四步。
- E 组（顺手做的工程优化）：不是 bug，是"让系统更经得起审查"的细节。

看完这一节，回头把第一节"一.一"里"被拒绝的选项"再过一遍——这时候你应该已经有足够的上下文，能把"设计时的权衡"和"实现时踩的坑"连成一个完整的故事。

---

## 学习完成的验收标准

不需要背代码，但应该能不看文档、口头讲清楚以下几件事：

1. 画出一次 `POST /chat` 请求从客户端到 Ollama 再回到客户端的完整路径，指出每一步在哪个进程、哪个文件。
2. 解释"为什么 worker 是独立进程"和"为什么要有 Redis 队列"这两个架构决策，以及不这么做会失去什么。
3. 挑 D 组 4 个真实 bug 里的任意一个，完整讲出"现象 → 根因 → 为什么测试没测出来 → 怎么修的"。
4. 解释限流、重试、取消这三个可靠性机制各自用什么技术实现，以及背后不能接受哪种失败模式。
