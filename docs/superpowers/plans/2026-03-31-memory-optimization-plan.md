# DeerFlow 内存优化实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除冗余代码、未使用组件、装饰性效果，减少内存占用约100-150MB

**Architecture:** 通过删除未使用的Skills、后端模块、前端组件和动画库依赖来优化项目体积。保持核心功能完整，不做架构变更。

**Tech Stack:** Python/LangGraph/FastAPI (后端), Next.js/React/TypeScript (前端)

---

## 文件变更概览

### 删除的文件

**Skills (3个目录):**
- `skills/public/surprise-me/`
- `skills/public/web-design-guidelines/`
- `skills/public/vercel-deploy-claimable/`

**后端模块:**
- `backend/src/community/infoquest/`

**前端UI组件 (9个文件 + 2个CSS):**
- `frontend/src/components/ui/magic-bento.tsx`
- `frontend/src/components/ui/magic-bento.css`
- `frontend/src/components/ui/carousel.tsx`
- `frontend/src/components/ui/spotlight-card.tsx`
- `frontend/src/components/ui/spotlight-card.css`
- `frontend/src/components/ui/word-rotate.tsx`
- `frontend/src/components/ui/flickering-grid.tsx`
- `frontend/src/components/ui/number-ticker.tsx`
- `frontend/src/components/ui/galaxy.jsx`
- `frontend/src/components/ui/galaxy.css`
- `frontend/src/components/ui/terminal.tsx`

**前端ai-elements组件 (12个文件):**
- `frontend/src/components/ai-elements/web-preview.tsx`
- `frontend/src/components/ai-elements/canvas.tsx`
- `frontend/src/components/ai-elements/sources.tsx`
- `frontend/src/components/ai-elements/shimmer.tsx`
- `frontend/src/components/ai-elements/edge.tsx`
- `frontend/src/components/ai-elements/node.tsx`
- `frontend/src/components/ai-elements/controls.tsx`
- `frontend/src/components/ai-elements/image.tsx`
- `frontend/src/components/ai-elements/checkpoint.tsx`
- `frontend/src/components/ai-elements/plan.tsx`
- `frontend/src/components/ai-elements/open-in-chat.tsx`
- `frontend/src/components/ai-elements/connection.tsx`

**前端装饰性组件 (2个文件):**
- `frontend/src/components/ui/aurora-text.tsx`
- `frontend/src/components/ui/confetti-button.tsx`

### 修改的文件

- `frontend/src/components/workspace/welcome.tsx` - 移除AuroraText引用
- `frontend/src/components/workspace/input-box.tsx` - 移除ConfettiButton引用
- `frontend/package.json` - 移除动画库依赖

---

## Task 1: 删除未使用的Skills

**Files:**
- Delete: `skills/public/surprise-me/`
- Delete: `skills/public/web-design-guidelines/`
- Delete: `skills/public/vercel-deploy-claimable/`

- [ ] **Step 1: 删除surprise-me skill目录**

```bash
rm -rf skills/public/surprise-me
```

- [ ] **Step 2: 删除web-design-guidelines skill目录**

```bash
rm -rf skills/public/web-design-guidelines
```

- [ ] **Step 3: 删除vercel-deploy-claimable skill目录**

```bash
rm -rf skills/public/vercel-deploy-claimable
```

- [ ] **Step 4: 验证删除结果**

```bash
ls skills/public/
```

Expected output should NOT include: surprise-me, web-design-guidelines, vercel-deploy-claimable

---

## Task 2: 删除后端infoquest模块

**Files:**
- Delete: `backend/src/community/infoquest/`

- [ ] **Step 1: 删除infoquest目录**

```bash
rm -rf backend/src/community/infoquest
```

- [ ] **Step 2: 验证删除结果**

```bash
ls backend/src/community/
```

Expected output: `image_search/` `tavily/` (infoquest已删除)

---

## Task 3: 删除前端未使用UI组件

**Files:**
- Delete: 11个文件

- [ ] **Step 1: 删除magic-bento组件**

```bash
rm frontend/src/components/ui/magic-bento.tsx
rm frontend/src/components/ui/magic-bento.css
```

- [ ] **Step 2: 删除carousel组件**

```bash
rm frontend/src/components/ui/carousel.tsx
```

- [ ] **Step 3: 删除spotlight-card组件**

```bash
rm frontend/src/components/ui/spotlight-card.tsx
rm frontend/src/components/ui/spotlight-card.css
```

- [ ] **Step 4: 删除word-rotate组件**

```bash
rm frontend/src/components/ui/word-rotate.tsx
```

- [ ] **Step 5: 删除flickering-grid组件**

```bash
rm frontend/src/components/ui/flickering-grid.tsx
```

- [ ] **Step 6: 删除number-ticker组件**

```bash
rm frontend/src/components/ui/number-ticker.tsx
```

- [ ] **Step 7: 删除galaxy组件**

```bash
rm frontend/src/components/ui/galaxy.jsx
rm frontend/src/components/ui/galaxy.css
```

- [ ] **Step 8: 删除terminal组件**

```bash
rm frontend/src/components/ui/terminal.tsx
```

- [ ] **Step 9: 验证删除结果**

```bash
ls frontend/src/components/ui/ | grep -E "magic-bento|carousel|spotlight-card|word-rotate|flickering-grid|number-ticker|galaxy|terminal"
```

Expected: 无输出 (所有文件已删除)

---

## Task 4: 删除前端未使用ai-elements组件

**Files:**
- Delete: 12个文件

- [ ] **Step 1: 删除web-preview.tsx**

```bash
rm frontend/src/components/ai-elements/web-preview.tsx
```

- [ ] **Step 2: 删除canvas.tsx**

```bash
rm frontend/src/components/ai-elements/canvas.tsx
```

- [ ] **Step 3: 删除sources.tsx**

```bash
rm frontend/src/components/ai-elements/sources.tsx
```

- [ ] **Step 4: 删除shimmer.tsx**

```bash
rm frontend/src/components/ai-elements/shimmer.tsx
```

- [ ] **Step 5: 删除edge.tsx**

```bash
rm frontend/src/components/ai-elements/edge.tsx
```

- [ ] **Step 6: 删除node.tsx**

```bash
rm frontend/src/components/ai-elements/node.tsx
```

- [ ] **Step 7: 删除controls.tsx**

```bash
rm frontend/src/components/ai-elements/controls.tsx
```

- [ ] **Step 8: 删除image.tsx**

```bash
rm frontend/src/components/ai-elements/image.tsx
```

- [ ] **Step 9: 删除checkpoint.tsx**

```bash
rm frontend/src/components/ai-elements/checkpoint.tsx
```

- [ ] **Step 10: 删除plan.tsx**

```bash
rm frontend/src/components/ai-elements/plan.tsx
```

- [ ] **Step 11: 删除open-in-chat.tsx**

```bash
rm frontend/src/components/ai-elements/open-in-chat.tsx
```

- [ ] **Step 12: 删除connection.tsx**

```bash
rm frontend/src/components/ai-elements/connection.tsx
```

- [ ] **Step 13: 验证删除结果**

```bash
ls frontend/src/components/ai-elements/
```

Expected output should include only: artifact.tsx, chain-of-thought.tsx, code-block.tsx, connection.tsx, context.tsx, conversation.tsx, loader.tsx, message.tsx, model-selector.tsx, panel.tsx, prompt-input.tsx, queue.tsx, reasoning.tsx, suggestion.tsx, task.tsx, toolbar.tsx

---

## Task 5: 修改welcome.tsx移除AuroraText

**Files:**
- Modify: `frontend/src/components/workspace/welcome.tsx`

- [ ] **Step 1: 修改welcome.tsx**

将文件内容替换为：

```tsx
"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

export function Welcome({
  className,
  mode,
}: {
  className?: string;
  mode?: "ultra" | "pro" | "thinking" | "flash";
}) {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const [waved, setWaved] = useState(false);
  const isUltra = useMemo(() => mode === "ultra", [mode]);
  useEffect(() => {
    setWaved(true);
  }, []);
  return (
    <div
      className={cn(
        "mx-auto flex w-full flex-col items-center justify-center gap-2 px-8 py-4 text-center",
        className,
      )}
    >
      <div className="text-2xl font-bold">
        {searchParams.get("mode") === "skill" ? (
          `✨ ${t.welcome.createYourOwnSkill} ✨`
        ) : (
          <div className="flex items-center gap-2">
            <div className={cn("inline-block", !waved ? "animate-wave" : "")}>
              {isUltra ? "🚀" : "👋"}
            </div>
            <span>{t.welcome.greeting}</span>
          </div>
        )}
      </div>
      {searchParams.get("mode") === "skill" ? (
        <div className="text-muted-foreground text-sm">
          {t.welcome.createYourOwnSkillDescription.includes("\n") ? (
            <pre className="font-sans whitespace-pre">
              {t.welcome.createYourOwnSkillDescription}
            </pre>
          ) : (
            <p>{t.welcome.createYourOwnSkillDescription}</p>
          )}
        </div>
      ) : (
        <div className="text-muted-foreground text-sm">
          {t.welcome.description.includes("\n") ? (
            <pre className="whitespace-pre">{t.welcome.description}</pre>
          ) : (
            <p>{t.welcome.description}</p>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: 验证修改**

```bash
head -20 frontend/src/components/workspace/welcome.tsx
```

Expected: 文件不再包含 `AuroraText` 导入

---

## Task 6: 修改input-box.tsx移除ConfettiButton

**Files:**
- Modify: `frontend/src/components/workspace/input-box.tsx`

- [ ] **Step 1: 删除ConfettiButton导入**

在 `frontend/src/components/workspace/input-box.tsx` 中，删除第35行的导入：

```diff
- import { ConfettiButton } from "@/components/ui/confetti-button";
```

- [ ] **Step 2: 替换SuggestionList中的ConfettiButton为Button**

找到 `SuggestionList` 函数（约第597行），将 `ConfettiButton` 替换为普通的 `button`：

```tsx
function SuggestionList({ threadId }: { threadId: string }) {
  const { t } = useI18n();
  const { textInput } = usePromptInputController();
  const createMenuTriggerId = `suggestion-create-trigger-${threadId}`;
  const createMenuContentId = `suggestion-create-content-${threadId}`;
  const handleSuggestionClick = useCallback(
    (prompt: string | undefined) => {
      if (!prompt) return;
      textInput.setInput(prompt);
      setTimeout(() => {
        const textarea = document.querySelector<HTMLTextAreaElement>(
          "textarea[name='message']",
        );
        if (textarea) {
          const selStart = prompt.indexOf("[");
          const selEnd = prompt.indexOf("]");
          if (selStart !== -1 && selEnd !== -1) {
            textarea.setSelectionRange(selStart, selEnd + 1);
            textarea.focus();
          }
        }
      }, 500);
    },
    [textInput],
  );
  return (
    <Suggestions className="min-h-16 w-fit items-start">
      <button
        className="text-muted-foreground cursor-pointer rounded-full border px-4 text-xs font-normal"
        onClick={() => handleSuggestionClick(t.inputBox.surpriseMePrompt)}
      >
        <SparklesIcon className="size-4" /> {t.inputBox.surpriseMe}
      </button>
      {t.inputBox.suggestions.map((suggestion) => (
        <Suggestion
          key={suggestion.suggestion}
          icon={suggestion.icon}
          suggestion={suggestion.suggestion}
          onClick={() => handleSuggestionClick(suggestion.prompt)}
        />
      ))}
      <DropdownMenu>
        <DropdownMenuTrigger
          id={createMenuTriggerId}
          aria-controls={createMenuContentId}
          asChild
        >
          <Suggestion icon={PlusIcon} suggestion={t.common.create} />
        </DropdownMenuTrigger>
        <DropdownMenuContent id={createMenuContentId} align="start">
          <DropdownMenuGroup>
            {t.inputBox.suggestionsCreate.map((suggestion, index) =>
              "type" in suggestion && suggestion.type === "separator" ? (
                <DropdownMenuSeparator key={index} />
              ) : (
                !("type" in suggestion) && (
                  <DropdownMenuItem
                    key={suggestion.suggestion}
                    onClick={() => handleSuggestionClick(suggestion.prompt)}
                  >
                    {suggestion.icon && <suggestion.icon className="size-4" />}
                    {suggestion.suggestion}
                  </DropdownMenuItem>
                )
              ),
            )}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </Suggestions>
  );
}
```

- [ ] **Step 3: 验证修改**

```bash
grep -n "ConfettiButton" frontend/src/components/workspace/input-box.tsx
```

Expected: 无输出 (ConfettiButton已移除)

---

## Task 7: 删除装饰性组件文件

**Files:**
- Delete: `frontend/src/components/ui/aurora-text.tsx`
- Delete: `frontend/src/components/ui/confetti-button.tsx`

- [ ] **Step 1: 删除aurora-text.tsx**

```bash
rm frontend/src/components/ui/aurora-text.tsx
```

- [ ] **Step 2: 删除confetti-button.tsx**

```bash
rm frontend/src/components/ui/confetti-button.tsx
```

- [ ] **Step 3: 验证删除结果**

```bash
ls frontend/src/components/ui/ | grep -E "aurora-text|confetti-button"
```

Expected: 无输出 (文件已删除)

---

## Task 8: 清理package.json动画库依赖

**Files:**
- Modify: `frontend/package.json`

**注意:** `motion` 被 `flip-display.tsx` 使用，不能删除。只删除以下依赖：
- `gsap` - magic-bento使用
- `canvas-confetti` - confetti-button使用
- `embla-carousel-react` - carousel使用
- `ogl` - galaxy使用

- [ ] **Step 1: 编辑package.json删除依赖**

从 `dependencies` 中删除以下行：

```diff
- "canvas-confetti": "^1.9.4",
- "embla-carousel-react": "^8.6.0",
- "gsap": "^3.13.0",
- "ogl": "^1.0.11",
```

从 `devDependencies` 中删除：

```diff
- "@types/gsap": "^3.0.0",
```

保留 `motion` 依赖（被 flip-display.tsx 使用）。

- [ ] **Step 2: 运行pnpm install清理依赖**

```bash
cd frontend && pnpm install
```

Expected: 依赖安装成功，无错误

---

## Task 9: 验证和测试

**Files:**
- Test: 后端测试
- Test: 前端lint/typecheck/build

- [ ] **Step 1: 运行后端测试**

```bash
cd backend && make test
```

Expected: 所有测试通过

- [ ] **Step 2: 运行前端lint检查**

```bash
cd frontend && pnpm lint
```

Expected: 无错误

- [ ] **Step 3: 运行前端typecheck**

```bash
cd frontend && pnpm typecheck
```

Expected: 无类型错误

- [ ] **Step 4: 运行前端生产构建**

```bash
cd frontend && pnpm build
```

Expected: 构建成功

- [ ] **Step 5: 提交变更**

```bash
git add -A
git commit -m "refactor: memory optimization for 4GB deployment

- Remove unused skills: surprise-me, web-design-guidelines, vercel-deploy-claimable
- Remove unused backend module: infoquest
- Remove unused frontend UI components: magic-bento, carousel, spotlight-card, etc.
- Remove unused ai-elements components
- Simplify welcome.tsx and input-box.tsx by removing decorative effects
- Remove animation library dependencies: gsap, canvas-confetti, embla-carousel-react, ogl

Estimated memory reduction: 100-150MB"
```

---

## 验收清单

- [ ] 后端测试通过 (`make test`)
- [ ] 前端lint通过 (`pnpm lint`)
- [ ] 前端typecheck通过 (`pnpm typecheck`)
- [ ] 前端build成功 (`pnpm build`)
- [ ] Web界面正常工作
- [ ] IM消息接入正常
- [ ] Scheduler定时任务正常
- [ ] Memory记忆系统正常
- [ ] 保留的Skills正常触发