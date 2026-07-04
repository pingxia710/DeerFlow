import { beforeEach, describe, expect, it, rs } from "@rstest/core";

import { getBackendBaseURL } from "@/core/config";
import { isStaticWebsiteOnly } from "@/core/static-mode";

rs.mock("@/core/config", () => ({
  getBackendBaseURL: rs.fn(() => "http://backend.test"),
}));

rs.mock("@/core/static-mode", () => ({
  isStaticWebsiteOnly: rs.fn(() => false),
}));

const { resolveArtifactURL, urlOfArtifact } =
  await import("@/core/artifacts/utils");
const mockedGetBackendBaseURL = rs.mocked(getBackendBaseURL);
const mockedIsStaticWebsiteOnly = rs.mocked(isStaticWebsiteOnly);

describe("artifact URL utilities", () => {
  beforeEach(() => {
    mockedGetBackendBaseURL.mockReturnValue("http://backend.test");
    mockedIsStaticWebsiteOnly.mockReturnValue(false);
  });

  it("builds a normal artifact URL", () => {
    expect(
      urlOfArtifact({ threadId: "thread-1", filepath: "/reports/result.md" }),
    ).toBe(
      "http://backend.test/api/threads/thread-1/artifacts/reports/result.md",
    );
  });

  it("returns a relative artifact URL when backend base URL is empty", () => {
    mockedGetBackendBaseURL.mockReturnValue("");

    expect(urlOfArtifact({ threadId: "t", filepath: "/a.txt" })).toBe(
      "/api/threads/t/artifacts/a.txt",
    );
  });

  it("returns a prefixed relative artifact URL when backend base URL is relative", () => {
    mockedGetBackendBaseURL.mockReturnValue("/gateway");

    expect(urlOfArtifact({ threadId: "t", filepath: "/a.txt" })).toBe(
      "/gateway/api/threads/t/artifacts/a.txt",
    );

    mockedGetBackendBaseURL.mockReturnValue("gateway");

    expect(urlOfArtifact({ threadId: "t", filepath: "/a.txt" })).toBe(
      "/gateway/api/threads/t/artifacts/a.txt",
    );
  });

  it("returns an absolute artifact URL when backend base URL is absolute", () => {
    mockedGetBackendBaseURL.mockReturnValue("https://example.com/backend");

    expect(urlOfArtifact({ threadId: "t", filepath: "/a.txt" })).toBe(
      "https://example.com/backend/api/threads/t/artifacts/a.txt",
    );
  });

  it("encodes threadId", () => {
    expect(
      urlOfArtifact({ threadId: "thread 1/中文?x#y%", filepath: "/a.txt" }),
    ).toBe(
      "http://backend.test/api/threads/thread%201%2F%E4%B8%AD%E6%96%87%3Fx%23y%25/artifacts/a.txt",
    );
  });

  it("encodes artifact filepath by segment while preserving path hierarchy", () => {
    expect(
      urlOfArtifact({
        threadId: "thread-1",
        filepath: "/dir with space/问?题#1/100%.md",
      }),
    ).toBe(
      "http://backend.test/api/threads/thread-1/artifacts/dir%20with%20space/%E9%97%AE%3F%E9%A2%98%231/100%25.md",
    );
  });

  it("preserves .skill hierarchy", () => {
    expect(resolveArtifactURL("/.skill/SKILL.md", "thread-1")).toBe(
      "http://backend.test/api/threads/thread-1/artifacts/.skill/SKILL.md",
    );

    expect(resolveArtifactURL("/.skill/assets/icon 1.svg", "thread-1")).toBe(
      "http://backend.test/api/threads/thread-1/artifacts/.skill/assets/icon%201.svg",
    );
  });

  it("keeps download query separate from filepath query-like characters", () => {
    mockedGetBackendBaseURL.mockReturnValue("/gateway");

    expect(
      urlOfArtifact({
        threadId: "thread /?&",
        filepath: "/dir name/file?name#section%.md",
        download: true,
      }),
    ).toBe(
      "/gateway/api/threads/thread%20%2F%3F%26/artifacts/dir%20name/file%3Fname%23section%25.md?download=true",
    );
  });

  it("maps static demo artifact paths to bundled public files", () => {
    mockedGetBackendBaseURL.mockReturnValue("");
    mockedIsStaticWebsiteOnly.mockReturnValue(true);

    expect(
      urlOfArtifact({
        filepath: "/mnt/user-data/outputs/index.html",
        threadId: "thread 1",
      }),
    ).toBe("/demo/threads/thread%201/user-data/outputs/index.html");

    expect(
      resolveArtifactURL("/mnt/user-data/outputs/style.css", "thread-1"),
    ).toBe("/demo/threads/thread-1/user-data/outputs/style.css");
  });
});
