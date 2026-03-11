import assert from "node:assert/strict";
import test from "node:test";

import type { Message } from "@langchain/langgraph-sdk";

const { groupMessages } = await import(new URL("./utils.ts", import.meta.url).href);

function humanMessage(content: string): Message {
  return {
    type: "human",
    id: `human-${content}`,
    content,
  } as Message;
}

function assistantMessage(
  id: string,
  content: Message["content"],
  additional_kwargs?: Record<string, unknown>,
): Message {
  return {
    type: "ai",
    id,
    content,
    additional_kwargs,
  } as Message;
}

function reasoningContent(thinking: string): Message["content"] {
  return [
    {
      type: "thinking",
      thinking,
    },
  ] as unknown as Message["content"];
}

void test("filters first-turn title-generation reasoning snapshots that arrive after a visible assistant answer", () => {
  const messages: Message[] = [
    humanMessage("你好"),
    assistantMessage("answer", [{ type: "text", text: "你好！很高兴见到你！" }]),
    assistantMessage(
      "late-thinking",
      reasoningContent(
        "The user is asking me to generate a concise title for this conversation.",
      ),
    ),
  ];

  const groupedTypes = groupMessages(messages, (group) => group.type, {
    hideLateTitleGenerationReasoning: true,
  });

  assert.deepEqual(groupedTypes, ["human", "assistant"]);
});

void test("keeps reasoning-only snapshots that happen before the visible assistant answer", () => {
  const messages: Message[] = [
    humanMessage("你好"),
    assistantMessage("thinking", reasoningContent("用户在打招呼，我应该先礼貌回应。")),
    assistantMessage("answer", [{ type: "text", text: "你好！有什么我可以帮助你的吗？" }]),
  ];

  const groupedTypes = groupMessages(messages, (group) => group.type, {
    hideLateTitleGenerationReasoning: true,
  });

  assert.deepEqual(groupedTypes, ["human", "assistant:processing", "assistant"]);
});

void test("keeps a processing group for ai messages that contain both reasoning and visible text", () => {
  const messages: Message[] = [
    humanMessage("解释一下"),
    assistantMessage(
      "mixed",
      [
        { type: "thinking", thinking: "先整理思路。" },
        { type: "text", text: "这是最终回答。" },
      ] as unknown as Message["content"],
    ),
  ];

  const groupedTypes = groupMessages(messages, (group) => group.type, {
    hideLateTitleGenerationReasoning: true,
  });

  assert.deepEqual(groupedTypes, ["human", "assistant:processing", "assistant"]);
});

void test("keeps later-turn reasoning snapshots when title-generation filtering is enabled", () => {
  const messages: Message[] = [
    humanMessage("第一轮"),
    assistantMessage("answer-1", [{ type: "text", text: "第一轮回答" }]),
    humanMessage("第二轮"),
    assistantMessage("thinking-2", reasoningContent("这是第二轮正常思考过程。")),
    assistantMessage("answer-2", [{ type: "text", text: "第二轮回答" }]),
  ];

  const groupedTypes = groupMessages(messages, (group) => group.type, {
    hideLateTitleGenerationReasoning: true,
  });

  assert.deepEqual(groupedTypes, [
    "human",
    "assistant",
    "human",
    "assistant:processing",
    "assistant",
  ]);
});

void test("keeps late reasoning snapshots when title-generation filtering is disabled", () => {
  const messages: Message[] = [
    humanMessage("你好"),
    assistantMessage("answer", [{ type: "text", text: "你好！很高兴见到你！" }]),
    assistantMessage("late-thinking", reasoningContent("普通迟到 reasoning")),
  ];

  const groupedTypes = groupMessages(messages, (group) => group.type);

  assert.deepEqual(groupedTypes, ["human", "assistant", "assistant:processing"]);
});

void test("does not enable title-generation filtering only because a thread already has a title", () => {
  const messages: Message[] = [
    humanMessage("你好"),
    assistantMessage("answer", [{ type: "text", text: "你好！" }]),
    assistantMessage("late-thinking", reasoningContent("历史 reasoning")),
  ];

  const groupedTypes = groupMessages(messages, (group) => group.type, {
    hideLateTitleGenerationReasoning: false,
  });

  assert.deepEqual(groupedTypes, ["human", "assistant", "assistant:processing"]);
});
