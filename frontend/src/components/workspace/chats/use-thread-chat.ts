"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { uuid } from "@/core/utils/uuid";

export function useThreadChat() {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id: string }>();

  const [threadId, setThreadId] = useState(() => {
    return threadIdFromPath === "new" ? "new" : threadIdFromPath;
  });

  const [isNewThread, setIsNewThread] = useState(
    () => threadIdFromPath === "new",
  );

  useEffect(() => {
    if (threadIdFromPath === "new") {
      setIsNewThread(true);
      setThreadId(uuid());
      return;
    }

    setIsNewThread(false);
    setThreadId(threadIdFromPath);
  }, [threadIdFromPath]);

  return { threadId, isNewThread, setIsNewThread };
}
