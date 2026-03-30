# DeerFlow 后端架构入门指南

> 本指南面向想要快速入门 DeerFlow 后端项目的开发者，从程序入口逐步深入到核心特性实现，帮助你理解项目的整体架构设计。

## 目录

- [第一部分：项目概览与启动流程](#第一部分项目概览与启动流程)
- [第二部分：Lead Agent 架构](#第二部分lead-agent-架构)
- [第三部分：中间件链设计](#第三部分中间件链设计)
- [第四部分：Subagent 协作架构](#第四部分subagent-协作架构)
- [第五部分：能力扩展系统](#第五部分能力扩展系统)
- [第六部分：Scheduler 定时任务](#第六部分scheduler-定时任务)
- [第七部分：持久化与记忆系统](#第七部分持久化与记忆系统)
- [第八部分：面试常见问题与回答要点](#第八部分面试常见问题与回答要点)

---

## 第一部分：项目概览与启动流程

### 1.1 什么是 DeerFlow？

DeerFlow 是一个基于 **LangGraph** 的 AI 智能体系统，采用全栈架构：

| 服务 | 端口 | 职责 |
|------|------|------|
| **LangGraph Server** | 2024 | Agent 运行时，执行对话流程 |
| **Gateway API** | 8001 | REST API，管理模型、MCP、Skills、Memory、上传等 |
| **Frontend** | 3000 | Next.js Web 界面 |
| **Nginx** | 2026 | 统一反向代理入口 |

**核心特性**：
- 沙箱执行环境（隔离的文件系统操作）
- 持久化记忆（短期会话状态 + 长期用户偏好）
- Subagent 任务委托（并发后台执行）
- 可扩展能力（内置工具 + MCP + Skills）
- 定时任务调度（基于 LLM 意图理解）

### 1.2 启动命令

从项目根目录：

```bash
make check      # 检查系统依赖（Node 22+, pnpm, Python 3.12+, uv, nginx）
make install    # 安装所有依赖
make dev        # 启动全部服务 → http://localhost:2026
make stop       # 停止所有服务
```

从 `backend/` 目录：

```bash
make dev        # 仅启动 LangGraph Server (2024)
make gateway    # 仅启动 Gateway API (8001)
make test       # 运行所有测试
make lint       # 代码检查
```

### 1.3 程序入口详解

#### LangGraph Server 入口

**配置文件**：`backend/langgraph.json`

```json
{
  "$schema": "https://langgra.ph/schema.json",
  "dependencies": ["."],
  "env": ".env",
  "graphs": {
    "lead_agent": "src.agents:make_lead_agent"  // ← 核心：Lead Agent 工厂方法
  },
  "checkpointer": {
    "path": "./src/agents/checkpointer/async_provider.py:make_checkpointer"  // ← 持久化
  }
}
```

**启动流程**：

1. `langgraph` CLI 读取 `langgraph.json`
2. 加载 `.env` 环境变量
3. 调用 `make_lead_agent(config)` 创建 Agent 实例
4. 调用 `make_checkpointer()` 初始化持久化存储
5. 启动 HTTP Server，监听 2024 端口

#### Gateway API 入口

**入口文件**：`backend/src/gateway/app.py`

```python
@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 加载配置
    config = get_app_config()

    # 2. 启动 IM Channel Service（可选）
    start_channel_service()

    # 3. 启动 Scheduler Service
    start_scheduler_service()

    yield  # 服务运行期间

    # 4. 关闭服务
    stop_scheduler_service()
    stop_channel_service()
```

**路由模块**：`backend/src/gateway/routers/`

| Router | 路径前缀 | 功能 |
|--------|----------|------|
| `models.py` | `/api/models` | 模型配置管理 |
| `mcp.py` | `/api/mcp` | MCP Server 配置 |
| `skills.py` | `/api/skills` | Skills 状态管理 |
| `memory.py` | `/api/memory` | 用户记忆数据 |
| `uploads.py` | `/api/threads/{id}/uploads` | 文件上传 |
| `artifacts.py` | `/api/threads/{id}/artifacts` | 输出文件访问 |
| `schedules.py` | `/api/schedules` | 定时任务管理 |

---

## 第二部分：Lead Agent 架构

### 2.1 Agent 工厂方法

**核心文件**：`backend/src/agents/lead_agent/agent.py`

```python
def make_lead_agent(config: RunnableConfig):
    # 1. 解析运行时配置
    cfg = config.get("configurable", {})
    model_name = cfg.get("model_name") or _resolve_model_name()
    thinking_enabled = cfg.get("thinking_enabled", True)
    is_plan_mode = cfg.get("is_plan_mode", False)
    subagent_enabled = cfg.get("subagent_enabled", False)

    # 2. 创建 LLM 模型
    model = create_chat_model(
        name=model_name,
        thinking_enabled=thinking_enabled
    )

    # 3. 加载工具集
    tools = get_available_tools(
        model_name=model_name,
        subagent_enabled=subagent_enabled
    )

    # 4. 构建中间件链
    middlewares = _build_middlewares(config, model_name)

    # 5. 生成系统提示词
    system_prompt = apply_prompt_template(
        subagent_enabled=subagent_enabled
    )

    # 6. 返回 Agent 实例
    return create_agent(
        model=model,
        tools=tools,
        middleware=middlewares,
        system_prompt=system_prompt,
        state_schema=ThreadState  # ← 自定义状态 Schema
    )
```

**设计要点**：

1. **动态模型选择**：支持在请求中指定模型，fallback 到默认模型
2. **Thinking 模式**：支持 Claude 等模型的 extended thinking
3. **工具组合**：内置 + MCP + 配置定义的动态组合
4. **中间件可插拔**：根据配置启用不同功能

### 2.2 ThreadState 状态定义

**核心文件**：`backend/src/agents/thread_state.py`

```python
class ThreadState(AgentState):
    # 沙箱状态
    sandbox: NotRequired[SandboxState | None]

    # 线程目录路径
    thread_data: NotRequired[ThreadDataState | None]

    # 自动生成的标题
    title: NotRequired[str | None]

    # 输出文件列表（使用 reducer 合并去重）
    artifacts: Annotated[list[str], merge_artifacts]

    # 任务列表（Plan Mode）
    todos: NotRequired[list | None]

    # 已上传文件
    uploaded_files: NotRequired[list[dict] | None]

    # 查看的图像（Vision 模型）
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]
```

**自定义 Reducer**：

```python
def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """合并并去重 artifacts 列表"""
    if existing is None:
        return new or []
    if new is None:
        return existing
    # 使用 dict.fromkeys 去重并保持顺序
    return list(dict.fromkeys(existing + new))
```

**设计意图**：

- `Annotated[..., reducer]` 允许自定义状态合并逻辑
- 避免重复数据（如多次调用 `present_files` 添加同一文件）
- `viewed_images` 支持清空操作（传入空 dict）

---

## 第三部分：中间件链设计

### 3.1 什么是 Middleware？

Middleware 是 LangGraph Agent 的生命周期钩子，可以在：
- `before_agent`：Agent 执行前
- `after_agent`：Agent 执行后
- `wrap_model_call`：模型调用前后（可注入上下文）

DeerFlow 使用 Middleware 实现：
- 状态管理（线程目录、沙箱）
- 上下文注入（上传文件、记忆数据）
- 安全机制（任务截断、澄清拦截）

### 3.2 中间件执行顺序

**代码位置**：`backend/src/agents/lead_agent/agent.py` → `_build_middlewares()`

```python
middlewares = build_lead_runtime_middlewares(lazy_init=True)

# 顺序添加：
middlewares.append(summarization_middleware)  # 可选
middlewares.append(todo_list_middleware)      # 可选（Plan Mode）
middlewares.append(TitleMiddleware())
middlewares.append(MemoryContextMiddleware())  # 注入记忆
middlewares.append(MemoryMiddleware())         # 排队更新
middlewares.append(ViewImageMiddleware())      # 可选（Vision）
middlewares.append(SubagentLimitMiddleware())  # 可选（Subagent）
middlewares.append(ClarificationMiddleware())  # 必须最后
```

**完整执行链**：

| 序号 | Middleware | 执行时机 | 核心职责 |
|------|------------|----------|----------|
| 1 | **ThreadDataMiddleware** | before_agent | 创建线程目录结构 |
| 2 | **UploadsMiddleware** | before_agent | 注入已上传文件信息 |
| 3 | **SandboxMiddleware** | before_agent | 获取沙箱环境 |
| 4 | **DanglingToolCallMiddleware** | before_agent | 处理中断的工具调用 |
| 5 | **SummarizationMiddleware** | wrap_model | Token 超限时压缩上下文 |
| 6 | **TodoMiddleware** | wrap_model | Plan Mode 任务跟踪 |
| 7 | **TitleMiddleware** | after_agent | 自动生成对话标题 |
| 8 | **MemoryContextMiddleware** | wrap_model_call | 注入最新记忆 |
| 9 | **MemoryMiddleware** | after_agent | 排队记忆更新 |
| 10 | **ViewImageMiddleware** | wrap_model | 注入 base64 图像 |
| 11 | **SubagentLimitMiddleware** | after_model | 截断超额并发任务 |
| 12 | **ClarificationMiddleware** | after_model | 拦截澄清请求 |

### 3.3 关键 Middleware 实现详解

#### MemoryMiddleware（长期记忆更新）

**文件**：`backend/src/agents/middlewares/memory_middleware.py`

```python
class MemoryMiddleware(AgentMiddleware):
    def after_agent(self, state, runtime) -> dict | None:
        # 1. 检查配置是否启用
        if not get_memory_config().enabled:
            return None

        # 2. 获取 thread_id
        thread_id = runtime.context.get("thread_id")

        # 3. 过滤消息：只保留 user + final AI response
        messages = state.get("messages", [])
        filtered = _filter_messages_for_memory(messages)
        # 过滤逻辑：
        # - 移除 ToolMessage（工具调用结果）
        # - 移除 AI 带有 tool_calls 的消息（中间步骤）
        # - 移除 <uploaded_files> 块（会话级临时数据）

        # 4. 排队到 MemoryUpdateQueue（异步处理）
        queue = get_memory_queue()
        queue.add(thread_id=thread_id, messages=filtered)
```

**设计要点**：

- **异步更新**：不阻塞 Agent 执行，后台线程处理
- **消息过滤**：避免将工具调用细节写入长期记忆
- **Debounce**：队列等待 30 秒后批量处理，避免频繁调用 LLM

#### MemoryContextMiddleware（记忆注入）

**文件**：`backend/src/agents/middlewares/memory_context_middleware.py`

```python
class MemoryContextMiddleware(AgentMiddleware):
    def wrap_model_call(self, state, runtime, call_next):
        # 1. 在 model call 前注入记忆
        memory_data = get_memory_data()  # 从 memory.json 读取

        # 2. 格式化为 <memory> 标签
        memory_block = format_memory_for_injection(memory_data)
        # 包含：
        # - user context (workContext, personalContext, topOfMind)
        # - top 15 facts (偏好、知识等)

        # 3. 注入到系统提示
        # 在消息列表开头插入包含 <memory> 的系统消息

        return call_next(state)
```

**设计意图**：

- 每次对话前注入最新记忆，LLM 可基于用户偏好回答
- Token 限制：默认最多 2000 tokens，避免占用过多上下文

#### SubagentLimitMiddleware（并发限制）

**文件**：`backend/src/agents/middlewares/subagent_limit_middleware.py`

```python
class SubagentLimitMiddleware(AgentMiddleware):
    def after_model(self, state, runtime) -> dict | None:
        # 1. 检查是否启用 Subagent
        if not runtime.config.get("subagent_enabled"):
            return None

        # 2. 获取模型输出的 tool_calls
        messages = state.get("messages", [])
        last_ai = messages[-1] if messages else None
        tool_calls = getattr(last_ai, "tool_calls", [])

        # 3. 筛选 task 工具调用
        task_calls = [tc for tc in tool_calls if tc["name"] == "task"]

        # 4. 截断超出限制的调用（MAX = 3）
        max_concurrent = runtime.config.get("max_concurrent_subagents", 3)
        if len(task_calls) > max_concurrent:
            # 只保留前 max_concurrent 个
            last_ai.tool_calls = task_calls[:max_concurrent]
```

**设计目的**：

- 防止 LLM 一次发起过多并行任务
- 保护系统资源（线程池有限）
- **截断而非拒绝**：执行部分任务，避免完全失败

---

## 第四部分：Subagent 协作架构

### 4.1 什么是 Subagent？

Subagent 是主 Agent（Lead Agent）委托子任务的后台执行器，用于：
- 复杂多步任务（需要独立思考和执行）
- 并发处理多个独立任务
- 防止主对话被长时间阻塞

### 4.2 双线程池设计

**核心文件**：`backend/src/subagents/executor.py`

```python
# 调度线程池：负责任务编排
_scheduler_pool = ThreadPoolExecutor(
    max_workers=3,
    thread_name_prefix="subagent-scheduler-"
)

# 执行线程池：实际运行 Subagent
_execution_pool = ThreadPoolExecutor(
    max_workers=3,
    thread_name_prefix="subagent-exec-"
)

MAX_CONCURRENT_SUBAGENTS = 3  # 全局并发上限
```

**为什么需要两个线程池？**

1. **调度池**：快速提交任务，不阻塞主线程
2. **执行池**：支持 timeout 控制，超时可取消
3. **解耦**：调度逻辑和执行逻辑分离

### 4.3 执行流程

```python
class SubagentExecutor:
    def execute_async(self, task: str, task_id: str | None = None) -> str:
        # 1. 创建 pending 结果对象
        result = SubagentResult(
            task_id=task_id,
            status=SubagentStatus.PENDING
        )
        _background_tasks[task_id] = result

        # 2. 提交到调度池
        def run_task():
            result.status = SubagentStatus.RUNNING

            # 3. 提交到执行池（带 timeout）
            future = _execution_pool.submit(self.execute, task, result)

            try:
                # 4. 等待执行（timeout = 15 分钟）
                exec_result = future.result(timeout=self.config.timeout_seconds)
            except FuturesTimeoutError:
                # 5. 超时处理
                result.status = SubagentStatus.TIMED_OUT
                future.cancel()

        _scheduler_pool.submit(run_task)
        return task_id  # 返回 task_id 供后续查询
```

### 4.4 task 工具调用流程

**工具定义**：`backend/src/tools/builtins/task_tool.py`

```
用户: "帮我分析这三个数据文件，生成报告"

Lead Agent:
  - 解析任务，识别三个独立分析任务
  - 调用 task 工具三次：
    task(description="分析 file1.csv", subagent_type="general-purpose")
    task(description="分析 file2.csv", subagent_type="general-purpose")
    task(description="分析 file3.csv", subagent_type="general-purpose")

SubagentLimitMiddleware:
  - 截断为前 3 个（符合限制）

task_tool:
  - 创建 SubagentExecutor
  - execute_async() 启动后台任务
  - 返回 task_id 列表

主线程轮询:
  - 每 5 秒检查 _background_tasks 状态
  - 发送 SSE 事件：task_started, task_running, task_completed
  - 所有完成后返回汇总结果
```

### 4.5 内置 Subagent 类型

**配置文件**：`backend/src/subagents/builtins/`

| 类型 | 描述 | 工具集 |
|------|------|--------|
| **general-purpose** | 复杂多步任务 | 继承所有工具（排除 task） |
| **bash** | 命令执行专家 | 仅 sandbox 工具 |

```python
# SubagentConfig 示例
@dataclass
class SubagentConfig:
    name: str
    description: str
    system_prompt: str
    tools: list[str] | None = None  # None = 继承所有
    disallowed_tools: list[str] = ["task"]  # 防止嵌套
    model: str = "inherit"  # 继承父 Agent 的模型
    max_turns: int = 50
    timeout_seconds: int = 900  # 15 分钟
```

---

## 第五部分：能力扩展系统

DeerFlow 支持三类能力扩展方式，形成灵活的工具生态。

### 5.1 三类扩展对比

| 类型 | 来源 | 特点 | 适用场景 |
|------|------|------|----------|
| **内置工具** | `src/tools/builtins/` | 原生实现，性能最佳 | 核心功能（文件展示、澄清请求、定时任务） |
| **MCP** | 外部 Server | 标准协议，生态丰富 | 第三方服务（GitHub、Slack、数据库） |
| **Skills** | `skills/*/SKILL.md` | 提示词注入，无需代码 | 领域知识（PDF 处理、数据分析） |

### 5.2 内置工具

**文件位置**：`backend/src/tools/builtins/`

| 工具 | 功能 | 特殊处理 |
|------|------|----------|
| `present_files` | 展示输出文件 | 仅允许 `/mnt/user-data/outputs` |
| `ask_clarification` | 请求澄清 | 被 ClarificationMiddleware 拦截 |
| `view_image` | 读取图像 | 仅 Vision 模型可用 |
| `task` | 委托 Subagent | 仅 subagent_enabled 时可用 |
| `schedule` | 定时任务管理 | Draft-First 安全机制 |

### 5.3 MCP 集成

**架构**：使用 `langchain-mcp-adapters` 的 `MultiServerMCPClient`

**配置文件**：`extensions_config.json`

```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    },
    "remote-api": {
      "enabled": true,
      "type": "http",
      "url": "https://api.example.com/mcp",
      "oauth": {
        "token_url": "https://auth.example.com/token",
        "grant_type": "client_credentials",
        "client_id": "xxx",
        "client_secret": "$OAUTH_SECRET"
      }
    }
  }
}
```

**传输类型**：

| 类型 | 启动方式 | 适用场景 |
|------|----------|----------|
| **stdio** | 命令行启动 | 本地工具（GitHub CLI、文件系统） |
| **sse** | Server-Sent Events | 轻量实时连接 |
| **http** | HTTP 请求 | REST API、需 OAuth 的服务 |

**OAuth 支持**：

```python
# oauth.py
class OAuthTokenManager:
    async def get_authorization_header(self, server_name: str):
        # 1. 检查缓存的 token 是否即将过期
        if token and not self._is_expiring(token, oauth):
            return f"{token.token_type} {token.access_token}"

        # 2. 使用锁保护并发刷新
        async with lock:
            fresh = await self._fetch_token(oauth)
            self._tokens[server_name] = fresh
            return f"{fresh.token_type} {fresh.access_token}"
```

**缓存机制**：

```python
# cache.py
_mcp_tools_cache: list[BaseTool] | None = None
_config_mtime: float | None = None

def get_cached_mcp_tools() -> list[BaseTool]:
    # 检测配置文件 mtime 变化
    if _is_cache_stale():
        reset_mcp_tools_cache()
    # Lazy initialization
```

**跨进程同步**：

- Gateway API 修改 `extensions_config.json`
- LangGraph Server 通过 mtime 检测变更，自动重新加载

### 5.4 Skills 系统

**目录结构**：

```
skills/
├── public/        # 公共 Skills（提交到 Git）
│   ├── pdf/SKILL.md
│   ├── data-analysis/SKILL.md
│   └── frontend-design/SKILL.md
└── custom/        # 自定义 Skills（本地）
```

**SKILL.md 格式**：

```yaml
---
name: pdf
description: Use this skill whenever the user wants to do anything with PDF files...
license: Proprietary
---

# PDF Processing Guide

## Overview
This skill provides comprehensive PDF handling capabilities...

## Usage Examples
...
```

**加载流程**：

```python
# loader.py
def load_skills(skills_path: Path, enabled_only: bool = False) -> list[Skill]:
    # 1. 扫描 public 和 custom 目录
    for category in ["public", "custom"]:
        for root, dirs, files in os.walk(category_path):
            if "SKILL.md" not in files:
                continue

            # 2. 解析 YAML frontmatter
            skill = parse_skill_file(skill_file, category)
            skills.append(skill)

    # 3. 从 extensions_config.json 读取 enabled 状态
    extensions_config = ExtensionsConfig.from_file()
    for skill in skills:
        skill.enabled = extensions_config.is_skill_enabled(skill.name)

    return skills
```

**注入方式**：

Skills 不作为工具调用，而是注入到系统提示词：

```python
# prompt.py
def apply_prompt_template(subagent_enabled: bool) -> str:
    skills_section = ""
    for skill in load_skills(enabled_only=True):
        skills_section += f"- {skill.name}: {skill.description}\n"
        skills_section += f"  Path: {skill.get_container_path()}\n"

    return f"""
You have access to the following skills:
{skills_section}

When a user request matches a skill's description, read the SKILL.md file
for detailed guidance before responding.
"""
```

---

## 第六部分：Scheduler 定时任务

### 6.1 核心设计理念

**"基于 LLM 意图理解实现定时任务"** 的含义：

1. 用户用自然语言描述需求："每天早上9点提醒我复盘"
2. LLM 解析意图，提取：
   - 任务内容（prompt = "提醒复盘"）
   - 时间规律（cron = "0 9 * * *"）
3. LLM 调用 `schedule` 工具创建定时任务
4. 系统后台执行，到期时启动 Agent 完成任务

**关键文件**：

| 文件 | 职责 |
|------|------|
| `backend/src/config/scheduler_config.py` | 配置 Schema |
| `backend/src/scheduler/store.py` | SQLite 持久化 |
| `backend/src/scheduler/service.py` | 后台调度循环 |
| `backend/src/scheduler/draft_actions.py` | Draft 执行逻辑 |
| `backend/src/tools/builtins/schedule_tool.py` | LLM 调用接口 |

### 6.2 配置 Schema

```python
class SchedulerConfig(BaseModel):
    enabled: bool = True
    db_path: str = "scheduler/scheduler.db"  # SQLite 路径
    poll_interval_seconds: int = 15          # 调度轮询间隔
    max_concurrency: int = 3                 # 最大并发执行
    lease_seconds: int = 60                  # 执行租约时长
    draft_ttl_seconds: int = 86400           # Draft 过期时间
    max_runs_per_schedule: int = 50          # 每个 Schedule 最大历史记录数
    retry_attempts: int = 1                  # 失败重试次数
    default_timezone: str = "Asia/Shanghai"
```

### 6.3 SQLite 持久化

**三个数据表**：

```sql
-- schedules: 定时任务定义
CREATE TABLE schedules (
    id TEXT PRIMARY KEY,
    owner_key TEXT,          -- 所有者标识（如 channel:chat）
    channel_name TEXT,       -- IM 渠道名称
    thread_id TEXT,          -- 执行线程 ID
    assistant_id TEXT,       -- Agent ID
    title TEXT,              -- 任务标题
    prompt TEXT,             -- 执行时的提示词
    kind TEXT,               -- 'cron' 或 'once'
    cron_expr TEXT,          -- cron 表达式（cron 类型）
    run_at_utc TEXT,         -- 执行时间（once 类型）
    timezone TEXT,
    status TEXT,             -- 'active' 或 'paused'
    next_run_at TEXT,        -- 下次执行时间
    run_now_pending INTEGER, -- 立即触发标记
    config_json TEXT,        -- 运行时配置快照
    context_json TEXT,       -- 运行时上下文快照
    lease_owner TEXT,        -- 执行租约持有者
    lease_expires_at TEXT,   -- 租约过期时间
    last_error TEXT,
    created_at TEXT,
    updated_at TEXT
);

-- schedule_runs: 执行历史
CREATE TABLE schedule_runs (
    id TEXT PRIMARY KEY,
    schedule_id TEXT,
    planned_at TEXT,         -- 计划执行时间
    started_at TEXT,         -- 实际开始时间
    finished_at TEXT,        -- 完成时间
    status TEXT,             -- 'running', 'success', 'failed'
    attempt INTEGER,
    error TEXT,
    output TEXT              -- 执行结果摘要
);

-- schedule_drafts: 两阶段提交草稿
CREATE TABLE schedule_drafts (
    id TEXT PRIMARY KEY,
    owner_key TEXT,
    action TEXT,             -- 'add', 'update', 'remove', 'run'
    schedule_id TEXT,
    payload_json TEXT,       -- 操作参数
    created_at TEXT,
    expires_at TEXT          -- TTL 过期
);
```

### 6.4 后台调度循环

```python
# service.py
class SchedulerService:
    async def _loop(self):
        while self._running:
            # 1. 计算可用槽位
            available_slots = self._config.max_concurrency - len(self._inflight_tasks)

            # 2. 声明到期任务（带租约）
            claimed = self._store.claim_due_schedules(
                limit=available_slots,
                lease_owner=self._instance_id,
                lease_seconds=self._config.lease_seconds
            )

            # 3. 分发执行任务
            for schedule in claimed:
                task = asyncio.create_task(self._run_claimed(schedule))
                self._inflight_tasks.add(task)

            # 4. 等待下次轮询或被唤醒
            await self._wake_event.wait(self._config.poll_interval_seconds)
```

**执行流程**：

```python
async def _run_claimed(self, schedule):
    # 1. 创建 run 记录
    run = self._store.create_run(schedule.id, status="running")

    # 2. 启动租约心跳
    heartbeat_task = asyncio.create_task(self._lease_heartbeat(schedule.id))

    try:
        # 3. 通过 LangGraph SDK 执行 Agent
        client = get_client(self._langgraph_url)
        result = await client.runs.wait(
            thread_id=schedule.thread_id,
            assistant_id=schedule.assistant_id,
            input={"messages": [{"role": "user", "content": schedule.prompt}]},
            config=json.loads(schedule.config_json),
            context=json.loads(schedule.context_json)
        )

        # 4. 提取响应
        response = extract_response(result)
        artifacts = extract_artifacts(result)

        # 5. 完成记录
        self._store.finish_run(run.id, status="success", output=response)

    finally:
        # 6. 释放租约
        self._store.release_schedule_claim(
            schedule.id,
            self._instance_id,
            success=True
        )
        heartbeat_task.cancel()
```

### 6.5 Draft-First 安全机制

**为什么需要 Draft？**

- 防止 LLM 误触发创建/修改/删除操作
- 确保用户明确确认后才执行
- 类似 "二次确认" 但更灵活

**流程**：

```
用户: "每天9点提醒我复盘"

LLM: 解析意图 → 调用 schedule(action="add", schedule={...})

schedule_tool:
  1. 创建 Draft（status: pending）
  2. 返回 Draft ID 和确认请求

LLM: 向用户展示: "我将创建每天9点的提醒任务，请回复确认"

用户: "好的" 或 "确认"

LLM: 调用 schedule(action="confirm", draft_id="xxx")

schedule_tool:
  1. 验证存在新的用户消息（防止自循环）
  2. 执行 Draft → 创建 Schedule
  3. 唤醒 Scheduler Service
```

**关键验证逻辑**：

```python
def _is_follow_up_user_confirmation(state, draft):
    # 检查 Draft 创建后是否有新的用户消息
    origin_message_id = draft.payload.meta.origin_user_message_id
    origin_message_count = draft.payload.meta.origin_user_message_count

    latest_message_id, human_message_count = _latest_human_message_meta(state)

    # 没有 new user message
    if human_message_count <= origin_message_count:
        return False

    # 是同一条消息（模型在创建 Draft 后立即调用 confirm）
    if origin_message_id == latest_message_id:
        return False

    return True  # 有新的用户确认消息
```

### 6.6 租约机制（分布式安全）

**目的**：多实例部署时防止同一任务被重复执行

```python
# store.py
def claim_due_schedules(limit, lease_owner, lease_seconds):
    # 原子事务
    conn.execute("BEGIN IMMEDIATE")

    # 选择到期且无租约的任务
    due = conn.execute("""
        SELECT * FROM schedules
        WHERE next_run_at <= ?
        AND (lease_owner IS NULL OR lease_expires_at <= ?)
        AND status = 'active'
        LIMIT ?
    """, (now, now, limit))

    # 设置租约
    for schedule in due:
        conn.execute("""
            UPDATE schedules SET
            lease_owner = ?, lease_expires_at = ?
            WHERE id = ?
        """, (lease_owner, now + lease_seconds, schedule.id))

    conn.commit()
    return due
```

**心跳续约**：

```python
async def _lease_heartbeat(self, schedule_id):
    interval = self._config.lease_seconds // 3
    while True:
        await asyncio.sleep(interval)
        renewed = self._store.renew_schedule_lease(
            schedule_id,
            self._instance_id,
            self._config.lease_seconds
        )
        if not renewed:
            return  # 租约丢失，停止心跳
```

---

## 第七部分：持久化与记忆系统

### 7.1 Checkpointer（短期会话状态）

**作用**：保存对话的完整状态，支持：
- 多轮对话连续性
- 恢复中断的会话
- 历史回溯

**配置**：

```yaml
# config.yaml
checkpointer:
  type: sqlite          # memory | sqlite | postgres
  connection_string: "store.db"
```

**实现**：

```python
# async_provider.py
@contextlib.asynccontextmanager
async def make_checkpointer() -> AsyncIterator[Checkpointer]:
    config = _resolve_effective_checkpointer_config()

    if config.type == "memory":
        yield InMemorySaver()

    elif config.type == "sqlite":
        async with AsyncSqliteSaver.from_conn_string(conn_str) as saver:
            await saver.setup()
            yield EnhancedAsyncSqliteSaver(saver)

    elif config.type == "postgres":
        async with AsyncPostgresSaver.from_conn_string(conn_str) as saver:
            await saver.setup()
            yield saver
```

**增强功能**：

```python
class EnhancedAsyncSqliteSaver:
    async def adelete_thread(self, thread_id: str):
        """删除整个线程的 checkpoints"""

    async def acopy_thread(self, source_id: str, target_id: str):
        """复制线程状态到新线程"""

    async def aprune(self, thread_ids, strategy: str = "keep_latest"):
        """清理旧 checkpoints，只保留最新"""
```

### 7.2 Memory（长期用户偏好）

**存储结构**：`backend/.deer-flow/memory.json`

```json
{
  "user": {
    "workContext": {"summary": "用户是一名数据科学家", "updatedAt": "..."},
    "personalContext": {"summary": "..."},
    "topOfMind": {"summary": "当前关注..."}
  },
  "history": {
    "recentMonths": {"summary": "..."},
    "earlierContext": {"summary": "..."},
    "longTermBackground": {"summary": "..."}
  },
  "facts": [
    {
      "id": "fact_xxx",
      "content": "用户偏好使用 Python 进行数据分析",
      "category": "preference",
      "confidence": 0.8,
      "createdAt": "...",
      "source": "对话摘要"
    }
  ]
}
```

**Fact 类型**：

| Category | 含义 | 示例 |
|----------|------|------|
| `preference` | 偏好 | "喜欢用简洁的代码风格" |
| `knowledge` | 知识 | "熟悉 LangChain 框架" |
| `context` | 上下文 | "正在开发一个 API 项目" |
| `behavior` | 行为 | "通常在周末进行代码审查" |
| `goal` | 目标 | "希望学习 MCP 协议" |

### 7.3 Memory 更新流程

```python
# updater.py
class MemoryUpdater:
    async def update_memory(self, thread_id: str, messages: list):
        # 1. 构建更新 Prompt
        prompt = MEMORY_UPDATE_PROMPT.format(
            current_memory=self._get_current_memory(),
            conversation=format_messages(messages)
        )

        # 2. LLM 提取更新
        response = await self._model.ainvoke(prompt)

        # 3. 解析 LLM 输出
        updates = parse_memory_updates(response)

        # 4. 应用更新
        self._apply_updates(updates)

        # 5. 原子写入文件
        self._save_memory_to_file()

    def _apply_updates(self, updates):
        # 合并 context 更新
        if updates.get("workContext"):
            self._data["user"]["workContext"] = updates["workContext"]

        # 合并 facts（去重、置信度过滤）
        for fact in updates.get("facts", []):
            if fact["confidence"] >= self._config.fact_confidence_threshold:
                self._add_or_update_fact(fact)
```

### 7.4 Memory 注入机制

```python
# memory_context_middleware.py
def format_memory_for_injection(memory_data: dict, max_tokens: int = 2000):
    sections = []

    # 1. User Context
    user = memory_data.get("user", {})
    if user.get("workContext"):
        sections.append(f"工作背景: {user['workContext']['summary']}")
    if user.get("personalContext"):
        sections.append(f"个人背景: {user['personalContext']['summary']}")

    # 2. Top Facts（限制数量和 Token）
    facts = memory_data.get("facts", [])
    for fact in facts[:15]:  # 最多 15 条
        sections.append(f"- [{fact['category']}] {fact['content']}")

    # 3. Token 截断
    result = "\n".join(sections)
    if estimate_tokens(result) > max_tokens:
        result = truncate_to_tokens(result, max_tokens)

    return f"<memory>\n{result}\n</memory>"
```

---

## 第八部分：面试常见问题与回答要点

### Q1: Lead-Sub Agent 架构是如何设计的？

**回答要点**：

1. **Lead Agent** 作为主控：
   - 接收用户请求，理解意图
   - 决策哪些任务需要委托给 Subagent
   - 调用 `task` 工具发起委托

2. **Subagent 执行器**：
   - 双线程池设计（调度池 + 执行池）
   - 支持超时控制（默认 15 分钟）
   - 最大并发限制（3 个），由 Middleware 截断

3. **协作流程**：
   - Lead Agent 调用 task 工具
   - 后台启动独立 Agent 实例
   - 主线程轮询状态，发送 SSE 事件
   - 完成后返回结果，Lead Agent 汇总

### Q2: 中间件链的设计目的是什么？

**回答要点**：

1. **模块化**：每个功能独立封装，可插拔
2. **有序执行**：通过严格的顺序保证依赖关系
   - ThreadDataMiddleware 先创建目录 → SandboxMiddleware 才能使用
   - MemoryMiddleware 收集对话 → MemoryContextMiddleware 下次注入
3. **生命周期钩子**：
   - `before_agent`: 初始化状态
   - `wrap_model_call`: 注入上下文
   - `after_agent`: 后处理
4. **安全机制**：
   - SubagentLimitMiddleware 截断超额任务
   - ClarificationMiddleware 拦截澄清请求

### Q3: 定时任务的 "基于 LLM 意图理解" 如何实现？

**回答要点**：

1. **自然语言解析**：
   - 用户说 "每天早上9点提醒我复盘"
   - LLM 提取 cron 表达式 "0 9 * * *" 和任务内容

2. **Draft-First 机制**：
   - LLM 先创建 Draft（草稿）
   - 向用户确认意图
   - 收到确认后才执行

3. **后台调度**：
   - SchedulerService 定期轮询到期任务
   - 通过 LangGraph SDK 启动 Agent
   - 租约机制防止重复执行

### Q4: 短期和长期记忆的区别是什么？

**回答要点**：

| 维度 | Checkpointer（短期） | Memory（长期） |
|------|----------------------|----------------|
| **存储内容** | 完整对话状态 | 用户偏好、背景知识 |
| **生命周期** | 会话级，可恢复/删除 | 跨会话持久化 |
| **更新时机** | 每次对话自动保存 | 后台异步 LLM 提取 |
| **注入方式** | LangGraph 自动恢复 | Middleware 注入 `<memory>` 标签 |

### Q5: 三类能力扩展的区别？

**回答要点**：

| 类型 | 实现 | 特点 |
|------|------|------|
| **内置工具** | Python 函数 | 原生，性能最佳 |
| **MCP** | 外部 Server + 协议 | 标准化，生态丰富，支持 OAuth |
| **Skills** | Markdown 提示词 | 无需代码，注入系统提示 |

### Q6: 如何保证分布式部署时定时任务不重复执行？

**回答要点**：

1. **租约机制**：
   - 声明任务时写入 `lease_owner` + `lease_expires_at`
   - 其他实例看到有租约则不声明

2. **心跳续约**：
   - 执行期间定期延长租约
   - 租约丢失则停止执行

3. **原子事务**：
   - 使用 `BEGIN IMMEDIATE` 保证声明和租约写入的原子性

---

## 附录：关键文件路径索引

### Agent 系统

```
backend/src/agents/
├── lead_agent/
│   ├── agent.py              # Lead Agent 工厂方法
│   └── prompt.py             # 系统提示词模板
├── middlewares/
│   ├── memory_middleware.py        # 长期记忆更新
│   ├── memory_context_middleware.py # 记忆注入
│   ├── subagent_limit_middleware.py # 并发限制
│   ├── clarification_middleware.py  # 澄清拦截
│   └── ...
├── memory/
│   ├── updater.py            # LLM 记忆提取
│   ├── queue.py              # Debounce 队列
│   └── prompt.py             # 记忆更新 Prompt
├── checkpointer/
│   ├── provider.py           # 同步工厂
│   └── async_provider.py     # 异步工厂
└── thread_state.py           # ThreadState Schema
```

### Subagent 系统

```
backend/src/subagents/
├── executor.py               # 执行引擎（双线程池）
├── registry.py               # Agent 注册表
├── config.py                 # SubagentConfig 定义
└── builtins/
    ├── general-purpose.py    # 通用 Agent
    └── bash.py               # 命令专家 Agent
```

### Scheduler 系统

```
backend/src/scheduler/
├── store.py                  # SQLite 持久化
├── service.py                # 后台调度循环
├── draft_actions.py          # Draft 执行逻辑
└── __init__.py               # 导出

backend/src/tools/builtins/
└── schedule_tool.py          # LLM 调用接口

backend/src/config/
└── scheduler_config.py       # 配置 Schema
```

### 扩展系统

```
backend/src/mcp/
├── client.py                 # Server 配置构建
├── tools.py                  # 工具加载
├── oauth.py                  # OAuth 管理
└── cache.py                  # 缓存机制

backend/src/skills/
├── loader.py                 # Skills 发现加载
├── parser.py                 # SKILL.md 解析
└── types.py                  # Skill 数据类

skills/
├── public/                   # 公共 Skills
└── custom/                   # 自定义 Skills
```

### Gateway API

```
backend/src/gateway/
├── app.py                    # FastAPI 应用
└── routers/
    ├── models.py             # 模型管理
    ├── mcp.py                # MCP 配置
    ├── skills.py             # Skills 管理
    ├── memory.py             # 记忆数据
    ├── uploads.py            # 文件上传
    ├── artifacts.py          # 输出文件
    └── schedules.py          # 定时任务
```

### 配置文件

```
config.yaml                   # 主配置（模型、工具、沙箱等）
extensions_config.json        # MCP + Skills 配置
backend/langgraph.json        # LangGraph Server 配置
backend/.deer-flow/
├── memory.json               # 用户记忆数据
├── scheduler/
│   └── scheduler.db          # 定时任务 SQLite
└── threads/{thread_id}/      # 线程目录
    └── user-data/
        ├── workspace/        # 工作空间
        ├── uploads/          # 上传文件
        └── outputs/          # 输出文件
```

---

## 学习路径建议

1. **第一步**：理解启动流程
   - 阅读 `langgraph.json` 和 `gateway/app.py`
   - 运行 `make dev` 观察服务启动

2. **第二步**：理解 Agent 创建
   - 阅读 `lead_agent/agent.py`
   - 理解 `make_lead_agent()` 的组装逻辑

3. **第三步**：理解状态流转
   - 阅读 `thread_state.py`
   - 追踪一次对话的状态变化

4. **第四步**：深入中间件
   - 选择感兴趣的 Middleware 阅读
   - 理解其执行时机和职责

5. **第五步**：研究 Scheduler
   - 从 `schedule_tool.py` 入口开始
   - 追踪到 `service.py` 的后台循环

6. **第六步**：理解持久化
   - 阅读 `checkpointer/async_provider.py`
   - 阅读 `memory/updater.py`

7. **实践**：
   - 添加一个新的 Middleware
   - 实现一个新的内置工具
   - 创建一个新的 Skill

---

> **文档版本**: 1.0
> **适用项目**: DeerFlow Backend
> **最后更新**: 2026-03-30