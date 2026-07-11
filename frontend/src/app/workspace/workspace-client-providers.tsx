"use client";

import { useEffect } from "react";

import { PromptInputProvider } from "@/components/ai-elements/prompt-input";
import { resetThreadChatNavigationIntent } from "@/components/workspace/chats/use-thread-chat";
import { PromptInputScopeProvider } from "@/components/workspace/prompt-input-scope";
import { SubtasksProvider } from "@/core/tasks/context";
import { clearAllThreadSingletonState } from "@/core/threads/hooks";
import { ThreadRuntimeProvider } from "@/core/threads/runtime";

export function WorkspaceClientProviders({
  children,
}: {
  children: React.ReactNode;
}) {
  useEffect(
    () => () => {
      clearAllThreadSingletonState();
      resetThreadChatNavigationIntent();
    },
    [],
  );

  return (
    <SubtasksProvider>
      <ThreadRuntimeProvider>
        <PromptInputProvider>
          <PromptInputScopeProvider>{children}</PromptInputScopeProvider>
        </PromptInputProvider>
      </ThreadRuntimeProvider>
    </SubtasksProvider>
  );
}
