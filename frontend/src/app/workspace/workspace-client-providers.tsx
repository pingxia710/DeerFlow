"use client";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { ArtifactsProvider } from "@/components/workspace/artifacts";
import { SubtasksProvider } from "@/core/tasks/context";
import { ThreadRuntimeProvider } from "@/core/threads/runtime";

export function WorkspaceClientProviders({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <SubtasksProvider>
      <ThreadRuntimeProvider>
        <ArtifactsProvider>
          <PromptInputProvider>{children}</PromptInputProvider>
        </ArtifactsProvider>
      </ThreadRuntimeProvider>
    </SubtasksProvider>
  );
}
