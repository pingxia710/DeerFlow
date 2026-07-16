"use client";

import { BotIcon, PlusSquare } from "lucide-react";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { AgentWelcome } from "@/components/workspace/agent-welcome";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import {
  beginThreadNavigation,
  ChatBox,
  getThreadNavigationGeneration,
  isThreadFinishForVisibleChat,
  markThreadChatNavigationIntent,
  shouldCommitThreadStart,
  shouldShowWelcomeMode,
  useThreadChat,
  WorkRecordActivityTrigger,
  WorkRecordTrigger,
} from "@/components/workspace/chats";
import { CommandRoomCapabilities } from "@/components/workspace/command-room-capabilities";
import { ExportTrigger } from "@/components/workspace/export-trigger";
import { InputBox } from "@/components/workspace/input-box";
import {
  MessageList,
  MESSAGE_LIST_DEFAULT_PADDING_BOTTOM,
} from "@/components/workspace/messages";
import { ThreadContext } from "@/components/workspace/messages/context";
import { ThreadTitle } from "@/components/workspace/thread-title";
import { TodoList } from "@/components/workspace/todo-list";
import { TokenUsageIndicator } from "@/components/workspace/token-usage-indicator";
import { Tooltip } from "@/components/workspace/tooltip";
import { useAgent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";
import { useNotification } from "@/core/notification/hooks";
import {
  migrateThreadModelName,
  useLocalSettings,
  useThreadSettings,
} from "@/core/settings";
import type { AgentThreadState } from "@/core/threads";
import {
  type ThreadStreamFinishMeta,
  useThreadContextUsage,
  useThreadMetadata,
  useThreadTokenUsage,
} from "@/core/threads/hooks";
import {
  resetThreadRuntimeSlot,
  useThreadRuntime,
} from "@/core/threads/runtime";
import { threadTokenUsageToTokenUsage } from "@/core/threads/token-usage";
import { pathOfThread, textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { cn } from "@/lib/utils";

const COMMAND_ROOM_AGENT = "command-room";
const COMMAND_ROOM_DEFAULT_MODEL = "gpt-5.6";

export function getAgentChatRuntimeKey(
  agentName: string,
  threadId: string,
  isNewThread: boolean,
) {
  return isNewThread
    ? `agent-new-chat:${agentName}:${threadId}`
    : `agent-chat:${agentName}:${threadId}`;
}

export default function AgentChatPage() {
  const { t } = useI18n();
  const router = useRouter();

  const { agent_name } = useParams<{
    agent_name: string;
  }>();

  const { agent } = useAgent(agent_name);
  const isCommandRoom = agent_name === COMMAND_ROOM_AGENT;

  const {
    threadId,
    setThreadId,
    isNewThread,
    setIsNewThread,
    resetToNewThread,
    isMock,
  } = useThreadChat();
  // `isNewThread` gates history/token-usage fetches until the backend creates
  // the thread. `isWelcomeMode` controls only the centered welcome layout, so
  // it can flip immediately on submit without triggering eager history loads.
  const [isWelcomeMode, setIsWelcomeMode] = useState(isNewThread);
  const runtimeScope = `agent:${agent_name}` as const;
  const [settings, setSettings] = useThreadSettings(
    runtimeScope,
    threadId,
    agent?.model ?? (isCommandRoom ? COMMAND_ROOM_DEFAULT_MODEL : undefined),
  );
  const [localSettings, setLocalSettings] = useLocalSettings();
  const { tokenUsageEnabled } = useModels();
  const threadTokenUsage = useThreadTokenUsage(
    isNewThread || isMock ? undefined : threadId,
    { enabled: tokenUsageEnabled && !isMock },
  );
  const threadContextUsage = useThreadContextUsage(
    isNewThread || isMock ? undefined : threadId,
    { enabled: !isMock },
  );
  const threadMetadata = useThreadMetadata(threadId, {
    enabled: !isNewThread && !isMock,
    isMock,
  });
  const backendTokenUsage = threadTokenUsageToTokenUsage(threadTokenUsage.data);
  const visibleThreadIdRef = useRef(threadId);
  const pendingStartThreadIdRef = useRef<string | null>(null);
  const runtimeNavigationRef = useRef<{
    runtimeKey: string;
    generation: number;
  } | null>(null);

  visibleThreadIdRef.current = threadId;

  const { showNotification } = useNotification();

  const threadContext = useMemo(
    () => ({
      ...settings.context,
      agent_name: agent_name,
      ...(isCommandRoom
        ? {
            mode: "ultra" as const,
            model_name:
              settings.context.model_name ?? COMMAND_ROOM_DEFAULT_MODEL,
            reasoning_effort: settings.context.reasoning_effort,
          }
        : {}),
    }),
    [agent_name, isCommandRoom, settings.context],
  );

  const runtimeKey = getAgentChatRuntimeKey(agent_name, threadId, isNewThread);
  if (runtimeNavigationRef.current?.runtimeKey !== runtimeKey) {
    runtimeNavigationRef.current = {
      runtimeKey,
      generation: getThreadNavigationGeneration(),
    };
  }
  const navigationGeneration = runtimeNavigationRef.current.generation;

  useEffect(() => {
    pendingStartThreadIdRef.current = null;
  }, [runtimeKey]);

  const runtimeRegistration = useMemo(() => {
    let navigationGenerationAtSend = navigationGeneration;
    return {
      runtimeScope,
      runtimeKey,
      threadId: isNewThread ? undefined : threadId,
      displayThreadId: threadId,
      context: threadContext,
      isMock,
      onSend: (sentThreadId: string) => {
        navigationGenerationAtSend = getThreadNavigationGeneration();
        pendingStartThreadIdRef.current = sentThreadId;
        setIsWelcomeMode(false);
      },
      onStart: (createdThreadId: string) => {
        const pendingThreadId = pendingStartThreadIdRef.current;
        const visibleThreadId = visibleThreadIdRef.current;
        const currentPathname = window.location.pathname;
        const streamStillOwnsVisibleChat = shouldCommitThreadStart({
          createdThreadId,
          pendingThreadId,
          visibleThreadId,
          committedPathname: currentPathname,
          navigationGenerationAtSend,
          currentNavigationGeneration: getThreadNavigationGeneration(),
        });
        if (!streamStillOwnsVisibleChat) {
          return;
        }
        migrateThreadModelName(runtimeScope, visibleThreadId, createdThreadId);
        visibleThreadIdRef.current = createdThreadId;
        pendingStartThreadIdRef.current = null;
        // ! Important: Never use next.js router for navigation in this case, otherwise it will cause the thread to re-mount and lose all states. Use native history API instead.
        history.replaceState(
          null,
          "",
          `/workspace/agents/${agent_name}/chats/${createdThreadId}`,
        );
        setThreadId(createdThreadId);
        setIsNewThread(false);
      },
      onFinish: (state: AgentThreadState, meta: ThreadStreamFinishMeta) => {
        // Finish side effects are scoped to the visible chat; background thread
        // completion should not rewrite the current chat UI or notification text.
        if (
          !isThreadFinishForVisibleChat({
            finishThreadId: meta.threadId,
            visibleThreadId: visibleThreadIdRef.current,
            committedPathname: window.location.pathname,
          })
        ) {
          return;
        }
        if (document.hidden || !document.hasFocus()) {
          let body = "Conversation finished";
          const lastMessage = state.messages[state.messages.length - 1];
          if (lastMessage) {
            const textContent = textOfMessage(lastMessage);
            if (textContent) {
              body =
                textContent.length > 200
                  ? textContent.substring(0, 200) + "..."
                  : textContent;
            }
          }
          showNotification(state.title, { body });
        }
      },
    };
  }, [
    agent_name,
    isMock,
    isNewThread,
    navigationGeneration,
    runtimeScope,
    setIsNewThread,
    runtimeKey,
    setThreadId,
    showNotification,
    threadContext,
    threadId,
  ]);

  const {
    thread,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    isUploading,
    isHistoryLoading,
    historyRuns,
    terminalNotice,
    recoveryStatus,
    retryRecovery,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadRuntime(runtimeRegistration);

  useEffect(() => {
    setIsWelcomeMode(
      shouldShowWelcomeMode({
        committedPathname: window.location.pathname,
        hasMessages: thread.messages.length > 0,
        hasPendingUsageMessages: pendingUsageMessages.length > 0,
        isHistoryLoading,
        isNewThread,
        isStreamingOrLoading: thread.isLoading || isUploading,
        pendingStartThreadId: pendingStartThreadIdRef.current,
      }),
    );
  }, [
    isHistoryLoading,
    isNewThread,
    isUploading,
    pendingUsageMessages.length,
    thread.isLoading,
    thread.messages.length,
  ]);

  useEffect(() => {
    const metadataAgentName = threadMetadata.data?.metadata?.agent_name;
    if (
      isNewThread ||
      isMock ||
      typeof metadataAgentName !== "string" ||
      metadataAgentName.length === 0 ||
      metadataAgentName === agent_name
    ) {
      return;
    }

    const nextPath = pathOfThread(threadId, {
      agent_name: metadataAgentName,
    });
    beginThreadNavigation(nextPath);
    router.replace(nextPath);
  }, [agent_name, isMock, isNewThread, router, threadId, threadMetadata.data]);

  const handleSubmit = useCallback(
    (message: PromptInputMessage) => {
      return sendMessage(threadId, message);
    },
    [sendMessage, threadId],
  );

  const handleStop = useCallback(async () => {
    await thread.stop();
  }, [thread]);
  const handleRegenerate = useCallback(
    (messageId: string, supersededMessageIds: string[]) =>
      regenerateMessage(threadId, messageId, supersededMessageIds),
    [regenerateMessage, threadId],
  );
  const tokenUsageInlineMode = tokenUsageEnabled
    ? localSettings.tokenUsage.inlineMode
    : "off";
  const hasTodos = (thread.values.todos?.length ?? 0) > 0;
  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <ChatBox isNewThread={isNewThread} threadId={threadId}>
        <div className="relative flex size-full min-h-0 justify-between">
          <header
            className={cn(
              "absolute top-0 right-0 left-0 z-30 flex h-12 shrink-0 items-center gap-2 px-2 sm:px-4",
              isWelcomeMode
                ? "bg-background/0 backdrop-blur-none"
                : "bg-background/80 shadow-xs backdrop-blur",
            )}
          >
            <SidebarTrigger className="md:hidden" />
            {/* Agent badge */}
            <div className="hidden min-w-0 shrink-0 items-center gap-1.5 rounded-md border px-2 py-1 sm:flex">
              <BotIcon className="text-primary h-3.5 w-3.5" />
              <span className="hidden max-w-24 truncate text-xs font-medium sm:inline sm:max-w-none">
                {agent?.name ?? agent_name}
              </span>
            </div>

            <div className="flex min-w-0 flex-1 items-center text-sm font-medium">
              <ThreadTitle
                threadId={threadId}
                thread={thread}
                isNewThread={isNewThread}
              />
            </div>
            <div className="flex min-w-0 shrink-0 items-center gap-0.5 sm:mr-4">
              {isCommandRoom && (
                <CommandRoomCapabilities
                  threadId={isNewThread ? undefined : threadId}
                  modelName={threadContext.model_name}
                  enabled={
                    !isMock && env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true"
                  }
                />
              )}
              <Tooltip content={t.agents.newChat}>
                <Button
                  className="px-2 sm:px-3"
                  size="sm"
                  variant="secondary"
                  onClick={() => {
                    const nextPath = `/workspace/agents/${agent_name}/chats/new`;
                    markThreadChatNavigationIntent(nextPath);
                    resetThreadRuntimeSlot(runtimeKey);
                    resetToNewThread();
                    router.push(nextPath);
                  }}
                >
                  <PlusSquare />
                  <span className="hidden sm:inline">{t.agents.newChat}</span>
                </Button>
              </Tooltip>
              <TokenUsageIndicator
                threadId={isNewThread ? undefined : threadId}
                backendUsage={backendTokenUsage}
                callerUsage={threadTokenUsage.data?.by_caller}
                contextUsage={threadContextUsage.data}
                enabled={tokenUsageEnabled || Boolean(threadContextUsage.data)}
                messages={thread.messages}
                pendingMessages={pendingUsageMessages}
                preferences={localSettings.tokenUsage}
                onPreferencesChange={(preferences) =>
                  setLocalSettings("tokenUsage", preferences)
                }
              />
              <ExportTrigger threadId={threadId} />
              <WorkRecordActivityTrigger />
              <WorkRecordTrigger />
              <ArtifactTrigger />
            </div>
          </header>

          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex min-h-0 flex-1 justify-center">
              <MessageList
                className={cn("size-full", !isWelcomeMode && "pt-10")}
                threadId={threadId}
                thread={thread}
                isCommandRoom={agent_name === "command-room"}
                contextSnapshot={
                  threadContextUsage.data?.latest_lead ??
                  threadContextUsage.data?.latest ??
                  null
                }
                paddingBottom={MESSAGE_LIST_DEFAULT_PADDING_BOTTOM}
                hasMoreHistory={hasMoreHistory}
                loadMoreHistory={loadMoreHistory}
                isHistoryLoading={isHistoryLoading}
                historyRuns={historyRuns}
                terminalNotice={terminalNotice}
                recoveryStatus={recoveryStatus}
                onRetryRecovery={retryRecovery}
                tokenUsageInlineMode={tokenUsageInlineMode}
                canRegenerate={
                  !isNewThread &&
                  !isMock &&
                  env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" &&
                  !isUploading &&
                  !thread.isLoading
                }
                onRegenerateMessage={handleRegenerate}
              />
            </div>

            <div
              className={cn(
                "right-0 bottom-0 left-0 z-30 flex justify-center px-3 sm:px-4",
                isWelcomeMode ? "absolute" : "relative shrink-0 pb-4",
              )}
            >
              <div
                className={cn(
                  "relative w-full",
                  isWelcomeMode &&
                    "-translate-y-[calc(50vh-48px)] sm:-translate-y-[calc(50vh-96px)]",
                  isWelcomeMode
                    ? "max-w-(--container-width-sm)"
                    : "max-w-(--container-width-md)",
                )}
              >
                {hasTodos && (
                  <div
                    className={cn(
                      "right-0 left-0 z-0",
                      isWelcomeMode ? "absolute -top-4" : "relative",
                    )}
                  >
                    <div
                      className={cn(
                        "right-0 bottom-0 left-0",
                        isWelcomeMode ? "absolute" : "relative",
                      )}
                    >
                      <TodoList
                        className="bg-background/5"
                        todos={thread.values.todos ?? []}
                        hidden={false}
                      />
                    </div>
                  </div>
                )}

                <InputBox
                  className={cn(
                    "bg-background/5 w-full",
                    isWelcomeMode && "-translate-y-2 sm:-translate-y-4",
                  )}
                  isWelcomeMode={isWelcomeMode}
                  threadId={threadId}
                  composerSessionId={runtimeKey}
                  autoFocus={isWelcomeMode}
                  status={
                    thread.error
                      ? "error"
                      : thread.isLoading
                        ? "streaming"
                        : "ready"
                  }
                  context={threadContext}
                  extraHeader={
                    isWelcomeMode && (
                      <AgentWelcome agent={agent} agentName={agent_name} />
                    )
                  }
                  disabled={
                    isMock ||
                    env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
                    isUploading
                  }
                  onContextChange={(context) => setSettings("context", context)}
                  onSubmit={handleSubmit}
                  onStop={handleStop}
                />
                {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" && (
                  <div className="text-muted-foreground/67 w-full translate-y-12 text-center text-xs">
                    {t.common.notAvailableInDemoMode}
                  </div>
                )}
              </div>
            </div>
          </main>
        </div>
      </ChatBox>
    </ThreadContext.Provider>
  );
}
