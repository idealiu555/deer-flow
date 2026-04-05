# DeerFlow 后端架构指南

> 目标读者：希望快速建立 DeerFlow 后端全局认知，并能顺着代码定位实现细节的开发者
>
> 文档范围：基于当前仓库代码，而不是历史设计稿。重点覆盖 Agent 组装、中间件链、Scheduler、Checkpointer、Memory、IM Channels。

---

## 目录

1. [项目概览](#1-项目概览)
2. [启动入口与运行时组成](#2-启动入口与运行时组成)
3. [Lead Agent 的组装过程](#3-lead-agent-的组装过程)
4. [工具、Skills 与子智能体](#4-工具skills-与子智能体)
5. [中间件链](#5-中间件链)
6. [Scheduler：基于 Agent/LLM 意图理解的定时任务](#6-scheduler基于-agentllm-意图理解的定时任务)
7. [Checkpointer 与 Memory：短期状态和长期记忆](#7-checkpointer-与-memory短期状态和长期记忆)
8. [IM Channels：飞书等外部消息接入](#8-im-channels飞书等外部消息接入)
9. [Reflection 动态加载系统](#9-reflection-动态加载系统)
10. [Gateway API 与关键文件](#10-gateway-api-与关键文件)
11. [架构要点总结](#11-架构要点总结)

---

## 1. 项目概览

### 1.1 技术栈

| 组件 | 作用 |
| --- | --- |
| Python 3.12+ | 后端主语言 |
| LangGraph + LangChain | Agent 编排、状态持久化、工具调用 |
| FastAPI | Gateway API |
| SQLite | Scheduler 元数据存储，以及可选的 Checkpointer 持久化后端 |
| MCP | 外部工具协议 |
| Skills | 通过 `SKILL.md` 向 Agent / Subagent 注入额外工作流知识与约束 |

### 1.2 后端核心职责

DeerFlow 后端不是单一进程，而是三层协作：

1. `LangGraph Server`
   负责实际执行 Lead Agent、维护线程状态、调用工具和中间件。
2. `Gateway`
   提供 `/api/models`、`/api/memory`、`/api/schedules`、`/api/channels` 等管理接口，并在启动时拉起 Scheduler / ChannelService。
3. `Scheduler / ChannelService`
   分别负责定时任务轮询执行，以及飞书/Telegram 等外部消息渠道接入。

前端、Nginx、Gateway、LangGraph Server 的关系如下：

```text
Browser / IM Client
        |
        v
      Nginx
   /api/langgraph  -> LangGraph Server
   /api/*          -> Gateway
        |
        +-> Gateway 启动时拉起 SchedulerService / ChannelService
```

### 1.3 当前后端目录结构

```text
backend/
├── langgraph.json
├── src/
│   ├── agents/
│   │   ├── lead_agent/       # Lead Agent 工厂函数与系统提示词
│   │   ├── middlewares/      # 11 个中间件组件
│   │   ├── memory/           # Memory 提取、队列、持久化
│   │   ├── thread_state.py   # ThreadState 状态结构定义
│   │   └── checkpointer/     # 同步/异步 Checkpointer 适配层
│   ├── tools/
│   │   ├── tools.py         # 工具加载入口 get_available_tools()
│   │   └── builtins/        # 内置工具 (present_files, ask_clarification, schedule, task, view_image)
│   ├── subagents/
│   │   ├── executor.py       # 后台执行引擎
│   │   ├── registry.py       # 子智能体注册表
│   │   └── builtins/        # 内置子智能体 (general-purpose, bash)
│   ├── scheduler/
│   │   ├── service.py        # 调度服务轮询器
│   │   ├── store.py          # SQLite 持久化层
│   │   └── draft_actions.py  # Draft 确认执行逻辑
│   ├── channels/
│   │   ├── service.py        # Channel 生命周期管理
│   │   ├── manager.py        # 消息调度核心
│   │   ├── message_bus.py   # 异步发布/订阅总线
│   │   ├── store.py          # channel→thread 映射存储
│   │   ├── feishu.py         # 飞书接入实现
│   │   └── telegram.py       # Telegram 接入实现
│   ├── gateway/
│   │   ├── app.py            # FastAPI 入口与路由注册
│   │   └── routers/          # 6 个路由模块 (models, mcp, memory, skills, artifacts, uploads, channels, schedules)
│   ├── mcp/                  # MCP 集成 (tools, cache, client, oauth)
│   ├── skills/               # Skills 发现、加载、解析
│   ├── sandbox/
│   │   ├── sandbox.py        # 抽象 Sandbox 接口
│   │   ├── middleware.py     # Sandbox 生命周期中间件
│   │   ├── tools.py          # 沙箱工具 (bash, ls, read_file, write_file, str_replace)
│   │   └── local/            # 本地文件系统沙箱实现
│   ├── models/               # 模型工厂 (thinking/vision 支持)
│   ├── community/
│   │   ├── tavily/           # Tavily Web Search / Web Fetch
│   │   └── image_search/     # DuckDuckGo 图片搜索
│   ├── reflection/           # 动态模块加载 (resolve_variable, resolve_class)
│   ├── config/               # 配置系统 (app, model, sandbox, scheduler, memory 等)
│   └── utils/                # 工具函数 (network, readability)
└── tests/
```

---

## 2. 启动入口与运行时组成

### 2.1 LangGraph 入口

LangGraph 入口定义在 `backend/langgraph.json`：

```json
{
  "$schema": "https://langgra.ph/schema.json",
  "dependencies": ["."],
  "env": ".env",
  "graphs": {
    "lead_agent": "src.agents:make_lead_agent"
  },
  "checkpointer": {
    "path": "./src/agents/checkpointer/async_provider.py:make_checkpointer"
  }
}
```

这说明两件事：

1. `lead_agent` 图的工厂函数是 `src.agents:make_lead_agent`
2. LangGraph Server 使用异步 checkpointer 工厂 `make_checkpointer()`，因此服务端线程状态持久化也会走 DeerFlow 自己的 checkpointer 适配层

### 2.2 Gateway 入口

Gateway 入口是 `backend/src/gateway/app.py`。

它在 FastAPI `lifespan` 中完成三件关键工作：

1. 读取 `config.yaml`
2. 启动 `ChannelService`
3. 启动 `SchedulerService`

因此：

- LangGraph Server 负责“跑 Agent”
- Gateway 负责“管外围服务和管理接口”

### 2.3 配置加载方式

主配置由 `backend/src/config/app_config.py` 解析，实际查找优先级是：

1. 显式传入的 `config_path`
2. 环境变量 `DEER_FLOW_CONFIG_PATH`
3. 当前工作目录下的 `config.yaml`
4. 当前工作目录父目录下的 `config.yaml`

当前实现支持三种环境变量写法：

- `$VAR`
- `${VAR}`
- `${VAR:-default}`

这对 Docker 部署很重要，例如当前 `channels.langgraph_url`、`channels.gateway_url`、`feishu.app_id`、`feishu.app_secret` 都支持这种占位。

---

## 3. Lead Agent 的组装过程

### 3.1 核心工厂函数

Lead Agent 由 `backend/src/agents/lead_agent/agent.py` 中的 `make_lead_agent(config)` 创建。

大致流程是：

1. 解析运行时参数
2. 解析最终模型名
3. 加载工具
4. 构造中间件链
5. 生成系统提示词
6. 调用 `create_agent(...)`

### 3.2 运行时配置来源

`make_lead_agent` 主要从 `config["configurable"]` 读取这些开关：

| 字段 | 作用 |
| --- | --- |
| `thinking_enabled` | 是否启用模型思考模式 |
| `reasoning_effort` | 推理力度 |
| `model_name` / `model` | 指定运行模型 |
| `is_plan_mode` | 是否启用 Todo 规划模式 |
| `subagent_enabled` | 是否允许 `task` 子智能体委托 |
| `max_concurrent_subagents` | 子智能体并发上限 |

这里有一个容易误判的细节：

- `_resolve_model_name()` 自身具备“非法模型名回退默认模型”的能力
- 但当前 `make_lead_agent()` 只有在“未显式传入 `model_name` / `model`”时才调用它
- 如果请求里显式传入了一个不存在的模型名，当前实现会在后续拿不到 `model_config` 时直接抛错，而不是静默回退

因此文档和接入方都应该按“显式非法模型名会失败”来理解当前行为。

### 3.3 `ThreadState`：线程级状态结构

线程状态定义在 `backend/src/agents/thread_state.py`。

当前 DeerFlow 关心的核心字段有：

| 字段 | 作用 |
| --- | --- |
| `sandbox` | 沙箱状态 |
| `thread_data` | 当前 thread 的 workspace / uploads / outputs 目录 |
| `title` | 自动生成的会话标题 |
| `artifacts` | 产物文件列表，带 reducer 去重 |
| `todos` | Todo 模式任务列表 |
| `uploaded_files` | 本轮上传文件元数据 |
| `viewed_images` | 已读取图片内容缓存 |

几个实现细节值得注意：

- `artifacts` 使用 `merge_artifacts` 做增量合并和去重
- `viewed_images` 使用 `merge_viewed_images`，支持用空字典清空状态
- 运行时上下文 `ThreadRuntimeContext` 里还会携带 `thread_id`、`channel_name`、`chat_id`、`user_id`、`assistant_id` 等跨模块信息

---

## 4. 工具、Skills 与子智能体

### 4.1 工具加载逻辑

工具统一由 `backend/src/tools/tools.py` 的 `get_available_tools(...)` 组装。

真正注册成 LangChain Tool 的能力来源分四类：

1. `config.yaml` 中声明的**普通工具**（含 Community Tools）
2. DeerFlow **内置工具**
3. **MCP 工具**
4. **沙箱工具**（bash, ls, read_file, write_file, str_replace）

其中 MCP 还有两个实现细节值得明确：

1. MCP 服务的启停配置来自 `extensions_config.json`，而不是 `config.yaml`
2. Gateway 只负责 MCP 配置管理；真正的 MCP tool 加载发生在 LangGraph 侧，并通过缓存 + 配置文件 mtime 检测实现懒加载 / 失效重载

**Community Tools** 是通过 `config.yaml` 配置的工具，代码位于 `backend/src/community/`：

| 工具 | 配置 group | 说明 |
| --- | --- | --- |
| `web_search` | `web_search` | Tavily 搜索（默认 5 条结果）|
| `web_fetch` | `web_search` | Tavily 网页内容提取（4KB 限制）|
| `image_search` | `image_search` | DuckDuckGo 图片搜索 |

这些工具需要先在 `config.yaml` 的 `tools` 段声明 `use` 路径后才能使用。

当前内置工具集合是：

| 工具 | 是否默认启用 | 说明 |
| --- | --- | --- |
| `present_files` | 是 | 向用户暴露生成产物 |
| `ask_clarification` | 是 | 请求用户补充信息 |
| `schedule` | 是 | 定时任务管理 |
| `task` | 仅 `subagent_enabled=true` | 委托子智能体 |
| `view_image` | 仅模型支持视觉 | 读取图片内容 |

### 4.2 Skills 不是 Tool，而是 Prompt 级能力扩展

这是当前代码里最容易被写错的地方。

`Skills` 并不会出现在 `get_available_tools(...)` 返回值中；它们的接入方式是：

1. `backend/src/skills/loader.py` 扫描 `skills/public` 和 `skills/custom` 下的 `SKILL.md`
2. `extensions_config.json` 中的 `skills` 开关决定哪些 skill 处于 enabled 状态
3. `backend/src/agents/lead_agent/prompt.py` 在生成系统提示词时，把已启用 skill 的名称、描述和容器内路径注入 `<skill_system>` 区块
4. `task` 工具在创建子智能体时，也会把同一份 skills section 追加到 subagent 的 system prompt

> **注意**：`task` 工具创建子智能体时，会把 skills section 追加到 subagent 的 system prompt 中，这点和 Lead Agent 是一致的。

因此 DeerFlow 的三类“能力扩展”应当更准确地理解为：

1. 内置工具 / 普通工具：真正的 Tool 调用能力
2. MCP：外部 Tool 提供者
3. Skills：Prompt 级工作流知识、最佳实践和资源入口

这和“Skills 也是一类工具”并不相同。

### 4.3 子智能体机制

`task` 工具定义在 `backend/src/tools/builtins/task_tool.py`。

它不是把任务丢给外部队列，而是在当前后端进程内：

1. 组装子智能体专用 Agent
2. 过滤允许使用的工具
3. 通过后台线程池异步执行
4. 由主 Agent 侧轮询执行结果并推流展示进度

当前内置子智能体类型：

| 类型 | 说明 |
| --- | --- |
| `general-purpose` | 通用多步任务 |
| `bash` | 命令执行专家 |

当前并发控制有两层：

1. Lead Agent 侧可通过 `max_concurrent_subagents` 运行时参数控制本轮最多保留多少个并行 `task` 调用
2. 子智能体后台线程池的硬上限仍由 `backend/src/subagents/constants.py` 中的 `MAX_CONCURRENT_SUBAGENTS = 3` 决定

也就是说，当前实现的真实执行上限仍然受固定线程池大小约束。

### 4.4 子智能体和主智能体的边界

设计上有两个重要约束：

1. 子智能体不会再次暴露 `task` 工具，避免递归套娃
2. 子智能体复用主线程的 sandbox / thread_data，保证文件操作仍然落在同一个会话空间

---

## 5. 中间件链

### 5.1 不要把中间件数量当成固定值

历史文档里常把中间件写成“固定 12 个”或“固定 13 个”，这在当前代码里已经不准确。

当前中间件链由两部分组成：

1. 基础运行时中间件：`build_lead_runtime_middlewares(...)`
2. Lead Agent 特有中间件：`_build_middlewares(...)`

其中有多项是条件启用的，所以数量不是常量。

### 5.2 基础运行时中间件

`build_lead_runtime_middlewares()` 当前顺序如下：

1. `ThreadDataMiddleware`
2. `UploadsMiddleware`
3. `SandboxMiddleware`
4. `DanglingToolCallMiddleware`
5. `ToolErrorHandlingMiddleware`

职责概括如下：

| 中间件 | 作用 |
| --- | --- |
| `ThreadDataMiddleware` | 为 thread 建立工作目录并注入路径状态 |
| `UploadsMiddleware` | 把上传文件写入 thread 目录，并向消息中注入 `<uploaded_files>` 信息 |
| `SandboxMiddleware` | 获取或挂载沙箱 |
| `DanglingToolCallMiddleware` | 修补历史里缺失的 `ToolMessage`，避免模型看到非法消息序列 |
| `ToolErrorHandlingMiddleware` | 把工具异常转成 `ToolMessage(status="error")`，不中断整轮运行 |

### 5.3 Lead Agent 额外中间件

`_build_middlewares(config, model_name)` 会在基础链后继续追加：

1. `SummarizationMiddleware`，如果开启总结压缩
2. `TodoMiddleware`，如果 `is_plan_mode=true`
3. `TitleMiddleware`
4. `MemoryContextMiddleware`
5. `MemoryMiddleware`
6. `ViewImageMiddleware`，如果模型支持视觉
7. `SubagentLimitMiddleware`，如果 `subagent_enabled=true`
8. `ClarificationMiddleware`，始终最后

也就是说，默认链路不是固定长度，而是“基础链 + 若干能力链”。

### 5.4 几个关键中间件的真实行为

#### 5.4.1 `MemoryContextMiddleware`

它不是往 `state` 里永久写入 `memory_context` 字段，而是通过 `wrap_model_call` 在每次模型调用前临时插入一条 `SystemMessage`：

```text
<memory>
...
</memory>
```

这是一个更稳妥的实现：

- 不污染持久化线程状态
- 每次调用都能拿到最新 memory.json
- 失败时只跳过注入，不影响主链路

#### 5.4.2 `MemoryMiddleware`

它也不是 `after_model`，而是 `after_agent` 执行。

当前真实逻辑是：

1. 读取整轮 `messages`
2. 只保留 human 消息和最终 AI 消息
3. 去掉 `<uploaded_files>` 这类不应该沉淀到长期记忆里的临时信息
4. 交给异步 `MemoryUpdateQueue`

因此它处理的是“整轮执行结果”，而不是单次模型调用结果。

#### 5.4.3 `ClarificationMiddleware`

它必须放在最后，原因不是风格偏好，而是控制流需要：

- 前面的工具异常要先被 `ToolErrorHandlingMiddleware` 转换
- 图片信息要先被 `ViewImageMiddleware` 注入
- 最后才由 `ClarificationMiddleware` 截获 `ask_clarification`

---

## 6. Scheduler：基于 Agent/LLM 意图理解的定时任务

### 6.1 定位

DeerFlow 的 Scheduler 不是通用任务队列，而是：

“以 Agent prompt 为执行单元的时间驱动调度器”

它把“是否需要定时任务、该创建什么任务”的意图理解交给 Agent / LLM，把真正的持久化、确认、轮询和执行交给调度层。

换句话说：

- LLM 负责理解用户意图，并构造 `schedule` 工具调用参数
- `schedule` 工具和 `SchedulerStore / SchedulerService` 只处理结构化调度语义，不再自己解析自然语言

### 6.2 整体链路

```text
用户自然语言
   -> Lead Agent 调用 schedule 工具
   -> 生成 draft
   -> 用户确认
   -> SchedulerStore 持久化 schedule
   -> SchedulerService 轮询 claim 到期任务
   -> 重新调用 LangGraph runs.wait 执行 prompt
   -> 记录 run 历史
   -> 如有 channel 目标则投递结果
```

### 6.3 `schedule` 工具的当前动作集

`schedule` 工具位于 `backend/src/tools/builtins/schedule_tool.py`。

当前支持这些 action：

| action | 作用 |
| --- | --- |
| `status` | 查看调度器状态统计 |
| `list` | 按 owner 范围列出 schedule |
| `add` | 创建新增任务草稿 |
| `update` | 创建更新任务草稿 |
| `remove` | 创建删除任务草稿 |
| `run` | 创建“立即执行一次”草稿 |
| `runs` | 查看某个 schedule 的执行历史 |
| `wake` | 立即唤醒调度循环 |
| `confirm` | 确认并执行 draft |

几个关键实现点：

1. 工具模式下，`add / update / remove / run` 一律 draft-first
2. `confirmed` 参数在工具模式里仅保留兼容性，不再直接绕过确认
3. `update / remove / run / runs` 会先校验目标 `schedule_id` 是否存在
4. `confirm` 在某些情况下可以自动解析唯一草稿，不必显式传 `draft_id`

### 6.4 Draft 机制的真实规则

草稿执行逻辑在 `backend/src/scheduler/draft_actions.py`。

当前的安全约束比“用户说确认就执行”更严格：

1. draft 带 TTL，默认 86400 秒，也就是 24 小时
2. `confirm` 默认要求“后续用户消息”触发，而不是原始创建消息本身
3. 如果同一 owner / thread 下只有一个可确认 draft，工具可以自动解析 `draft_id`
4. 如果存在多个匹配草稿，则必须显式指定 `draft_id`

这能避免模型在同一轮里“自己创建又自己确认”。

### 6.5 Schedule 规范化

新增和更新都会做 canonicalization：

- `one_time` / `single` / `oneoff` 会统一成 `once`
- `recurring` / `repeat` 会统一成 `cron`
- `cron` / `at` / `timezone` 会被统一校验

这部分逻辑现在已经收敛到：

- `normalize_add_schedule_payload(...)`
- `normalize_schedule_patch_payload(...)`

避免 add / update 两条链路各自做一套 kind 兼容。

### 6.6 `SchedulerStore`：持久化与去重执行

`backend/src/scheduler/store.py` 用 SQLite 维护三类数据：

| 表 | 作用 |
| --- | --- |
| `schedules` | 已持久化的定时任务 |
| `schedule_runs` | 每次执行历史 |
| `schedule_drafts` | 待确认草稿 |

当前 `schedules` 表里的重要字段包括：

| 字段 | 说明 |
| --- | --- |
| `owner_key` | 任务归属范围，例如 `web:settings` 或 `feishu:<user_id>` |
| `channel_name/chat_id/topic_id` | 如果任务源于 IM，会保留投递目标 |
| `thread_id` | 关联 DeerFlow 会话线程 |
| `assistant_id` | 执行时使用的 assistant |
| `prompt` | 实际会重复执行的用户任务语义 |
| `kind` | `cron` 或 `once` |
| `next_run_at` | 下次执行时间 |
| `run_now_pending` | 已被 lease 领取时的“追加立即执行”标记 |
| `config_json/context_json` | 从当前运行快照下来的稳定运行参数 |
| `lease_owner/lease_expires_at` | 防重复执行租约 |

### 6.7 `run_now_pending` 的作用

这是当前实现里容易忽略但很关键的点。

如果某个任务已经被某个 scheduler worker claim 并开始执行，这时用户又触发一次“立即执行”，不能直接覆盖 lease。当前实现会：

1. 保持已有 lease
2. 把 `run_now_pending = 1`
3. 等本次执行完成后，`release_schedule_claim(...)` 再把下一次时间重置为“立刻再跑一次”

这样可以避免：

- 重复并发执行同一个 schedule
- 立即执行请求被吞掉

### 6.8 `SchedulerService`：时间驱动轮询执行

`backend/src/scheduler/service.py` 是后台轮询器，主要职责是：

1. `claim_due_schedules(...)` 领取到期任务
2. 为每个已领取任务启动异步执行
3. 周期性续租 lease
4. 执行结束后释放 claim，并计算下一次执行时间

它不是把 schedule 转换成某种内部 job handler，而是直接重新发起一次 LangGraph run：

```python
await client.runs.wait(
    thread_id,
    assistant_id,
    input={"messages": [{"role": "human", "content": schedule["prompt"]}]},
    config=run_config,
    context=run_context,
)
```

这就是为什么说它是“任务内容驱动、时间轮询调度”，而不是通用任务队列框架。

### 6.9 任务执行后的状态流转

调度任务执行完成后：

1. 新建 `schedule_runs` 记录
2. 成功则写入 `output`
3. 失败则写入 `error`
4. 对于 `once` 任务，执行后会自动转成 `paused`
5. 对于 `cron` 任务，重新计算 `next_run_at`
6. 如果有 channel 目标，则把结果通过 `ChannelService` 投递出去

### 6.10 REST API 与工具模式的区别

Scheduler 既有工具接口，也有 Gateway REST API。

Gateway 路由在 `backend/src/gateway/routers/schedules.py`，主要接口有：

| 路由 | 作用 |
| --- | --- |
| `GET /api/schedules` | 列表 |
| `GET /api/schedules/status` | 调度器状态 |
| `POST /api/schedules` | 创建任务 |
| `PATCH /api/schedules/{id}` | 更新任务 |
| `DELETE /api/schedules/{id}` | 删除任务 |
| `POST /api/schedules/{id}/trigger` | 立即执行 |
| `POST /api/schedules/drafts/{draft_id}/confirm` | 确认草稿 |
| `GET /api/schedules/{id}/runs` | 执行历史 |
| `POST /api/schedules/{id}/pause` | 暂停任务 |
| `POST /api/schedules/{id}/resume` | 恢复任务 |

和工具模式不同，REST API 支持 `confirmed=true` 直接执行，不一定强制 draft-first。

---

## 7. Checkpointer 与 Memory：短期状态和长期记忆

### 7.1 两者的边界

这部分最容易被讲混。

当前系统的职责边界是：

| 机制 | 作用 | 数据形态 |
| --- | --- | --- |
| Checkpointer | 会话短期状态持久化 | LangGraph checkpoint |
| Memory | 跨会话长期信息沉淀 | `memory.json` |

简化理解：

- Checkpointer 记“这个线程之前聊到哪了、工具状态是什么”
- Memory 记“当前系统沉淀下来的长期上下文、偏好和事实”

### 7.1.1 当前 Memory 是全局的，不按用户隔离

这点必须明确写出来。

当前 `memory.json` 是单份全局存储：

- `MemoryMiddleware` 只用 `thread_id` 做 debounce 队列键
- `MemoryUpdater` 最终写回的是同一个 `memory.json`
- `Gateway /api/memory` 返回的也是全局 memory，而不是按 `user_id` 或 `channel` 切分后的视图

因此，当前实现可以说是“长期记忆”或“长期偏好沉淀”，但不能严格说成“每个用户独立长期记忆”。

如果后续要做多租户或 IM 用户级隔离，需要在存储层和注入层引入 owner / user scope。

### 7.2 Checkpointer：当前真实实现

Checkpointer 适配层有两套：

| 文件 | 用途 |
| --- | --- |
| `backend/src/agents/checkpointer/provider.py` | 同步工厂，给嵌入式客户端或同步场景使用 |
| `backend/src/agents/checkpointer/async_provider.py` | 异步工厂，给 LangGraph Server 使用 |

支持的后端仍然是三种：

| 类型 | 说明 |
| --- | --- |
| `memory` | 进程内内存，不持久化 |
| `sqlite` | 本地 SQLite 文件 |
| `postgres` | PostgreSQL |

### 7.3 Checkpointer 的默认行为

当前代码的真实行为是：

1. 如果没有显式配置 `checkpointer`，会回退到 `InMemorySaver`
2. 如果配置了 `sqlite`，会自动创建数据库目录并执行 `setup()`
3. LangGraph Server 侧走 `make_checkpointer()` 异步上下文
4. 同步客户端侧走 `get_checkpointer()` 或 `checkpointer_context()`

所以它不是“必须配置才能运行”，而是“默认可运行，但默认不持久化”。

### 7.4 异步 SQLite Checkpointer 的增强能力

`async_provider.py` 里有一个 `EnhancedAsyncSqliteSaver` 包装层，除了常规 checkpoint 读写，还补充了几个维护接口：

| 方法 | 作用 |
| --- | --- |
| `adelete_thread` | 删除线程 checkpoint |
| `adelete_for_runs` | 按 run_id 删除相关 checkpoint |
| `acopy_thread` | 复制线程状态 |
| `aprune` | 保留最新 checkpoint 或全量删除 |

这说明 DeerFlow 不只是“用 LangGraph 默认持久化”，而是对线程维护做了额外封装。

### 7.5 Memory：长期记忆的数据结构

长期记忆保存在 `memory.json`，当前结构由 `backend/src/agents/memory/updater.py` 初始化，核心字段包括：

```json
{
  "version": "1.0",
  "lastUpdated": "...",
  "user": {
    "workContext": {"summary": "", "updatedAt": ""},
    "personalContext": {"summary": "", "updatedAt": ""},
    "topOfMind": {"summary": "", "updatedAt": ""}
  },
  "history": {
    "recentMonths": {"summary": "", "updatedAt": ""},
    "earlierContext": {"summary": "", "updatedAt": ""},
    "longTermBackground": {"summary": "", "updatedAt": ""}
  },
  "facts": []
}
```

`facts` 中每条事实包含：

- `id`
- `content`
- `category`
- `confidence`
- `createdAt`
- `source`

### 7.6 Memory 更新链路

真实更新流程如下：

1. `MemoryMiddleware.after_agent(...)` 收集整轮消息
2. 过滤掉 tool message 和带 tool_calls 的中间 AI message
3. 清理 `<uploaded_files>` 这类临时注入内容
4. `MemoryUpdateQueue` 以 thread 为粒度做 debounce
5. `MemoryUpdater` 调用 LLM 生成结构化更新
6. `_apply_updates(...)` 合并摘要和 facts
7. `_strip_upload_mentions_from_memory(...)` 再次清理上传事件残留
8. 原子写回 `memory.json`

### 7.7 Debounce 队列的实现特点

`backend/src/agents/memory/queue.py` 当前实现有两个重要特征：

1. debounce 是按 `thread_id` 独立计算的，不会跨线程互相饿死
2. 同一个 thread 的新更新会覆盖旧的 pending 更新

另外还有一个“最大等待时间”机制：

- 默认 `max_wait = debounce_seconds * 4`

这样可以防止高频会话永远因为持续抖动而不落盘。

### 7.8 Memory 注入的真实方式

当前不是“把 top 15 facts 拼成 state 字段再持久化”，而是：

1. 每次模型调用前动态读取最新 memory
2. `format_memory_for_injection(...)` 做格式化和 token 限制
3. 用一条 transient `SystemMessage` 注入

这比把 memory 混入 thread state 更合适，因为：

- 长期记忆天然是全局资源，不该复制进每个 checkpoint
- 更新 memory 后，下次调用立刻生效
- 注入失败时可以安全降级

### 7.9 上传内容为什么要从 Memory 中剔除

当前代码专门清理“上传了某个文件”这种事件型描述，原因很实际：

- 上传文件是会话态资源
- 下一次对话时这些文件路径通常已经不存在
- 如果把这些路径沉淀到长期记忆，Agent 反而会被误导去找不存在的文件

这也是当前 memory 实现比旧版本更稳的一个点。

---

## 8. IM Channels：飞书等外部消息接入

### 8.1 整体结构

Channels 相关代码位于 `backend/src/channels/`，主要分三层：

| 层级 | 文件 | 作用 |
| --- | --- | --- |
| 生命周期管理 | `service.py` | 启停所有 channel 和 manager |
| 消息调度 | `manager.py` | 入站消息分发、线程映射、调用 LangGraph |
| 平台实现 | `feishu.py` / `telegram.py` | 各平台 SDK 接入 |

### 8.2 `ChannelService`

`ChannelService` 负责：

1. 从 `config.yaml` 的 `channels` 段读取配置
2. 构造 `MessageBus`
3. 构造 `ChannelManager`
4. 启动所有 enabled channel

它不会强依赖某个平台必须成功启动。

例如当前飞书如果没配 `app_id` / `app_secret`，`FeishuChannel.start()` 会记录错误并返回，不会把整个 Gateway 直接拉挂。

### 8.3 `ChannelManager` 的消息流

`ChannelManager` 是真正把 IM 平台和 DeerFlow Agent 接上的桥。

一条普通消息的路径是：

1. channel SDK 收到消息，写入 `MessageBus.inbound`
2. `ChannelManager` 消费消息
3. 通过 `ChannelStore` 查找 `(channel_name, chat_id, topic_id)` 对应的 thread
4. 找不到就创建新 thread
5. 解析 session 配置，拼出 `assistant_id`、`run_config`、`run_context`
6. 调用 `langgraph_sdk.get_client(...).runs.wait(...)`
7. 提取最终文本和新产物
8. 通过 `MessageBus.publish_outbound(...)` 回发到 channel

### 8.4 Session 覆盖层次

这是当前 channels 设计里一个非常实用的点。

运行参数会按多层配置覆盖：

1. 默认 session
2. channel 级 session
3. channel 下某个 user 的 session
4. 运行时临时 user 设置
5. 消息 metadata 中的显式覆盖

因此同一个飞书 bot 下，可以给不同用户配置不同的默认模型、思考模式、assistant。

### 8.5 支持的命令

当前 `ChannelManager` 内建命令包括：

| 命令 | 作用 |
| --- | --- |
| `/new` | 新建会话 thread |
| `/status` | 查看当前 thread |
| `/mode flash|thinking|pro|ultra` | 切换运行模式 |
| `/models` | 查看可用模型 |
| `/model <name>` | 切换当前模型 |
| `/memory` | 查看 memory 状态摘要 |
| `/help` | 帮助 |

注意：

- 当前没有单独的 `/schedules` 命令
- `/mode` 是通过更新 runtime user session 实现的，不会改全局配置文件

### 8.6 飞书实现的当前特征

`FeishuChannel` 目前使用 `lark-oapi` 的 WebSocket 长连接模式。

这有两个实际收益：

1. 不要求服务端开放公网 webhook 地址
2. 更适合单机 Docker 部署

另外它为了兼容 uvicorn/uvloop，专门在独立线程里创建事件循环并启动 Lark WebSocket client，这不是装饰性代码，而是为了解决 SDK 内部 `run_until_complete()` 与主事件循环冲突的问题。

### 8.7 产物与图片投递

当 Agent 产出附件时，`ChannelManager` 会：

1. 从 `present_files` 工具调用里提取新产物路径
2. 只允许 `/mnt/user-data/outputs/` 下的文件对外发送
3. 把虚拟路径映射回 thread outputs 目录
4. 如果文本中出现远程图片 URL，也会尝试下载后再作为附件发送

这层路径校验是安全边界，避免把 uploads 或 workspace 中的任意文件直接泄露到外部 IM。

---

## 9. Reflection 动态加载系统

### 9.1 定位

`backend/src/reflection/` 提供了动态导入模块和变量的能力，这是 DeerFlow 实现**工具可插拔**的核心机制。

它的作用是：把 `config.yaml` 中声明的 `use: "module.path:variable"` 字符串转换为实际的 Python 对象。

### 9.2 核心函数

| 函数 | 作用 |
| --- | --- |
| `resolve_variable(path)` | 导入模块并返回指定变量（如 `src.sandbox.tools:bash_tool` → `bash_tool` 函数）|
| `resolve_class(path, base_class)` | 导入并验证类是否符合基类约束 |

### 9.3 在工具加载中的应用

```python
# config.yaml 中声明：
tools:
  - name: bash
    group: sandbox
    use: src.sandbox.tools:bash_tool

# 代码中动态解析：
tool_config = config.tools[0]
bash_tool = resolve_variable(tool_config.use)  # 返回实际的 tool 函数
```

### 9.4 错误处理

如果指定的模块不存在，`resolve_variable` 会从反射结果中提取可操作的安装提示：

```
Missing module 'langchain_google_genai'. Install it with: uv add langchain-google-genai
```

这比直接抛 `ModuleNotFoundError` 更友好。

---

## 10. Gateway API 与关键文件

### 10.1 Gateway 当前暴露的核心路由

Gateway 在 `backend/src/gateway/app.py` 中注册了这些路由：

| 路由前缀 | 作用 |
| --- | --- |
| `/api/models` | 模型列表 |
| `/api/mcp` | MCP 配置管理 |
| `/api/memory` | Memory 数据与配置 |
| `/api/skills` | Skills 状态 |
| `/api/threads/{thread_id}/artifacts` | 会话产物 |
| `/api/threads/{thread_id}/uploads` | 上传文件 |
| `/api/channels` | IM channel 状态与重启 |
| `/api/schedules` | Scheduler 管理 |
| `/health` | 健康检查 |

### 10.2 `memory` 路由

当前 `backend/src/gateway/routers/memory.py` 提供：

- `GET /api/memory`
- `POST /api/memory/reload`
- `GET /api/memory/config`
- `GET /api/memory/status`

它返回的是全局 memory 数据，而不是某个 thread 的 checkpoint。

### 10.3 `channels` 路由

当前 `backend/src/gateway/routers/channels.py` 提供：

- `GET /api/channels`
- `POST /api/channels/{name}/restart`

这也是为什么生产反向代理必须把 `/api/channels` 转发到 Gateway，而不是只转发 `/api/models`、`/api/memory`。

### 10.4 需要优先掌握的关键文件

| 模块 | 文件 |
| --- | --- |
| LangGraph 入口 | `backend/langgraph.json` |
| Lead Agent 组装 | `backend/src/agents/lead_agent/agent.py` |
| 线程状态 | `backend/src/agents/thread_state.py` |
| 工具加载 | `backend/src/tools/tools.py` |
| 定时任务工具 | `backend/src/tools/builtins/schedule_tool.py` |
| 子智能体工具 | `backend/src/tools/builtins/task_tool.py` |
| Draft 执行 | `backend/src/scheduler/draft_actions.py` |
| Scheduler 存储 | `backend/src/scheduler/store.py` |
| Scheduler 服务 | `backend/src/scheduler/service.py` |
| Checkpointer | `backend/src/agents/checkpointer/provider.py` |
| Async Checkpointer | `backend/src/agents/checkpointer/async_provider.py` |
| Memory 注入 | `backend/src/agents/middlewares/memory_context_middleware.py` |
| Memory 更新 | `backend/src/agents/middlewares/memory_middleware.py` |
| Memory 持久化 | `backend/src/agents/memory/updater.py` |
| Memory 队列 | `backend/src/agents/memory/queue.py` |
| Channel 生命周期 | `backend/src/channels/service.py` |
| Channel 调度 | `backend/src/channels/manager.py` |
| 飞书接入 | `backend/src/channels/feishu.py` |
| Gateway 入口 | `backend/src/gateway/app.py` |
| Reflection 动态加载 | `backend/src/reflection/resolvers.py` |
| Sandbox 抽象 | `backend/src/sandbox/sandbox.py` |
| Sandbox 中间件 | `backend/src/sandbox/middleware.py` |
| 沙箱工具 | `backend/src/sandbox/tools.py` |

---

## 11. 架构要点总结

如果只记住几件事，建议记住下面这些：

1. DeerFlow 后端是”LangGraph Server + Gateway + 后台服务”的组合，不是单体 FastAPI。
2. Lead Agent 的能力来自”工具 + 中间件 + 系统提示词”三者的组合，而不是只靠 prompt。
3. Scheduler 是”LLM 负责意图理解，Store/Service 负责持久化和时间驱动执行”的双层设计。
4. `schedule` 工具当前是严格 draft-first，`confirm` 还要求 follow-up user confirmation，安全性比旧实现更强。
5. Checkpointer 负责 thread 级短期状态，Memory 负责全局长期记忆，两者边界清晰。
6. Memory 注入是 transient system message，Memory 更新是 `after_agent + debounce queue + LLM extraction`。
7. IM channel 不是直接调业务函数，而是统一通过 `ChannelManager -> LangGraph runs.wait` 复用同一套 Agent 能力。
8. 飞书当前采用 WebSocket 长连接模式，适合 Docker 单机部署，不要求公网 webhook。
9. Skills 当前通过系统提示词注入给 Lead Agent 和 Subagent，而不是作为 Tool 注册；写文档时要和内置工具 / MCP 分开表述。
10. 当前长期记忆是全局 `memory.json`，并不按 `user_id` 隔离；如果对外表述为”用户级长期记忆”，需要先补齐作用域设计。
11. Reflection 系统（`resolve_variable`）是把 `config.yaml` 中的字符串路径动态转换为 Python 对象的机制，是工具可插拔的基础。
12. Community Tools（web_search、web_fetch、image_search）需要先在 `config.yaml` 中声明才能使用，不是内置工具。

---

**文档版本**：3.2
**更新日期**：2026-04-05
**依据代码**：当前仓库工作区实现
