import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";
import type { AgentThread } from "../threads";

export function urlOfArtifact({
  filepath,
  threadId,
  download = false,
  isMock = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
  isMock?: boolean;
}) {
  if (isStaticWebsiteOnly()) {
    return staticDemoArtifactURL({ filepath, threadId, download });
  }
  return buildArtifactURL({
    basePath: isMock ? "/mock/api/threads" : "/api/threads",
    filepath,
    threadId,
    download,
  });
}

export function extractArtifactsFromThread(thread: AgentThread) {
  return thread.values.artifacts ?? [];
}

export function resolveArtifactURL(absolutePath: string, threadId: string) {
  if (isStaticWebsiteOnly()) {
    return staticDemoArtifactURL({ filepath: absolutePath, threadId });
  }
  return buildArtifactURL({
    basePath: "/api/threads",
    filepath: absolutePath,
    threadId,
  });
}

function staticDemoArtifactURL({
  filepath,
  threadId,
  download = false,
}: {
  filepath: string;
  threadId: string;
  download?: boolean;
}) {
  const demoPath = filepath.replace(/^\/mnt\//, "/");
  return buildArtifactURL({
    basePath: "/demo/threads",
    filepath: demoPath,
    threadId,
    download,
    includeArtifactsPath: false,
  });
}

function buildArtifactURL({
  basePath,
  filepath,
  threadId,
  download = false,
  includeArtifactsPath = true,
}: {
  basePath: string;
  filepath: string;
  threadId: string;
  download?: boolean;
  includeArtifactsPath?: boolean;
}) {
  const backendBaseURL = getBackendBaseURL();
  const artifactPath = [
    trimSlashes(basePath),
    encodeURIComponent(threadId),
    includeArtifactsPath ? "artifacts" : "",
    trimLeadingSlash(encodeArtifactPath(filepath)),
  ]
    .filter(Boolean)
    .join("/");
  const urlPath = [trimSlashes(backendBaseURL), artifactPath]
    .filter(Boolean)
    .join("/");
  const url = `${/^https?:\/\//i.test(backendBaseURL) ? "" : "/"}${urlPath}`;
  return download ? `${url}?download=true` : url;
}

function encodeArtifactPath(filepath: string) {
  return filepath
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

function trimSlashes(path: string) {
  return path.replace(/^\/+|\/+$/g, "");
}

function trimLeadingSlash(path: string) {
  return path.replace(/^\/+/, "");
}
