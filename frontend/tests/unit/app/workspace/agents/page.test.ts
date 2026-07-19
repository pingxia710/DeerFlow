import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@rstest/core";

import { zhCN } from "@/core/i18n/locales/zh-CN";

test("agent new chat runtime key includes the pending display thread id", () => {
  const source = readFileSync(
    resolve(
      __dirname,
      "../../../../../src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx",
    ),
    "utf-8",
  );

  expect(source).toContain("getAgentChatRuntimeKey(");
  expect(source).toContain("`agent-new-chat:${agentName}:${threadId}`");
  expect(source).toContain("resetThreadRuntimeSlot(runtimeKey)");
});

test("AI Team gallery keeps agents and professional roles as separate tabs", () => {
  const source = readFileSync(
    resolve(
      __dirname,
      "../../../../../src/components/workspace/agents/agent-gallery.tsx",
    ),
    "utf-8",
  );

  expect(source).toContain('value="agents"');
  expect(source).toContain('value="roles"');
  expect(source).toContain("useRoles()");
  expect(source).toContain("<RoleCard");
});

test("professional roles have readable Chinese names and descriptions", () => {
  expect(zhCN.agents.roleCopy.planner).toEqual({
    name: "方案规划",
    description:
      "一次性方案规划角色；根据指挥室交接，独立形成完整方向、目标、边界、执行路线和可观察的完成标准。",
  });
  expect(zhCN.agents.roleCopy["fact-finder"]?.name).toBe("事实核查");
  expect(zhCN.agents.roleCopy.opposition?.description).toContain(
    "不代替指挥室决策",
  );
});
