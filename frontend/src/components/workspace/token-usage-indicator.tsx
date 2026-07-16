"use client";

import type { Message } from "@langchain/langgraph-sdk";
import {
  BrainCircuitIcon,
  CheckCircle2Icon,
  ChevronDownIcon,
  CoinsIcon,
  FileTextIcon,
} from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useI18n } from "@/core/i18n/hooks";
import {
  formatContextCount,
  formatTokenCount,
  selectHeaderTokenUsage,
  type TokenUsage,
} from "@/core/messages/usage";
import {
  getTokenUsageViewPreset,
  tokenUsagePreferencesFromPreset,
  type TokenUsagePreferences,
  type TokenUsageViewPreset,
} from "@/core/messages/usage-model";
import { useThreadContextDetail } from "@/core/threads/hooks";
import {
  getCallerTokenUsageRows,
  getLeadAgentTokenUsage,
} from "@/core/threads/token-usage";
import type {
  ThreadContextUsageSnapshot,
  ThreadContextUsageResponse,
  ThreadTokenUsageResponse,
} from "@/core/threads/types";
import { cn } from "@/lib/utils";

interface TokenUsageIndicatorProps {
  threadId?: string;
  messages: Message[];
  pendingMessages?: Message[];
  backendUsage?: TokenUsage | null;
  callerUsage?: ThreadTokenUsageResponse["by_caller"] | null;
  contextUsage?: ThreadContextUsageResponse | null;
  enabled?: boolean;
  preferences: TokenUsagePreferences;
  onPreferencesChange: (preferences: TokenUsagePreferences) => void;
  className?: string;
}

export function TokenUsageIndicator({
  threadId,
  messages,
  pendingMessages,
  backendUsage,
  callerUsage,
  contextUsage,
  enabled = false,
  preferences,
  onPreferencesChange,
  className,
}: TokenUsageIndicatorProps) {
  const { t } = useI18n();

  const usage = useMemo(
    () =>
      selectHeaderTokenUsage({
        backendUsage: threadId ? backendUsage : null,
        messages,
        pendingMessages,
      }),
    [backendUsage, messages, pendingMessages, threadId],
  );
  const preset = getTokenUsageViewPreset(preferences);
  const callerRows = getCallerTokenUsageRows(callerUsage);
  const leadAgentTokens = getLeadAgentTokenUsage(callerUsage);
  const contextSnapshot = contextUsage?.latest_lead ?? contextUsage?.latest;
  const contextCallerRows = getContextCallerRows(contextUsage);
  const [contextInspectorOpen, setContextInspectorOpen] = useState(false);
  const [selectedContext, setSelectedContext] =
    useState<ThreadContextUsageSnapshot | null>(null);
  const selectedContextSnapshot = selectedContext ?? contextSnapshot ?? null;
  const contextDetail = useThreadContextDetail(
    threadId,
    selectedContextSnapshot?.run_id,
    selectedContextSnapshot?.seq,
    {
      enabled:
        contextInspectorOpen && Boolean(selectedContextSnapshot?.has_full_text),
    },
  );
  const callerLabels = {
    lead_agent: t.tokenUsage.callerLeadAgent,
    subagent: t.tokenUsage.callerSubagent,
    middleware: t.tokenUsage.callerMiddleware,
  };

  if (!enabled || (!usage && !contextSnapshot)) {
    return null;
  }

  const openContextInspector = () => {
    const preferred =
      (contextUsage?.latest_lead?.has_full_text
        ? contextUsage.latest_lead
        : null) ??
      contextUsage?.recent.find((snapshot) => snapshot.has_full_text) ??
      contextSnapshot ??
      null;
    setSelectedContext(preferred);
    setContextInspectorOpen(true);
  };

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            className={cn(
              "text-muted-foreground bg-background/70 hover:bg-background/90 flex h-auto items-center gap-1.5 rounded-full border px-2 py-1 text-xs font-normal",
              className,
            )}
          >
            {contextSnapshot ? (
              <>
                <BrainCircuitIcon size={14} />
                <span className="hidden sm:inline">{t.contextUsage.label}</span>
                <span className="font-mono">
                  {formatContextCount(contextSnapshot.char_count)}
                </span>
                <span className="hidden sm:inline">
                  {t.contextUsage.charUnit}
                </span>
              </>
            ) : (
              <>
                <CoinsIcon size={14} />
                <span className="hidden sm:inline">{t.tokenUsage.label}</span>
              </>
            )}
            {!contextSnapshot &&
              (preferences.headerTotal ? (
                leadAgentTokens !== null ? (
                  <>
                    <span>{t.tokenUsage.callerLeadAgentShort}</span>
                    <span className="font-mono">
                      {formatTokenCount(leadAgentTokens)}
                    </span>
                    {usage && usage.totalTokens !== leadAgentTokens && (
                      <span className="text-muted-foreground/70 hidden items-center gap-1 lg:inline-flex">
                        <span>/</span>
                        <span>{t.tokenUsage.total}</span>
                        <span className="font-mono">
                          {formatTokenCount(usage.totalTokens)}
                        </span>
                      </span>
                    )}
                  </>
                ) : (
                  <span className="font-mono">
                    {usage ? formatTokenCount(usage.totalTokens) : "-"}
                  </span>
                )
              ) : (
                <span className="font-mono">
                  {t.tokenUsage.presets[presetKeyToTranslationKey(preset)]}
                </span>
              ))}
            <ChevronDownIcon className="size-3" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent side="bottom" align="end" className="w-80">
          <DropdownMenuLabel>{t.contextUsage.title}</DropdownMenuLabel>
          <div className="px-2 py-1 text-xs">
            {contextSnapshot ? (
              <div className="space-y-1">
                <ContextUsageRow
                  label={t.contextUsage.chars}
                  value={`${formatContextCount(contextSnapshot.char_count)} ${t.contextUsage.charUnit}`}
                />
                <ContextUsageRow
                  label={t.contextUsage.messages}
                  value={String(contextSnapshot.message_count)}
                />
                <ContextUsageRow
                  label={t.contextUsage.tools}
                  value={String(contextSnapshot.tool_schema_count)}
                />
                {contextCallerRows.length > 0 && (
                  <div className="border-t pt-1">
                    <div className="text-muted-foreground pb-0.5">
                      {t.contextUsage.byCaller}
                    </div>
                    {contextCallerRows.map((row) => (
                      <ContextUsageRow
                        key={row.caller}
                        label={formatContextCaller(row.caller, t)}
                        value={`${formatContextCount(row.snapshot.char_count)} ${t.contextUsage.charUnit}`}
                      />
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-muted-foreground">
                {t.contextUsage.unavailable}
              </div>
            )}
          </div>
          <DropdownMenuItem
            disabled={!threadId || !contextUsage?.recent.length}
            onSelect={openContextInspector}
          >
            <FileTextIcon />
            {t.contextUsage.openFullText}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>{t.tokenUsage.title}</DropdownMenuLabel>
          <div className="px-2 py-1 text-xs">
            {usage ? (
              <div className="space-y-1">
                <div className="flex justify-between gap-4">
                  <span>{t.tokenUsage.input}</span>
                  <span className="font-mono">
                    {formatTokenCount(usage.inputTokens)}
                  </span>
                </div>
                <div className="flex justify-between gap-4">
                  <span>{t.tokenUsage.output}</span>
                  <span className="font-mono">
                    {formatTokenCount(usage.outputTokens)}
                  </span>
                </div>
                <div className="border-t pt-1">
                  <div className="flex justify-between gap-4">
                    <span>{t.tokenUsage.total}</span>
                    <span className="font-mono font-medium">
                      {formatTokenCount(usage.totalTokens)}
                    </span>
                  </div>
                </div>
                {callerRows.length > 0 && (
                  <div className="border-t pt-1">
                    <div className="text-muted-foreground pb-0.5">
                      {t.tokenUsage.callerBreakdown}
                    </div>
                    {callerRows.map((row) => (
                      <div key={row.key} className="flex justify-between gap-4">
                        <span>{callerLabels[row.key]}</span>
                        <span className="font-mono">
                          {formatTokenCount(row.tokens)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="text-muted-foreground">
                {t.tokenUsage.unavailable}
              </div>
            )}
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuLabel>{t.tokenUsage.view}</DropdownMenuLabel>
          <DropdownMenuRadioGroup
            value={preset}
            onValueChange={(value) =>
              onPreferencesChange(
                tokenUsagePreferencesFromPreset(value as TokenUsageViewPreset),
              )
            }
          >
            {(
              ["off", "summary", "per_turn", "debug"] as TokenUsageViewPreset[]
            ).map((value) => {
              const translationKey = presetKeyToTranslationKey(value);
              return (
                <DropdownMenuRadioItem key={value} value={value}>
                  <div className="grid gap-0.5">
                    <span>{t.tokenUsage.presets[translationKey]}</span>
                    <span className="text-muted-foreground text-xs">
                      {t.tokenUsage.presetDescriptions[translationKey]}
                    </span>
                  </div>
                </DropdownMenuRadioItem>
              );
            })}
          </DropdownMenuRadioGroup>
          <DropdownMenuSeparator />
          <div className="text-muted-foreground px-2 py-2 text-xs leading-relaxed">
            {t.tokenUsage.note}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog
        open={contextInspectorOpen}
        onOpenChange={setContextInspectorOpen}
      >
        <DialogContent className="h-[calc(100vh-2rem)] grid-rows-[auto_minmax(0,1fr)] gap-0 p-0 sm:h-[min(52rem,calc(100vh-4rem))] sm:max-w-6xl">
          <DialogHeader className="border-b px-6 py-4 pr-12">
            <DialogTitle>{t.contextUsage.title}</DialogTitle>
            <DialogDescription>{t.contextUsage.complete}</DialogDescription>
          </DialogHeader>
          <div className="grid min-h-0 grid-cols-1 md:grid-cols-[17rem_minmax(0,1fr)]">
            <ScrollArea className="border-b md:border-r md:border-b-0">
              <div className="space-y-1 p-3">
                <div className="text-muted-foreground px-2 pb-1 text-xs font-medium">
                  {t.contextUsage.calls}
                </div>
                {(contextUsage?.recent ?? []).map((snapshot) => {
                  const selected =
                    selectedContextSnapshot?.run_id === snapshot.run_id &&
                    selectedContextSnapshot.seq === snapshot.seq;
                  return (
                    <button
                      key={`${snapshot.run_id}:${snapshot.seq}`}
                      type="button"
                      className={cn(
                        "hover:bg-muted flex w-full flex-col gap-1 rounded-md px-2 py-2 text-left text-xs",
                        selected && "bg-muted",
                      )}
                      onClick={() => setSelectedContext(snapshot)}
                    >
                      <span className="font-medium">
                        {formatContextCaller(snapshot.caller, t)} · #
                        {snapshot.llm_call_index}
                      </span>
                      <span className="text-muted-foreground font-mono">
                        {formatContextCount(snapshot.char_count)}{" "}
                        {t.contextUsage.charUnit}
                      </span>
                    </button>
                  );
                })}
              </div>
            </ScrollArea>
            <ScrollArea className="min-h-0">
              <div className="space-y-5 p-4 sm:p-6">
                {contextDetail.isLoading ? (
                  <div className="text-muted-foreground text-sm">
                    {t.contextUsage.loading}
                  </div>
                ) : contextDetail.data ? (
                  <>
                    <div className="flex flex-wrap items-center gap-2 text-xs">
                      <span className="bg-muted rounded-full px-2 py-1 font-medium">
                        {formatContextCaller(contextDetail.data.caller, t)} · #
                        {contextDetail.data.llm_call_index}
                      </span>
                      <span className="text-muted-foreground font-mono">
                        {formatContextCount(contextDetail.data.char_count)}{" "}
                        {t.contextUsage.charUnit}
                      </span>
                      <span className="flex items-center gap-1 text-emerald-700 dark:text-emerald-400">
                        <CheckCircle2Icon className="size-3.5" />
                        {t.contextUsage.complete}
                      </span>
                    </div>

                    <ContextPayloadSection
                      title={t.contextUsage.modelMessages}
                      values={contextDetail.data.messages}
                    />
                    <ContextPayloadSection
                      title={t.contextUsage.toolSchemas}
                      values={contextDetail.data.tool_schemas}
                    />
                  </>
                ) : (
                  <div className="text-muted-foreground text-sm">
                    {selectedContextSnapshot?.has_full_text
                      ? t.contextUsage.unavailable
                      : t.contextUsage.fullTextUnavailable}
                  </div>
                )}
              </div>
            </ScrollArea>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

export function ContextPayloadSection({
  title,
  values,
}: {
  title: string;
  values: unknown[];
}) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {values.length === 0 ? (
        <div className="text-muted-foreground rounded-md border px-3 py-2 text-xs">
          —
        </div>
      ) : (
        <div className="space-y-3">
          {values.map((value, index) => (
            <pre
              key={index}
              className="bg-muted/60 overflow-x-auto rounded-md border p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap"
            >
              {JSON.stringify(value, null, 2)}
            </pre>
          ))}
        </div>
      )}
    </section>
  );
}

function ContextUsageRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4">
      <span>{label}</span>
      <span className="font-mono">{value}</span>
    </div>
  );
}

function getContextCallerRows(
  usage: ThreadContextUsageResponse | null | undefined,
) {
  return Object.entries(usage?.by_caller ?? {})
    .map(([caller, snapshot]) => ({ caller, snapshot }))
    .sort((a, b) => b.snapshot.char_count - a.snapshot.char_count);
}

function formatContextCaller(
  caller: string,
  t: ReturnType<typeof useI18n>["t"],
) {
  if (caller === "lead_agent") {
    return t.tokenUsage.callerLeadAgent;
  }
  if (caller.startsWith("subagent:")) {
    return t.tokenUsage.callerSubagent;
  }
  if (caller.startsWith("middleware:")) {
    return t.tokenUsage.callerMiddleware;
  }
  return caller;
}

function presetKeyToTranslationKey(preset: TokenUsageViewPreset) {
  switch (preset) {
    case "per_turn":
      return "perTurn" as const;
    default:
      return preset;
  }
}
