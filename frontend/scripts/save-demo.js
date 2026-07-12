import { config } from "dotenv";
import fs from "fs";
import path from "path";
import { env } from "process";

import { staticDemoIndexEntry } from "./static-demo-index.js";

export async function main() {
  const url = new URL(process.argv[2]);
  const threadId = url.pathname.split("/").pop();
  const host = url.host;
  const apiURL = new URL(
    `/api/langgraph/threads/${threadId}/history`,
    `${url.protocol}//${host}`,
  );
  const response = await fetch(apiURL, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      limit: 10,
    }),
  });

  const data = (await response.json())[0];
  if (!data) {
    console.error("No data found");
    return;
  }

  const title = data.values.title;

  const rootPath = path.resolve(process.cwd(), "public/demo/threads", threadId);
  if (fs.existsSync(rootPath)) {
    fs.rmSync(rootPath, { recursive: true });
  }
  fs.mkdirSync(rootPath, { recursive: true });
  fs.writeFileSync(
    path.resolve(rootPath, "thread.json"),
    JSON.stringify(data, null, 2),
  );
  const backendRootPath = path.resolve(
    process.cwd(),
    "../backend/.deer-flow/threads",
    threadId,
  );
  copyFolder("user-data/outputs", rootPath, backendRootPath);
  copyFolder("user-data/uploads", rootPath, backendRootPath);
  writeThreadIndex(path.resolve(process.cwd(), "public/demo/threads"));
  console.info(`Saved demo "${title}" to ${rootPath}`);
}

function writeThreadIndex(threadsRootPath) {
  const summaries = fs
    .readdirSync(threadsRootPath, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => {
      const threadPath = path.resolve(
        threadsRootPath,
        entry.name,
        "thread.json",
      );
      if (!fs.existsSync(threadPath)) {
        return null;
      }
      const thread = JSON.parse(fs.readFileSync(threadPath, "utf8"));
      return staticDemoIndexEntry(entry.name, thread);
    })
    .filter(Boolean);
  fs.writeFileSync(
    path.resolve(threadsRootPath, "index.json"),
    JSON.stringify(summaries, null, 2),
  );
}

function copyFolder(relPath, rootPath, backendRootPath) {
  const outputsPath = path.resolve(backendRootPath, relPath);
  if (fs.existsSync(outputsPath)) {
    fs.cpSync(outputsPath, path.resolve(rootPath, relPath), {
      recursive: true,
    });
  }
}

config();
main();
