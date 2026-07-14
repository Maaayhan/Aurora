# Aurora —— 架构设计（中文版）

> 本文档是 [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) 的中文翻译，仅用于学习，内容以英文原版为准。图示和代码块保留英文原文，避免翻译破坏排版和代码正确性。

## 1. 分层

```
Client 客户端
  │  HTTPS, SSE
  ▼
API Layer 接口层           (app/ —— FastAPI)
  │  认证、限流、校验、查会话、任务入队、转发流
  ▼
Application Layer 应用层    (app/services —— 会话、对话、队列编排)
  │  Celery 任务 + Redis pub/sub —— 这一层不会直接对模型发起网络请求
  ▼
Inference Layer 推理层      (worker/ —— Celery worker)
  │  provider 接口、超时/重试/熔断、流式 token 转发
  ▼
Model 模型                 (本地用 Ollama / 生产用 vLLM，充当 Aurora-13B 的替身)
```

让这套东西称得上是"平台"而不是"一个包了层皮的脚本"的那条规则是：**应用层永远不直接跟模型对话。** 它只对队列说话。只有推理层（一个独立进程）才持有连接模型 provider 的客户端。正是这条规则，让"这个模型同时最多只能有效处理 N 个并发请求"变成了系统里一条真正被强制执行的属性，而不是一句没人会去读的注释。

## 2. 为什么要有队列，以及为什么选 Celery

如果 `app/` 在请求处理函数里直接调用模型 provider，有两件事会变得没法正确做到：

- **在模型这一端限制并发数。** 一个本地的 Ollama 实例（用来代替一个受 GPU 限制的推理引擎）在延迟对所有人都开始变差之前，只能有效服务少量并发生成。没有队列的话，"N 个请求正在处理中，下一个请求要等"这件事根本没地方安身——只能重新发明成一个进程内的信号量（semaphore），而这种东西扛不住进程重启，一旦 API 实例超过一个也会立刻失效。
- **把"接受请求"和"执行请求"这两件事解耦。** API 应该能立刻说一句"我已经收到你的请求了"，然后继续响应新的连接、健康检查、取消请求，同时推理在别处进行。正是队列，让"已接受"和"正在执行"变成了两个不同的时间点。

选 Celery（broker 用 Redis，跟会话、限流共用同一个 Redis）而不是 `arq` 或者手写一个 asyncio 队列，是因为它是 Python 生态里成熟、开箱即用的选择：内置带退避的重试机制、一套经过充分验证的 worker 并发模型，而且 Flower 能免费提供一个队列/worker 监控面板（见 `ROADMAP.md` 第 3 阶段）。这里做出的权衡是刻意摆明的，而不是后来才发现的——Celery 的默认配置是为"发完就不管的后台任务"调的，不是为"一个长时间运行、还要把部分输出实时流出去的任务"调的。有两件具体的事必须刻意配置，不能保留默认值，这两件事都在 `ERROR_HANDLING.md` 里有覆盖：

- `task_acks_late=True` + `task_reject_on_worker_lost=True` —— 不这么配的话，Celery 会在 worker *刚接到*任务的那一刻就确认（ack）并丢弃这个任务的记录，而不是等它真正跑完才确认。一个在推理过程中崩溃的 worker，会悄悄把这个任务弄丢，而不是把它放回队列重试。
- 取消是通过协作式的方式处理的（一个 Redis 标志位，在每个 token chunk 之间被轮询检查），而不是通过 `task.revoke(terminate=True)`——在 Celery 默认的 prefork 进程池模型下，`terminate=True` 会给整个 worker 进程发 `SIGTERM`，这比"停止这一次生成"要粗暴得多。

## 3. 为什么 worker 要是一个独立于 API 的进程

这是除了字面意义上的"请求/响应循环"之外，唯一一个刻意做出的结构性决策，所以值得专门拿出来论证一下。

一个"生产者和消费者都在同一个进程里"的队列，本质上不算是真正的队列——它只是一个多加了几道手续的待办清单。一个队列在生产级服务系统里真正值得拥有的那些特性，只有在生产者和消费者可以独立伸缩、独立失败、独立重启的前提下才会显现出来：

- **`app/`**（FastAPI，跑在 Uvicorn 之下）完全根据 HTTP 并发量（连接数、认证检查、SSE 扇出）来做水平扩展——它不做任何跟模型绑定的工作，所以它永远不需要知道现在有多少个模型副本存在。
- **`worker/`**（Celery）根据推理容量来扩展——每一个可用的模型副本对应一个 worker 进程（或者 `--concurrency=N`），直接表达出"这个 worker 最多同时跑 N 次 provider 调用"这件事。
- 一个 worker 崩溃（比如模型 provider 失去响应、进程被监督程序杀掉）不会把 API 也带垮——正在进行中的 SSE 连接会优雅降级（见 `ERROR_HANDLING.md`），而不是整个服务返回 502。
- 这也如实反映了一家真正运营真实 GPU 基础设施的公司会怎么搭建这套系统：持有推理引擎客户端连接的那个进程，永远不会是同一个负责终止公网连接的进程。

这种拆分带来的代价是：流式传输必须跨越一个进程边界——用 Redis pub/sub 解决，详见第 5 节。

## 4. 模型 provider 边界

`ModelProvider`（`providers/base.py` 里的一个 `typing.Protocol`）是 worker 被允许依赖的、唯一能跟模型对话的东西：

```python
class ModelProvider(Protocol):
    async def chat(
        self, messages: list[ChatMessage], *, cancel_check: Callable[[], bool],
    ) -> AsyncIterator[ChatChunk]: ...

    async def health_check(self) -> ProviderHealth: ...
```

- **`OllamaProvider`** —— 用于本地开发，通过 HTTP 调用 Ollama 那个兼容 OpenAI 的 `/v1/chat/completions` 接口（`stream: true`）。它原生运行在 Mac mini 宿主机上（不在 Docker 里——原因见 `DEPLOYMENT.md` 第 1 节），所以容器要通过 `host.docker.internal:11434` 来调用它。
- **`VLLMProvider`** —— 未来生产环境下同一个接口的实现，目标是一台运行 vLLM 兼容 OpenAI 服务器的 NVIDIA GPU 主机。这个项目里没有构建它（因为没有可用的 GPU 集群），但接口的设计目标就是让切换到它只是一次配置修改（`MODEL_PROVIDER=ollama|vllm`），而不是一次重写。
- **`MockProvider`** —— 为测试提供确定性的预设响应，这样 `worker/` 的测试就完全不需要依赖一个正在运行的真实模型。

在这个接口之上的任何东西——队列逻辑、会话逻辑、API 层——都不知道、也不关心底层运行时具体是不是 Ollama。这直接落实了 PRD 里关于 Ollama vs. vLLM 的那条权衡取舍，也正是为什么"本地/Mac 环境的限制"完全不会渗透进应用代码的任何一个角落。

## 5. 如何在 API/worker 的拆分之间桥接流式传输

这是整个系统里最容易出错、也最值得能够精确讲清楚的一段时序（图示见 `SEQUENCE_DIAGRAMS.md` 第 1 节）：

1. 客户端 `POST /chat` → API。API 完成认证、限流检查、持久化用户消息，生成一个 `request_id`，派发一个 Celery 任务 `run_inference.delay(request_id, conversation_id, messages)`，然后立刻打开一个 SSE 响应——它**不会**等任务被真正捞起来才返回。
2. API 在把控制权交给 SSE 生成器之前，先订阅 Redis 的 pub/sub 频道 `stream:{request_id}`，这样任何 chunk 都不可能在订阅者存在之前就被发布出去。
3. Worker 捞起任务（遵守它配置的 `--concurrency`），调用 `provider.chat(...)`，每收到一个 token chunk，就把它 `PUBLISH` 到 `stream:{request_id}`。
4. API 的订阅循环把每一条收到的消息，按接收顺序转发成一个 SSE 的 `data:` 事件给客户端。
5. 流结束时（无论是正常完成、出错，还是被取消——见 `ERROR_HANDLING.md`），worker 会在同一个频道上发布一个终态事件（`{"type": "done", ...}` 或者 `{"type": "error", "code": ...}`）；API 转发这个事件，关闭 SSE 响应，并取消订阅。

任何一个 API 进程实例都能服务这个客户端，任何一个 worker 都能执行这个任务，因为 Redis pub/sub 是它们之间唯一的耦合点——双方都不需要知道对方的进程身份。

## 6. 模块职责

**`app/`**（FastAPI）

| 模块 | 职责 | 明确不负责 |
|---|---|---|
| `app/core/auth.py` | JWT + API key 依赖项、身份解析 | 限流决策 |
| `app/core/rate_limit.py` | 基于 Redis 的滑动窗口、429 响应 | 队列深度或推理并发数 |
| `app/api/chat.py` | `POST /chat`、SSE 响应、请求校验（Pydantic） | 直接跟模型对话 |
| `app/services/session_service.py` | 对话的增删改查、历史记录获取（Postgres + Redis 缓存） | 判断一个对话什么时候算"结束" |
| `app/services/queue_service.py` | 派发 Celery 任务，负责 pub/sub 的订阅/转发桥接 | 执行推理 |
| `app/api/health.py` | 汇总 Postgres、Redis、Celery，以及（通过一次轻量 ping）模型 provider 的健康状态 | —— |

**`worker/`**（Celery）

| 模块 | 职责 | 明确不负责 |
|---|---|---|
| `worker/tasks.py` | Celery 任务入口，持有 `ModelProvider` 实例，发布流式 chunk | HTTP 相关事务、认证、限流 |
| `worker/reliability.py` | 带退避的重试、熔断器状态、超时执行、协作式取消轮询 | 决定*是否*接受一个请求（那是 API 在派发之前的职责） |

**`providers/`**：`ModelProvider` 协议 + `OllamaProvider` / `VLLMProvider` / `MockProvider`，只被 `worker/` 导入。

**`shared/`**：`app/` 和 `worker/` 都会用到的 Pydantic 模型——任务载荷的形状、SSE 事件的形状、错误码枚举——这样一条消息在进程边界的两侧,含义永远是一致的。

## 7. 为什么这种拆分能让路由处理函数保持"薄"

规范里对编码的要求（"不要有巨大的处理函数，服务要单一职责"）是从上面的分层结构里自然推导出来的：`app/api/chat.py` 里的一个 FastAPI 路由恰好只做三件事——通过一个 Pydantic 模型校验请求（声明在函数签名里，不需要手动解析）、调用 `ChatService.send_message()`、返回一个绑定了 SSE 生成器的 `StreamingResponse`。`ChatService` 负责编排（检查限流 → 持久化消息 → 派发任务 → 订阅）；它自己并不实现限流、持久化或排队的具体逻辑——那些是各自只有一个职责的独立服务。没有哪一层会跨过它正下方的那一层直接去够更下面的东西（`app/` 从不导入 provider 的具体类；`worker/` 从不导入 FastAPI）。

## 8. 目前刻意还没决定的事情

- 精确的限流数值（每分钟请求数、突发允许量）——在 `API_SPEC.md` 里只是占位符，可以通过环境变量调整，现阶段没必要把这个问题过早定死。
- Postgres 是存完整的消息内容，还是让 Redis 保留唯一事实来源、Postgres 只做持久化溢出层——等 Phase 1 把对话保留期限的需求定下来之后，在 `DATA_MODEL.md` 里做决定。
- 多模型路由（属于加分项范围）——推迟到 `ROADMAP.md` 第 4 阶段之后；第 4 节里那条 provider 边界正是让这件事以后不用返工就能做到的关键。
- Mac mini 本地能舒服地跑动哪个尺寸的模型（`Qwen2.5:3b`/`llama3.2:3b` 作为稳妥的默认选择，如果统一内存足够，换更大尺寸只是一次配置修改）——这是一个硬件适配问题，不是架构问题，等 Ollama 真正在本地跑起来之后靠实测来解决。
