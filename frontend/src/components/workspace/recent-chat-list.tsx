"use client";

import {
  Download,
  FileJson,
  FileText,
  LoaderCircleIcon,
  MoreHorizontal,
  Pencil,
  Share2,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useParams, usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuAction,
  SidebarMenuButton,
  SidebarMenuItem,
} from "@/components/ui/sidebar";
import {
  isThreadAtCommittedPath,
  markThreadChatNavigationIntent,
  resetThreadChatAfterDelete,
} from "@/components/workspace/chats/use-thread-chat";
import { getAPIClient } from "@/core/api";
import { writeTextToClipboard } from "@/core/clipboard";
import { useI18n } from "@/core/i18n/hooks";
import {
  exportThreadAsJSON,
  exportThreadAsMarkdown,
} from "@/core/threads/export";
import {
  clearThreadFinishedActivity,
  getThreadDeleteFailureState,
  shouldShowThreadRunningStatus,
  useDeleteThread,
  useInfiniteThreads,
  useRenameThread,
  useThreadActivity,
} from "@/core/threads/hooks";
import type { AgentThread, AgentThreadState } from "@/core/threads/types";
import {
  channelSourceOfThread,
  pathOfThread,
  titleOfThread,
} from "@/core/threads/utils";
import { env } from "@/env";
import { isIMEComposing } from "@/lib/ime";

import { ThreadChannelIcon } from "./thread-channel-source";

export function RecentChatList() {
  const { t } = useI18n();
  const router = useRouter();
  const pathname = usePathname();
  const { thread_id: threadIdFromPath, agent_name: agentNameFromPath } =
    useParams<{
      thread_id: string;
      agent_name?: string;
    }>();
  const {
    data: infiniteThreads,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
  } = useInfiniteThreads();
  const threads = useMemo(
    () => infiniteThreads?.pages.flat() ?? [],
    [infiniteThreads],
  );
  const threadActivity = useThreadActivity();

  useEffect(() => {
    if (!threadIdFromPath || threadIdFromPath === "new") {
      return;
    }
    clearThreadFinishedActivity(threadIdFromPath);
  }, [threadIdFromPath]);

  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const element = sentinelRef.current;
    if (!element || !hasNextPage) {
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      { rootMargin: "120px 0px 120px 0px" },
    );
    observer.observe(element);
    return () => observer.disconnect();
  }, [fetchNextPage, hasNextPage, isFetchingNextPage]);

  const { mutateAsync: deleteThread, isPending: isDeletingThread } =
    useDeleteThread();
  const { mutateAsync: renameThread, isPending: isRenaming } =
    useRenameThread();

  // Rename dialog state
  const [renameDialogOpen, setRenameDialogOpen] = useState(false);
  const [renameThreadId, setRenameThreadId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<AgentThread | null>(null);
  const [deletePhase, setDeletePhase] = useState<"remote" | "local" | null>(
    null,
  );
  const [deleteError, setDeleteError] = useState<
    "deleting" | "partial" | "failed" | null
  >(null);
  const cancelDeleteButtonRef = useRef<HTMLButtonElement>(null);

  const handleDeleteClick = useCallback(
    (thread: AgentThread) => {
      if (isDeletingThread) {
        return;
      }
      setDeleteTarget(thread);
      setDeleteError(null);
      setDeletePhase(null);
      setDeleteDialogOpen(true);
    },
    [isDeletingThread],
  );

  const handleDelete = useCallback(async () => {
    if (!deleteTarget || isDeletingThread) {
      return;
    }
    const currentPathname =
      typeof window === "undefined" ? pathname : window.location.pathname;
    const nextThreadPath = pathOfThread("new", {
      agent_name: agentNameFromPath,
    });
    const isCurrentThread = isThreadAtCommittedPath(
      deleteTarget.thread_id,
      currentPathname,
    );

    setDeleteError(null);
    setDeletePhase("remote");
    try {
      await deleteThread({
        threadId: deleteTarget.thread_id,
        onRemoteDeleteStarted: () => setDeletePhase("local"),
        onRemoteDeleted: () => {
          if (isCurrentThread) {
            resetThreadChatAfterDelete({
              deletedThreadId: deleteTarget.thread_id,
              nextPath: nextThreadPath,
              force: true,
            });
            void router.replace(nextThreadPath);
          }
        },
      });
      setDeleteDialogOpen(false);
      toast.success(t.conversation.deleteSuccess);
    } catch (error) {
      setDeletePhase(null);
      setDeleteError(getThreadDeleteFailureState(error));
      setDeleteDialogOpen(true);
    }
  }, [
    agentNameFromPath,
    deleteTarget,
    deleteThread,
    isDeletingThread,
    pathname,
    router,
    t.conversation.deleteSuccess,
  ]);

  const handleRenameClick = useCallback(
    (threadId: string, currentTitle: string) => {
      setRenameThreadId(threadId);
      setRenameValue(currentTitle);
      setRenameDialogOpen(true);
    },
    [],
  );

  const handleRenameSubmit = useCallback(async () => {
    const title = renameValue.trim();
    if (renameThreadId && title && !isRenaming) {
      try {
        await renameThread({ threadId: renameThreadId, title });
      } catch {
        toast.error(t.common.renameFailed);
        return;
      }
      setRenameDialogOpen(false);
      setRenameThreadId(null);
      setRenameValue("");
    }
  }, [isRenaming, renameThread, renameThreadId, renameValue, t]);

  const handleShare = useCallback(
    async (thread: AgentThread) => {
      // Always use Vercel URL for sharing so others can access
      const VERCEL_URL = "https://deer-flow-v2.vercel.app";
      const isLocalhost =
        window.location.hostname === "localhost" ||
        window.location.hostname === "127.0.0.1";
      // On localhost: use Vercel URL; On production: use current origin
      const baseUrl = isLocalhost ? VERCEL_URL : window.location.origin;
      const shareUrl = `${baseUrl}${pathOfThread(thread)}`;
      try {
        const didCopy = await writeTextToClipboard(shareUrl);
        if (!didCopy) {
          toast.error(t.clipboard.failedToCopyToClipboard);
          return;
        }

        toast.success(t.clipboard.linkCopied);
      } catch {
        toast.error(t.clipboard.failedToCopyToClipboard);
      }
    },
    [t],
  );

  const handleExport = useCallback(
    async (thread: AgentThread, format: "markdown" | "json") => {
      try {
        const apiClient = getAPIClient();
        const state = await apiClient.threads.getState<AgentThreadState>(
          thread.thread_id,
        );
        const messages = state.values?.messages ?? [];
        if (messages.length === 0) {
          toast.error(t.conversation.noMessages);
          return;
        }
        if (format === "markdown") {
          exportThreadAsMarkdown(thread, messages);
        } else {
          exportThreadAsJSON(thread, messages);
        }
        toast.success(t.common.exportSuccess);
      } catch {
        toast.error("Failed to export conversation");
      }
    },
    [t],
  );

  if (threads.length === 0 && !deleteDialogOpen) {
    return null;
  }
  return (
    <>
      <SidebarGroup>
        <SidebarGroupLabel>
          {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true"
            ? t.sidebar.recentChats
            : t.sidebar.demoChats}
        </SidebarGroupLabel>
        <SidebarGroupContent className="group-data-[collapsible=icon]:pointer-events-none group-data-[collapsible=icon]:-mt-8 group-data-[collapsible=icon]:opacity-0">
          <SidebarMenu>
            <div className="flex w-full flex-col gap-1">
              {threads.map((thread) => {
                const threadPath = pathOfThread(thread);
                const isActive = threadPath === pathname;
                const channelSource = channelSourceOfThread(thread);
                const isRunning = shouldShowThreadRunningStatus(
                  thread.status,
                  threadActivity.running.has(thread.thread_id),
                );
                const isFinished =
                  !isRunning && threadActivity.finished.has(thread.thread_id);
                const threadTitle = titleOfThread(thread);
                const activityLabel = isRunning
                  ? t.sidebar.chatRunning
                  : isFinished
                    ? t.sidebar.chatFinished
                    : null;
                return (
                  <SidebarMenuItem
                    key={thread.thread_id}
                    className="group/side-menu-item"
                  >
                    <SidebarMenuButton isActive={isActive} asChild>
                      <Link
                        aria-label={
                          activityLabel
                            ? `${threadTitle}, ${activityLabel}`
                            : threadTitle
                        }
                        className="text-muted-foreground min-w-0 whitespace-nowrap group-hover/side-menu-item:overflow-hidden"
                        href={pathOfThread(thread)}
                        onNavigate={() =>
                          markThreadChatNavigationIntent(pathOfThread(thread))
                        }
                        title={
                          activityLabel
                            ? `${threadTitle} - ${activityLabel}`
                            : threadTitle
                        }
                      >
                        <ThreadChannelIcon source={channelSource} />
                        <span className="min-w-0 truncate">{threadTitle}</span>
                        {(isRunning || isFinished || channelSource) && (
                          <span className="ml-auto inline-flex shrink-0 items-center gap-1">
                            {activityLabel && (
                              <span className="sr-only">{activityLabel}</span>
                            )}
                            {isRunning && (
                              <LoaderCircleIcon
                                aria-hidden="true"
                                className="text-muted-foreground size-3.5 animate-spin"
                              />
                            )}
                            {isFinished && (
                              <span
                                aria-hidden="true"
                                className="size-2 rounded-full bg-blue-500"
                              />
                            )}
                            {channelSource && (
                              <span
                                className="bg-muted text-muted-foreground inline-flex h-5 max-w-14 items-center rounded-md px-1.5 text-[10px] font-medium"
                                title={`${channelSource.label} channel`}
                              >
                                <span className="truncate">
                                  {channelSource.label}
                                </span>
                              </span>
                            )}
                          </span>
                        )}
                      </Link>
                    </SidebarMenuButton>
                    {env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY !== "true" && (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <SidebarMenuAction
                            showOnHover
                            className="bg-background/50 hover:bg-background after:left-0!"
                          >
                            <MoreHorizontal />
                            <span className="sr-only">{t.common.more}</span>
                          </SidebarMenuAction>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent
                          className="w-48 rounded-lg"
                          side={"right"}
                          align={"start"}
                        >
                          <DropdownMenuItem
                            onSelect={() =>
                              handleRenameClick(
                                thread.thread_id,
                                titleOfThread(thread),
                              )
                            }
                          >
                            <Pencil className="text-muted-foreground" />
                            <span>{t.common.rename}</span>
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onSelect={() => handleShare(thread)}
                          >
                            <Share2 className="text-muted-foreground" />
                            <span>{t.common.share}</span>
                          </DropdownMenuItem>
                          <DropdownMenuSub>
                            <DropdownMenuSubTrigger>
                              <Download className="text-muted-foreground" />
                              <span>{t.common.export}</span>
                            </DropdownMenuSubTrigger>
                            <DropdownMenuSubContent>
                              <DropdownMenuItem
                                onSelect={() =>
                                  handleExport(thread, "markdown")
                                }
                              >
                                <FileText className="text-muted-foreground" />
                                <span>{t.common.exportAsMarkdown}</span>
                              </DropdownMenuItem>
                              <DropdownMenuItem
                                onSelect={() => handleExport(thread, "json")}
                              >
                                <FileJson className="text-muted-foreground" />
                                <span>{t.common.exportAsJSON}</span>
                              </DropdownMenuItem>
                            </DropdownMenuSubContent>
                          </DropdownMenuSub>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            disabled={isDeletingThread}
                            onSelect={() => handleDeleteClick(thread)}
                          >
                            <Trash2 className="text-muted-foreground" />
                            <span>{t.common.delete}</span>
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </SidebarMenuItem>
                );
              })}
              {hasNextPage && (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mx-2 my-1 w-[calc(100%-1rem)] justify-center text-xs"
                    onClick={() => void fetchNextPage()}
                    disabled={isFetchingNextPage}
                    data-testid="recent-chat-list-load-more"
                  >
                    {isFetchingNextPage
                      ? t.chats.loadingMore
                      : t.chats.loadOlderChats}
                  </Button>
                  <div
                    ref={sentinelRef}
                    aria-hidden="true"
                    className="h-px w-full"
                    data-testid="recent-chat-list-sentinel"
                  />
                </>
              )}
            </div>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>

      {/* Rename Dialog */}
      <Dialog open={renameDialogOpen} onOpenChange={setRenameDialogOpen}>
        <DialogContent className="sm:max-w-[425px]">
          <DialogHeader>
            <DialogTitle>{t.common.rename}</DialogTitle>
          </DialogHeader>
          <div className="py-4">
            <Input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder={t.common.rename}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !isIMEComposing(e)) {
                  e.preventDefault();
                  void handleRenameSubmit();
                }
              }}
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRenameDialogOpen(false)}
            >
              {t.common.cancel}
            </Button>
            <Button
              disabled={isRenaming}
              onClick={() => void handleRenameSubmit()}
            >
              {t.common.save}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={deleteDialogOpen}
        onOpenChange={(open) => {
          if (!isDeletingThread) {
            setDeleteDialogOpen(open);
            if (!open) {
              setDeleteError(null);
            }
          }
        }}
      >
        <DialogContent
          aria-busy={isDeletingThread}
          showCloseButton={!isDeletingThread}
          onOpenAutoFocus={(event) => {
            event.preventDefault();
            cancelDeleteButtonRef.current?.focus();
          }}
          onEscapeKeyDown={(event) => {
            if (isDeletingThread) {
              event.preventDefault();
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>{t.conversation.deleteTitle}</DialogTitle>
            <DialogDescription>
              {t.conversation.deleteDescription}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 text-sm">
            {deleteTarget && (
              <p className="break-words">
                <span className="font-medium">
                  {t.conversation.deleteTarget}
                </span>{" "}
                {titleOfThread(deleteTarget)}
              </p>
            )}
            <div>
              <p className="font-medium">{t.conversation.deleteWillRemove}</p>
              <ul className="text-muted-foreground mt-2 list-disc space-y-1 pl-5">
                <li>{t.conversation.deleteMessagesAndConversation}</li>
                <li>{t.conversation.deleteLocalThreadData}</li>
                <li>{t.conversation.deleteActiveRuns}</li>
                <li>{t.conversation.deleteRunRecords}</li>
              </ul>
            </div>
            <div>
              <p className="font-medium">
                {t.conversation.deleteWillNotRemove}
              </p>
              <ul className="text-muted-foreground mt-2 list-disc space-y-1 pl-5">
                <li>{t.conversation.deleteSavedMemory}</li>
                <li>{t.conversation.deleteExternalMessages}</li>
              </ul>
            </div>
            {deletePhase && (
              <p aria-live="polite" role="status">
                {deletePhase === "remote"
                  ? t.conversation.deleteDeletingConversation
                  : t.conversation.deleteFinishingLocalCleanup}
              </p>
            )}
            {deleteError && (
              <p className="text-destructive" role="alert">
                {deleteError === "deleting"
                  ? t.conversation.deleteInProgress
                  : deleteError === "partial"
                    ? t.conversation.deletePartialFailure
                    : t.conversation.deleteFailed}
              </p>
            )}
          </div>
          <DialogFooter>
            <Button
              ref={cancelDeleteButtonRef}
              variant="outline"
              disabled={isDeletingThread}
              onClick={() => {
                setDeleteDialogOpen(false);
                setDeleteError(null);
              }}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              disabled={isDeletingThread || !deleteTarget}
              onClick={() => void handleDelete()}
            >
              {isDeletingThread
                ? t.common.loading
                : deleteError
                  ? t.conversation.retryDelete
                  : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
