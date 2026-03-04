import fs from "fs";
import path from "path";

import type { NextRequest } from "next/server";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ thread_id: string }> },
) {
  const threadId = (await params).thread_id;
  const threadFile = path.resolve(
    process.cwd(),
    `public/demo/threads/${threadId}/thread.json`,
  );
  if (!fs.existsSync(threadFile)) {
    return Response.json({ error: "Thread not found" }, { status: 404 });
  }
  const jsonString = fs.readFileSync(threadFile, "utf8");
  const json = JSON.parse(jsonString);
  if (Array.isArray(json.history)) {
    return Response.json(json);
  }
  return Response.json([json]);
}
