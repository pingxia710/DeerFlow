import { expect, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createElement, type ComponentType } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { CommandRoomTrajectory } from "@/components/workspace/messages/command-room-trajectory";
import { I18nProvider } from "@/core/i18n/context";
import {
  buildCommandRoomTrajectory,
  groupCommandRoomTrajectoryByWorkPackage,
  splitCommandRoomTrajectory,
} from "@/core/threads/command-room-read-model";
import { queryKeys } from "@/core/threads/query-keys";

const I18nProviderForCreateElement = I18nProvider as ComponentType<
  Omit<Parameters<typeof I18nProvider>[0], "children">
>;

function planTask(workPackageId: string, containerArtifactWritten?: boolean) {
  return {
    id: `${workPackageId}-plan`,
    commandRoomContainer: "planning" as const,
    containerArtifactKind: "spec" as const,
    description: "Recorded plan",
    prompt: "",
    runId: `${workPackageId}-run`,
    startedAt: 1,
    status: "completed" as const,
    subagent_type: "recorder",
    threadId: "thread-1",
    workPackageId,
    ...(containerArtifactWritten === undefined
      ? {}
      : { containerArtifactWritten }),
  };
}

function renderPlans(
  tasks: ReturnType<typeof planTask>[],
  artifactByTaskId: Record<string, string> = {},
) {
  const queryClient = new QueryClient();
  for (const task of tasks) {
    const artifact = artifactByTaskId[task.id];
    if (artifact !== undefined) {
      queryClient.setQueryData(
        queryKeys.thread.commandRoomPlanArtifact(
          task.threadId,
          task.runId,
          task.id,
        ),
        artifact,
      );
    }
  }
  return renderToStaticMarkup(
    createElement(
      QueryClientProvider,
      { client: queryClient },
      createElement(
        I18nProviderForCreateElement,
        { initialLocale: "en-US" },
        createElement(CommandRoomTrajectory, {
          chairMessages: [],
          steps: buildCommandRoomTrajectory(tasks),
          unstagedTasks: [],
        }),
      ),
    ),
  );
}

test("renders a readable plan without adding an approval control", () => {
  const task = planTask("package-a", true);
  const markup = renderPlans([task], { [task.id]: "# Recorded plan" });

  expect(markup).toContain("Recorded plan");
  expect(markup).not.toContain("Confirm execution");
});

test("shows a factual unavailable reason without approval language", () => {
  const task = planTask("package-a", false);
  const markup = renderPlans([task], { [task.id]: "" });

  expect(markup).toContain("The plan has not been recorded.");
  expect(markup).not.toContain("Confirm execution");
});

test("keeps plan projections separated by work package", () => {
  const packages = groupCommandRoomTrajectoryByWorkPackage(
    buildCommandRoomTrajectory([
      planTask("package-a", true),
      planTask("package-b", false),
    ]),
  );

  expect(packages.map((workPackage) => workPackage.workPackageId)).toEqual([
    "package-a",
    "package-b",
  ]);
  expect(
    packages.map(
      (workPackage) =>
        splitCommandRoomTrajectory(workPackage.steps).planProposals[0]?.tasks[0]
          ?.id,
    ),
  ).toEqual(["package-a-plan", "package-b-plan"]);
});
