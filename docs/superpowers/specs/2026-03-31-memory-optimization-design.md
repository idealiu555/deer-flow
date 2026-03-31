# DeerFlow 内存优化设计方案（4GB部署）

**日期**: 2026-03-31
**目标**: 删除冗余代码、未使用组件、装饰性效果，减少内存占用约100-150MB

## 1. 背景

DeerFlow是LangGraph-based AI super agent系统，部署到4GB内存服务器需要优化。

**核心功能（不可删除）**：
- Lead-Sub Agents协作架构
- IM消息接入（Feishu/Telegram）
- Web界面
- Scheduler定时任务（基于LLM意图理解）
- Checkpointer短期状态持久化
- Memory长期用户偏好沉淀
- Skills集成
- MCP集成

## 2. 设计决策

### 2.1 Skills删除
删除3个skills：
- `surprise-me` - 随机创意展示，很少触发
- `web-design-guidelines` - UI设计审查，需明确请求
- `vercel-deploy-claimable` - Vercel部署，用户确定不需要

保留`claude-to-deerflow`作为API文档参考。

### 2.2 后端模块清理
删除`backend/src/community/infoquest/`目录：
- infoquest是备选web搜索工具，未被config.yaml引用
- tavily已在config.yaml中配置为web_search

### 2.3 前端未使用UI组件删除
删除以下9个文件+2个CSS：
- `magic-bento.tsx` + `magic-bento.css`（20KB动画组件）
- `carousel.tsx`（轮播组件）
- `spotlight-card.tsx` + `spotlight-card.css`（聚光卡片）
- `word-rotate.tsx`（文字旋转）
- `flickering-grid.tsx`（闪烁网格）
- `number-ticker.tsx`（数字跳动）
- `galaxy.jsx` + `galaxy.css`（银河效果）
- `terminal.tsx`（终端组件）

### 2.4 前端未使用ai-elements组件删除
删除12个未使用组件：
- web-preview.tsx, canvas.tsx, sources.tsx, shimmer.tsx
- edge.tsx, node.tsx, controls.tsx, image.tsx
- checkpoint.tsx, plan.tsx, open-in-chat.tsx, connection.tsx

### 2.5 前端装饰性组件简化
删除装饰性组件：
- `aurora-text.tsx`（渐变文字）
- `confetti-button.tsx`（庆祝按钮）

修改引用文件：
- `welcome.tsx` - 移除AuroraText引用，使用普通文本
- `input-box.tsx` - 移除ConfettiButton引用，使用普通Button

### 2.6 前端动画库依赖清理
从package.json删除：
- `gsap` - 动画库
- `canvas-confetti` - 庆祝效果
- `embla-carousel-react` - 轮播库
- `motion` - 动画库

## 3. 实施顺序

1. 删除3个Skills目录
2. 删除infoquest后端模块
3. 删除前端未使用UI组件（9个文件+2个CSS）
4. 删除前端未使用ai-elements组件（12个文件）
5. 修改welcome.tsx和input-box.tsx
6. 删除aurora-text.tsx和confetti-button.tsx
7. 修改package.json删除动画库依赖
8. 运行pnpm install清理依赖

## 4. 验收标准

- 后端测试通过：`cd backend && make test`
- 前端lint通过：`cd frontend && pnpm lint`
- 前端typecheck通过：`cd frontend && pnpm typecheck`
- 前端build成功：`cd frontend && pnpm build`
- 功能验证：Web界面、IM消息、Scheduler、Memory正常工作

## 5. 预估收益

- 前端包大小减少：15-20MB
- 前端运行时内存减少：50-100MB（生产模式）
- 后端代码减少：约50KB
- 总内存优化：100-150MB

## 6. 风险评估

- **低风险**：删除的组件都是未使用或装饰性的
- **兼容性**：无需兼容旧配置，直接删除
- **测试覆盖**：删除后需运行完整测试套件验证