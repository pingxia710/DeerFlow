"use client";

import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  GaugeIcon,
  LoaderCircleIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip } from "@/components/workspace/tooltip";
import { useCapabilitySnapshot } from "@/core/capabilities";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import {
  getCommandRoomCapabilityHealth,
  getCommandRoomModelLabel,
} from "./command-room-capability-state";

function FactRow({
  label,
  value,
  warning = false,
}: {
  label: string;
  value: string;
  warning?: boolean;
}) {
  return (
    <div className="grid grid-cols-[6rem_minmax(0,1fr)] gap-2 py-1 text-xs">
      <span className="text-muted-foreground">{label}</span>
      <span
        className={cn(
          "min-w-0 text-right break-words",
          warning && "text-amber-700 dark:text-amber-400",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function formatNames(names: readonly string[] | undefined, fallback: string) {
  return names && names.length > 0 ? names.join(", ") : fallback;
}

export function CommandRoomCapabilities({
  threadId,
  modelName,
  enabled = true,
}: {
  threadId?: string;
  modelName?: string;
  enabled?: boolean;
}) {
  const { t } = useI18n();
  const query = useCapabilitySnapshot(threadId, { enabled });
  const runtime = query.data?.command_room_runtime;
  const health = runtime
    ? getCommandRoomCapabilityHealth(runtime)
    : { status: "warning" as const, issueCount: query.error ? 1 : 0 };
  const warning = health.status === "warning";

  return (
    <DropdownMenu>
      <Tooltip content={t.capabilities.title}>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            aria-label={t.capabilities.title}
            className={cn(
              "text-muted-foreground relative",
              warning && "text-amber-700 dark:text-amber-400",
            )}
          >
            {query.isLoading ? (
              <LoaderCircleIcon className="animate-spin" />
            ) : (
              <GaugeIcon />
            )}
            {warning && !query.isLoading && (
              <span className="bg-background absolute top-0.5 right-0.5 size-2 rounded-full border border-amber-500 bg-amber-500" />
            )}
          </Button>
        </DropdownMenuTrigger>
      </Tooltip>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel className="flex items-center justify-between gap-2">
          <span>{t.capabilities.title}</span>
          <span
            className={cn(
              "flex items-center gap-1 text-xs font-normal",
              warning
                ? "text-amber-700 dark:text-amber-400"
                : "text-emerald-700 dark:text-emerald-400",
            )}
          >
            {warning ? <AlertTriangleIcon /> : <CheckCircle2Icon />}
            {warning ? t.capabilities.attention : t.capabilities.ready}
          </span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <div className="px-2 py-1">
          <FactRow
            label={t.capabilities.model}
            value={runtime ? getCommandRoomModelLabel(runtime, modelName) : "-"}
            warning={runtime?.agent_config.model_fallback}
          />
          <FactRow
            label={t.capabilities.directTools}
            value={formatNames(runtime?.direct.configured_tools, "-")}
          />
          <FactRow
            label={t.capabilities.skills}
            value={formatNames(runtime?.skills.loaded, t.capabilities.none)}
          />
          {(runtime?.skills.missing.length ?? 0) > 0 && (
            <FactRow
              label={t.capabilities.missingSkills}
              value={runtime?.skills.missing.join(", ") ?? ""}
              warning
            />
          )}
          <FactRow
            label={t.capabilities.childModel}
            value={
              runtime?.task_transport.model ??
              runtime?.task_transport.configured_model ??
              "-"
            }
          />
          <FactRow
            label={t.capabilities.reasoningEffort}
            value={runtime?.task_transport.reasoning_effort ?? "-"}
          />
          <FactRow
            label={t.capabilities.timeout}
            value={runtime ? `${runtime.task_transport.timeout_seconds}s` : "-"}
          />
          <FactRow
            label={t.capabilities.sandbox}
            value={runtime?.task_transport.sandbox_mode ?? "-"}
          />
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
