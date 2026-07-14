# Aurora（中文版）

> 本文档是 [`README.md`](../../../README.md) 的中文翻译，仅用于学习，内容以英文原版为准。

**一个生产级别的后端平台，用来通过可靠的 API 对外提供开源大语言模型服务——包括流式响应、会话管理、排队、可观测性和部署流程。**

Aurora 不是一个聊天机器人，而是聊天机器人运行所依赖的那个平台。

## 项目前提

Aurora AI 的研究团队花了六个月训练出一个模型 `Aurora-13B`。训练已经完成，GPU 集群也已经就绪。但一个模型 checkpoint 并不是一个产品——它没有 API、没有认证、没有限流、没有重试逻辑，甚至没有办法知道模型现在是不是健康。这个项目就是把一个训练好的 checkpoint，变成公众可以放心依赖的东西的那个平台。

在本地环境里，`Aurora-13B` 由一个通过 [Ollama](https://ollama.com) 提供的、兼容 OpenAI 接口的小型开源模型来代替——推理层是针对一个"接口"写的，而不是针对某个具体的运行时，所以底层模型可以随时替换，完全不需要改动应用代码。

## 为什么要做这个项目

这是一份用来展示"LLM 服务系统的生产级后端工程能力"的作品集项目：请求/会话处理、可靠性机制（限流、重试、熔断、背压）、可观测性、部署设计——这些都是 **AI 平台团队** 关心的问题，而不是 AI 应用团队关心的问题。这份代码里刻意不包含任何业务逻辑、RAG、Agent 或工具调用。具体的"明确不做什么"以及原因见 [`docs/PRD.md`](../../PRD.md)。

## 文档

设计先于代码。从这里开始看：

| 文档 | 内容 |
|---|---|
| [`docs/PRD.md`](../../PRD.md) | 问题陈述、目标/非目标、分阶段范围、成功标准 |
| [`docs/ARCHITECTURE.md`](../../ARCHITECTURE.md) | 系统分层、模块边界、技术选型及理由 |
| [`docs/DATA_MODEL.md`](../../DATA_MODEL.md) | Postgres 表结构、Redis key 设计、队列任务格式 |
| [`docs/API_SPEC.md`](../../API_SPEC.md) | 接口、请求/响应契约、SSE 事件格式、错误码 |
| [`docs/SEQUENCE_DIAGRAMS.md`](../../SEQUENCE_DIAGRAMS.md) | 请求流程、流式传输、取消、重试、部署——以 Mermaid 时序图呈现 |
| [`docs/ERROR_HANDLING.md`](../../ERROR_HANDLING.md) | 系统设计要能扛住的每一种失败场景，以及怎么扛 |
| [`docs/DEPLOYMENT.md`](../../DEPLOYMENT.md) | Docker Compose 拓扑、checkpoint 发布流水线、回滚 |
| [`docs/ROADMAP.md`](../../ROADMAP.md) | 分阶段构建计划（MVP → 生产加固 → 可观测性 → 部署），每个阶段都有完成标准 |

## 当前状态

设计阶段。目前还没有应用代码——为什么这是刻意的、以及什么条件下才能解锁 Phase 1（写代码），见 `docs/ROADMAP.md`。

> （学习提示：这份中文翻译生成时，Phase 1 已经完成并且在真实环境里验证通过，具体过程见 `docs/DEVLOG.md`。）

## 技术栈

Python 3.12 · FastAPI · Celery（Redis 作 broker）· PostgreSQL（SQLAlchemy + Alembic）· Redis · Ollama（本地，原生运行，用 Metal 加速）/ vLLM（未来生产环境用，通过统一的 provider 接口）· Docker Compose · Prometheus/Grafana · OpenTelemetry/Jaeger · Flower

## 仓库目录结构

```
app/          FastAPI 服务 —— API 层（认证、限流、路由、SSE）
worker/       Celery worker —— 推理层（重试、熔断、流式转发）
providers/    ModelProvider 接口 + OllamaProvider / VLLMProvider / MockProvider
shared/       app/ 和 worker/ 共用的 Pydantic 模型（任务载荷、SSE 事件、错误码）
migrations/   Alembic 数据库迁移文件
docker/       Docker Compose + 各服务配置
docs/         上面表格里列出的所有文档
scripts/      deploy.py / rollback.py，压测脚本
tests/
```
