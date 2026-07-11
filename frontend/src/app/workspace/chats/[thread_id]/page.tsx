"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { type PromptInputMessage } from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { ArtifactTrigger } from "@/components/workspace/artifacts";
import {
  ChatBox,
  isThreadFinishForVisibleChat,
  pendingNavigationAllowsThreadStart,
  shouldShowWelcomeMode,
  useSpecificChatMode,
  useThreadChat,
} from "@/components/workspace/chats";
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
import { Welcome } from "@/components/workspace/welcome";
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
  getThreadHistoryLoadErrorKind,
  type ThreadStreamFinishMeta,
  useThreadContextUsage,
  useThreadMetadata,
  useThreadTokenUsage,
} from "@/core/threads/hooks";
import { useThreadRuntime } from "@/core/threads/runtime";
import { threadTokenUsageToTokenUsage } from "@/core/threads/token-usage";
import { pathOfThread, textOfMessage } from "@/core/threads/utils";
import { env } from "@/env";
import { cn } from "@/lib/utils";

export function getChatRuntimeKey(threadId: string, isNewThread: boolean) {
  return isNewThread ? `new-chat:${threadId}` : `chat:${threadId}`;
}

export default function ChatPage() {
  const { t } = useI18n();
  const router = useRouter();
  const { threadId, setThreadId, isNewThread, setIsNewThread, isMock } =
    useThreadChat();
  // `isNewThread` tracks whether the backend has the thread yet — gates the
  // SDK's history fetch (see issue #2746).  `isWelcomeMode` is the visual
  // welcome layout (centered input, hero, quick actions); we flip it to false
  // the moment the user submits so the UI animates immediately, even though
  // `isNewThread` stays true until the backend actually creates the thread.
  const [isWelcomeMode, setIsWelcomeMode] = useState(isNewThread);
  const [settings, setSettings] = useThreadSettings("chat", threadId);
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
  const mountedRef = useRef(false);
  const defaultedModeThreadIdRef = useRef<string | null>(null);
  const visibleThreadIdRef = useRef(threadId);
  const pendingStartThreadIdRef = useRef<string | null>(null);
  useSpecificChatMode();

  visibleThreadIdRef.current = threadId;

  useEffect(() => {
    mountedRef.current = true;
  }, []);

  useEffect(() => {
    if (!isNewThread) {
      defaultedModeThreadIdRef.current = null;
      return;
    }
    if (defaultedModeThreadIdRef.current === threadId) {
      return;
    }
    defaultedModeThreadIdRef.current = threadId;
    if (settings.context.mode !== "ultra") {
      setSettings("context", { mode: "ultra" });
    }
  }, [isNewThread, setSettings, settings.context.mode, threadId]);

  const { showNotification } = useNotification();

  const runtimeKey = getChatRuntimeKey(threadId, isNewThread);

  const threadRuntime = useMemo(
    () => ({
      runtimeScope: "chat",
      runtimeKey,
      threadId: isNewThread ? undefined : threadId,
      displayThreadId: threadId,
      context: settings.context,
      isMock,
      // onSend only animates the UI; do NOT flip `isNewThread` here — the
      // LangGraph SDK eagerly fetches /history the moment it receives a
      // thread id and assumes the thread exists on the backend (issue #2746).
      onSend: (sentThreadId: string) => {
        pendingStartThreadIdRef.current = sentThreadId;
        setIsWelcomeMode(false);
      },
      onStart: (createdThreadId: string) => {
        const pendingThreadId = pendingStartThreadIdRef.current;
        const visibleThreadId = visibleThreadIdRef.current;
        const currentPathname = window.location.pathname;
        const streamStillOwnsVisibleChat =
          pendingNavigationAllowsThreadStart(createdThreadId) &&
          (visibleThreadId === createdThreadId ||
            (pendingThreadId !== null &&
              visibleThreadId === pendingThreadId &&
              currentPathname.endsWith("/new")));
        if (!streamStillOwnsVisibleChat) {
          return;
        }
        migrateThreadModelName("chat", visibleThreadId, createdThreadId);
        visibleThreadIdRef.current = createdThreadId;
        pendingStartThreadIdRef.current = null;
        // ! Important: Never use next.js router for navigation in this case, otherwise it will cause the thread to re-mount and lose all states. Use native history API instead.
        history.replaceState(null, "", `/workspace/chats/${createdThreadId}`);
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
          const lastMessage = state.messages.at(-1);
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
    }),
    [
      isMock,
      isNewThread,
      setIsNewThread,
      setThreadId,
      runtimeKey,
      settings.context,
      showNotification,
      threadId,
    ],
  );

  const {
    thread,
    pendingUsageMessages,
    sendMessage,
    regenerateMessage,
    isUploading,
    isHistoryLoading,
    historyRuns,
    historyError,
    terminalNotice,
    recoveryStatus,
    retryRecovery,
    hasMoreHistory,
    loadMoreHistory,
  } = useThreadRuntime(threadRuntime);

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
    const agentName = threadMetadata.data?.metadata?.agent_name;
    if (
      isNewThread ||
      isMock ||
      typeof agentName !== "string" ||
      agentName.length === 0
    ) {
      return;
    }

    const agentPath = pathOfThread(threadId, { agent_name: agentName });
    if (window.location.pathname !== agentPath) {
      router.replace(agentPath);
    }
  }, [isMock, isNewThread, router, threadId, threadMetadata.data]);

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
  const historyErrorKind = historyError
    ? getThreadHistoryLoadErrorKind(historyError)
    : null;
  const historyErrorCopy =
    historyErrorKind === "not-found"
      ? {
          title: t.chats.historyNotFoundTitle,
          description: t.chats.historyNotFoundDescription,
        }
      : historyErrorKind === "forbidden"
        ? {
            title: t.chats.historyForbiddenTitle,
            description: t.chats.historyForbiddenDescription,
          }
        : historyErrorKind === "failed"
          ? {
              title: t.chats.historyLoadFailedTitle,
              description: t.chats.historyLoadFailedDescription,
            }
          : null;

  return (
    <ThreadContext.Provider value={{ thread, isMock }}>
      <ChatBox threadId={threadId}>
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
            <div className="flex min-w-0 flex-1 items-center text-sm font-medium">
              <ThreadTitle
                threadId={threadId}
                thread={thread}
                isNewThread={isNewThread}
              />
            </div>
            <div className="flex shrink-0 items-center gap-2">
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
              <ArtifactTrigger />
            </div>
          </header>
          <main className="flex min-h-0 max-w-full grow flex-col">
            <div className="flex min-h-0 flex-1 justify-center">
              {historyErrorCopy && !isHistoryLoading ? (
                <div className="flex size-full items-center justify-center px-4 pt-10">
                  <div className="border-border/60 bg-background/90 max-w-sm rounded-lg border p-4 text-center shadow-sm">
                    <div className="text-sm font-medium">
                      {historyErrorCopy.title}
                    </div>
                    <div className="text-muted-foreground mt-2 text-sm">
                      {historyErrorCopy.description}
                    </div>
                    <Button
                      className="mt-4"
                      size="sm"
                      type="button"
                      variant="outline"
                      onClick={() => {
                        void loadMoreHistory();
                      }}
                    >
                      {t.common.retry}
                    </Button>
                  </div>
                </div>
              ) : (
                <MessageList
                  className={cn("size-full", !isWelcomeMode && "pt-10")}
                  threadId={threadId}
                  thread={thread}
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
              )}
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
                {mountedRef.current ? (
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
                    context={settings.context}
                    extraHeader={
                      isWelcomeMode && <Welcome mode={settings.context.mode} />
                    }
                    disabled={
                      isMock ||
                      env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true" ||
                      isUploading
                    }
                    onContextChange={(context) =>
                      setSettings("context", context)
                    }
                    onSubmit={handleSubmit}
                    onStop={handleStop}
                  />
                ) : (
                  <div
                    aria-hidden="true"
                    className={cn(
                      "bg-background/5 h-32 w-full rounded-2xl",
                      isWelcomeMode && "-translate-y-2 sm:-translate-y-4",
                    )}
                  />
                )}
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
