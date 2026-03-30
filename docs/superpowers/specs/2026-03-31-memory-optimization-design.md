# DeerFlow 4GB内存优化设计规范

**日期**: 2026-03-31
**目标**: 在4GB内存服务器上部署DeerFlow，通过保守优化减少内存占用

## 1. 背景

DeerFlow是LangGraph-based AI super agent系统，当前预估内存占用为700MB-1.2GB。部署到4GB内存服务器需要优化以确保稳定运行。

## 2. 约束条件

### 必须保留的核心功能
- **Lead-Sub Agents协作架构** - 系统核心
- **IM消息接入** (Feishu/Telegram) - 频繁使用
- **Web界面** - 频繁使用，生产模式部署
- **Scheduler定时任务** - 核心功能，基于LLM意图理解
- **Checkpointer** - 短期状态持久化
- **Memory** - 长期用户偏好沉淀
- **Subagent并发** - 需要3个并发线程
- **Skills** - 大部分都在使用
- **MCP集成** - 需要保留

### 部署场景
- 个人自用，非多用户场景
- Web和IM两种交互方式都频繁使用

## 3. 优化策略

采用**保守优化**策略：
- 只删除确定无用的代码和重复的工具
- 不改变核心架构
- 风险最低，预计节省50-100MB内存

## 4. 具体优化项

### 4.1 Community Tools整合

**删除文件:**
- `backend/src/community/tools/firecrawl.py`
- `backend/src/community/tools/jina_ai.py`

**保留文件:**
- `backend/src/community/tools/duckduckgo.py` - 免费无限制，无需API Key
- `backend/src/community/tools/tavily.py` - 搜索质量高，用户选择保留

**依赖清理:**
- 从 `pyproject.toml` 移除 `firecrawl-py` 依赖
- 保留 `ddgs` 和 `tavily-python`

**更新导入:**
- 修改 `backend/src/community/tools/__init__.py` 移除已删除工具的导出

### 4.2 代码清理

**删除:**
- `backend/debug.py` - Debug入口，生产环境不需要

**保留:**
- `extensions_config.example.json` - 配置文档模板
- 所有测试文件 - 保证代码质量

**前端清理:**
- 已完成的代码清理（注释代码已在上次review中清理）

### 4.3 前端生产部署

**部署方式变更:**
```bash
# 生产构建
cd frontend && pnpm build

# 启动生产服务器
pnpm start
```

**环境变量要求:**
- `BETTER_AUTH_SECRET` - 生产构建必需

**内存收益:**
- 开发模式: 200-400MB (Turbopack运行时)
- 生产模式: 100-150MB
- 节省: ~100-250MB

## 5. 预估内存分配

| 组件 | 内存占用 |
|------|----------|
| LangGraph Server | 300-500MB |
| Gateway API | 100-200MB |
| Frontend (prod) | 100-150MB |
| Subagent线程池 | 100-150MB |
| Community Tools | ~25MB |
| IM Channels | 100-200MB |
| Scheduler | 50-100MB |
| **Agent系统总计** | ~700-1150MB |

**4GB服务器分配:**
- Agent系统: ~1GB
- 系统+其他进程: ~1GB
- **可用余量**: ~2GB

## 6. 不做改动项

以下功能保留，不做任何优化:
- Subagent系统（3并发线程）
- Skills系统（17个skill全部保留）
- MCP集成
- Memory系统
- Summarization中间件
- 所有11个中间件

## 7. 实施顺序

1. 删除firecrawl和jina_ai工具文件
2. 更新community tools导入
3. 更新pyproject.toml移除依赖
4. 删除debug.py
5. 前端生产构建测试
6. 运行完整测试验证

## 8. 验收标准

- 所有后端测试通过 (`make test`)
- 前端类型检查通过 (`pnpm typecheck`)
- 前端lint通过 (`pnpm lint`)
- 前端生产构建成功 (`pnpm build`)
- 内存占用低于1.2GB