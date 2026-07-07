import { existsSync, readFileSync } from "node:fs";
import { basename, join, normalize, sep } from "node:path";

import type { NextRequest } from "next/server";

export async function GET(
  request: NextRequest,
  {
    params,
  }: {
    params: Promise<{
      thread_id: string;
      artifact_path?: string[] | undefined;
    }>;
  },
) {
  const { artifact_path, thread_id: threadId } = await params;
  const artifactPath = artifact_path?.join("/") ?? "";
  if (artifactPath.startsWith("mnt/")) {
    const baseDir = join(process.cwd(), "public", "demo", "threads", threadId);
    const filePath = normalize(
      join(baseDir, artifactPath.slice("mnt/".length)),
    );
    if (
      (filePath === baseDir || filePath.startsWith(`${baseDir}${sep}`)) &&
      existsSync(filePath)
    ) {
      if (request.nextUrl.searchParams.get("download") === "true") {
        // Attach the file to the response
        const headers = new Headers();
        headers.set(
          "Content-Disposition",
          `attachment; filename="${basename(filePath)}"`,
        );
        return new Response(readFileSync(filePath), {
          status: 200,
          headers,
        });
      }
      if (filePath.endsWith(".mp4")) {
        return new Response(readFileSync(filePath), {
          status: 200,
          headers: {
            "Content-Type": "video/mp4",
          },
        });
      }
      return new Response(readFileSync(filePath), { status: 200 });
    }
  }
  return new Response("File not found", { status: 404 });
}
