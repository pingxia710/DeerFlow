"use client";

import type { Message } from "@langchain/langgraph-sdk";
import { BrainCircuitIcon, ChevronDownIcon, CoinsIcon } from "lucide-react";
import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import {
  getCallerTokenUsageRows,
  getLeadAgentTokenUsage,
} from "@/core/threads/token-usage";
import type {
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
  const callerLabels = {
    lead_agent: t.tokenUsage.callerLeadAgent,
    subagent: t.tokenUsage.callerSubagent,
    middleware: t.tokenUsage.callerMiddleware,
  };

  if (!enabled || (!usage && !contextSnapshot)) {
    return null;
  }

  return (
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
              <span>{t.contextUsage.label}</span>
              <span className="font-mono">
                {formatContextCount(contextSnapshot.estimated_tokens)}
              </span>
            </>
          ) : (
            <>
              <CoinsIcon size={14} />
              <span>{t.tokenUsage.label}</span>
            </>
          )}
          {preferences.headerTotal ? (
            contextSnapshot ? (
              <>
                <span className="text-muted-foreground/70">/</span>
                <span>{t.tokenUsage.total}</span>
                <span className="font-mono">
                  {usage ? formatTokenCount(usage.totalTokens) : "-"}
                </span>
              </>
            ) : leadAgentTokens !== null ? (
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
              <>
                {contextSnapshot && <span>{t.tokenUsage.total}</span>}
                <span className="font-mono">
                  {usage ? formatTokenCount(usage.totalTokens) : "-"}
                </span>
              </>
            )
          ) : (
            <span className="font-mono">
              {t.tokenUsage.presets[presetKeyToTranslationKey(preset)]}
            </span>
          )}
          <ChevronDownIcon className="size-3" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="bottom" align="end" className="w-80">
        <DropdownMenuLabel>{t.contextUsage.title}</DropdownMenuLabel>
        <div className="px-2 py-1 text-xs">
          {contextSnapshot ? (
            <div className="space-y-1">
              <ContextUsageRow
                label={t.contextUsage.estimated}
                value={formatTokenCount(contextSnapshot.estimated_tokens)}
              />
              <ContextUsageRow
                label={t.contextUsage.messages}
                value={String(contextSnapshot.message_count)}
              />
              <ContextUsageRow
                label={t.contextUsage.tools}
                value={String(contextSnapshot.tool_schema_count)}
              />
              <ContextUsageRow
                label={t.contextUsage.chars}
                value={formatTokenCount(contextSnapshot.char_count)}
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
                      value={formatTokenCount(row.snapshot.estimated_tokens)}
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
    .sort((a, b) => b.snapshot.estimated_tokens - a.snapshot.estimated_tokens);
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
