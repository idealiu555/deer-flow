import fs from "fs";
import path from "path";

export function POST() {
  const threadsRoot = path.resolve(process.cwd(), "public/demo/threads");
  if (!fs.existsSync(threadsRoot)) {
    return Response.json([]);
  }
  const threadsDir = fs.readdirSync(threadsRoot, {
    withFileTypes: true,
  });
  const threadData = threadsDir
    .map((threadId) => {
      if (threadId.isDirectory() && !threadId.name.startsWith(".")) {
        const threadData = fs.readFileSync(
          path.resolve(`public/demo/threads/${threadId.name}/thread.json`),
          "utf8",
        );
        return {
          thread_id: threadId.name,
          values: JSON.parse(threadData).values,
        };
      }
      return false;
    })
    .filter(Boolean);
  return Response.json(threadData);
}
