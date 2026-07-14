# Aurora —— 数据模型（中文版）

> 本文档是 [`docs/DATA_MODEL.md`](../../DATA_MODEL.md) 的中文翻译，仅用于学习，内容以英文原版为准。SQL/Python 代码块保留英文原文。

三个存储，三种不同的职责。任何数据只要在两处重复出现，这里都会写明原因。

## 1. PostgreSQL —— 持久化的事实来源（system of record）

拥有一切"必须扛得住 Redis 被清空"的数据，以及未来分析/评估要用到的数据（Phase 1 的"评估"属于加分项范围）。

```sql
CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  api_key_hash  TEXT UNIQUE,          -- 如果这个身份从头到尾只用 JWT，这里就是 null
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id),
  title         TEXT,                 -- 从第一条消息推导出来，可以为空
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  role            TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content         TEXT NOT NULL,
  model           TEXT,               -- 是哪个模型/版本回答的，用户消息这里为 null
  latency_ms      INTEGER,            -- 用户消息这里为 null
  prompt_tokens   INTEGER,
  completion_tokens INTEGER,
  retry_count     INTEGER NOT NULL DEFAULT 0,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_messages_conversation ON messages(conversation_id, created_at);

CREATE TABLE feedback (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  message_id    UUID NOT NULL REFERENCES messages(id),
  rating        SMALLINT NOT NULL CHECK (rating IN (-1, 1)),  -- 点踩 / 点赞
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE model_deployments (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  checkpoint_tag  TEXT NOT NULL,       -- 例如 'checkpoint-v17'
  status          TEXT NOT NULL CHECK (status IN ('registered','warming','canary','active','rolled_back')),
  traffic_pct     SMALLINT NOT NULL DEFAULT 0,
  promoted_at     TIMESTAMPTZ,
  rolled_back_at  TIMESTAMPTZ
);
```

`messages` 表就是 JD 里"日志、遥测和评估信号"这句话直接对应的那份只增不改的审计日志——每一次助手回复，都会记录它花了多久、用了多少 token、重试了几次，不需要去翻应用日志就能回答"这条回复是不是因为重试才变慢的"这类问题。

## 2. Redis —— 热路径状态，没有任何东西被要求自发扛过一次重启

| Key 模式 | 类型 | TTL | 用途 |
|---|---|---|---|
| `session:{conversationId}` | Hash | 滑动，24 小时 | 缓存最近的消息窗口，让 worker 不用每一轮对话都去查一次 Postgres |
| `ratelimit:{identityId}` | Sorted set / 计数器 | 窗口长度（例如 60 秒） | 限流计数器——算法见 `ERROR_HANDLING.md` 的"限流"一节 |
| `stream:{requestId}` | Pub/Sub 频道 | 不适用（本来就是临时的） | Worker → 网关的 token 转发（见 `ARCHITECTURE.md` 第 5 节） |
| `cancel:{requestId}` | 字符串标志位 | 5 分钟 | 由网关在客户端断开连接时写入；worker 在每个 chunk 之间轮询检查 |
| `circuit:inference` | Hash（`state`、`failureCount`、`openedAt`） | 不适用，显式管理 | 熔断器状态，跨所有 worker 副本共享 |

Redis 明确地**不是**任何数据的事实来源——这里的每一个 key，要么是 Postgres 的缓存，要么是一个协调用的标志位，要么本来就是真正意义上的临时数据（一条 pub/sub 消息只在被发送的那一瞬间有意义）。如果 Redis 被清空，系统会丢失正在进行中的流式传输和限流窗口，但不会丢失对话历史。

## 3. Celery 任务载荷

在 `shared/` 里以一个 Pydantic 模型的形式只定义一次，被 `app/`（用来构造它）和 `worker/`（用来解析它）同时导入——这样这两个进程就永远不可能在这个数据形状上悄悄产生分歧：

```python
class InferenceTaskPayload(BaseModel):
    request_id: str          # 等同于 SSE 关联 id，也等同于 stream:{request_id} 频道名
    conversation_id: str
    user_id: str
    messages: list[ChatMessage]  # 完整的上下文窗口，在派发之前由 API 解析好
    model: str                # 要路由到哪个模型/版本（加分项：多模型路由）
    enqueued_at: datetime      # 用来计算排队等待时长，供指标使用
```

解析完整的消息上下文（从 Redis/Postgres 里拉历史记录）这件事发生在 `app/` 里，并且是在任务被派发之**前**，而不是在 `worker/` 里——这样可以让 worker 唯一的外部依赖始终只剩模型 provider，也让"我们实际发给模型的是什么"完全由这份任务载荷决定，这意味着仅凭任务数据本身，就能够重放或审计某一次具体的请求。第 1 节里那些 Postgres 表用的 ORM 是 SQLAlchemy 2.0（异步版本），schema 迁移由 Alembic 管理（`migrations/`）。

## 4. 为什么不干脆只用一个存储

一个很有诱惑力的简化方案是"所有东西都用 Postgres 存，别用 Redis 了"。这个方案被否决了，因为限流计数器和 pub/sub 流式传输本质上都是"高频率、低持久性要求"的操作——如果把它们都路由经过 Postgres，就意味着每一个 token chunk 都会变成一次数据库写入，而限流检查（必须在每一个请求上都发生，包括被拒绝的请求）也会给热路径带来关系型数据库的延迟。Redis 才是这里正确的工具，因为它在这里承担的任何职责都不需要 ACID 保证；它们需要的是速度，以及基于 TTL 的自动清理。
