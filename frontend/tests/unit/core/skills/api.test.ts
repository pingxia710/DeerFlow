import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import { enableSkill, loadSkills, SkillRequestError } from "@/core/skills/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("skills API errors", () => {
  test("loadSkills returns the skills payload", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { skills: [{ name: "review", enabled: true }] }),
    );
    await expect(loadSkills()).resolves.toEqual([
      { name: "review", enabled: true },
    ]);
  });

  test("loadSkills throws a typed request error instead of parsing 403 as data", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(403, { detail: "Authentication required" }),
    );
    await expect(loadSkills()).rejects.toMatchObject({
      name: "SkillRequestError",
      status: 403,
      isAdminRequired: true,
      message: "Authentication required",
    });
    await expect(
      Promise.reject(new SkillRequestError(500, "failed")),
    ).rejects.toBeInstanceOf(SkillRequestError);
  });

  test("enableSkill rejects backend failures with their detail", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(500, { detail: "Could not update skill configuration" }),
    );
    await expect(enableSkill("review", false)).rejects.toMatchObject({
      name: "SkillRequestError",
      status: 500,
      message: "Could not update skill configuration",
    });
  });
});
