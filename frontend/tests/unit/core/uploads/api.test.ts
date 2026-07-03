import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "/backend",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  deleteUploadedFile,
  listUploadedFiles,
  uploadFiles,
} from "@/core/uploads/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
  mockedFetch.mockImplementation(async () =>
    jsonResponse(200, { success: true, files: [], skipped_files: [] }),
  );
});

describe("uploads api", () => {
  test("encodes thread id for upload and list routes", async () => {
    await uploadFiles("thread/with space", [
      new File(["demo"], "note.txt", { type: "text/plain" }),
    ]);
    await listUploadedFiles("thread/with space");

    expect(mockedFetch).toHaveBeenNthCalledWith(
      1,
      "/backend/api/threads/thread%2Fwith%20space/uploads",
      expect.objectContaining({ method: "POST" }),
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      2,
      "/backend/api/threads/thread%2Fwith%20space/uploads/list",
    );
  });

  test("encodes thread id and filename for delete route", async () => {
    await deleteUploadedFile("thread/with space", "报告 final/notes.md");

    expect(mockedFetch).toHaveBeenCalledWith(
      "/backend/api/threads/thread%2Fwith%20space/uploads/%E6%8A%A5%E5%91%8A%20final%2Fnotes.md",
      { method: "DELETE" },
    );
  });
});
