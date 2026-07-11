import { beforeEach, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  DEFAULT_NON_STREAMING_REQUEST_TIMEOUT_MS: 15_000,
  fetch: rs.fn(),
}));

rs.mock("@/core/artifacts/utils", () => ({
  urlOfArtifact: () => "/api/artifact.txt",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  ArtifactRequestError,
  loadArtifactContent,
} from "@/core/artifacts/loader";

const mockedFetch = rs.mocked(fetcher);

beforeEach(() => {
  mockedFetch.mockReset();
});

test("loadArtifactContent returns successful text with a bounded request", async () => {
  mockedFetch.mockResolvedValueOnce(
    new Response("artifact body", { status: 200 }),
  );

  await expect(
    loadArtifactContent({ filepath: "/artifact.txt", threadId: "thread-1" }),
  ).resolves.toEqual({ content: "artifact body", url: "/api/artifact.txt" });
  expect(mockedFetch).toHaveBeenCalledWith("/api/artifact.txt", {
    credentials: "include",
    timeoutMs: 15_000,
  });
});

test("loadArtifactContent rejects an HTTP error instead of rendering it as file content", async () => {
  mockedFetch.mockResolvedValueOnce(
    new Response(JSON.stringify({ detail: "Artifact not found" }), {
      status: 404,
      headers: { "Content-Type": "application/json" },
    }),
  );

  await expect(
    loadArtifactContent({ filepath: "/missing.txt", threadId: "thread-1" }),
  ).rejects.toMatchObject({
    name: "ArtifactRequestError",
    status: 404,
    message: "Artifact not found",
  });
  expect(new ArtifactRequestError(500, "failed")).toBeInstanceOf(Error);
});
