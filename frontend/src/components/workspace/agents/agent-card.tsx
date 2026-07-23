"use client";

import {
  BotIcon,
  MessageSquareIcon,
  Settings2Icon,
  Trash2Icon,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { type ComponentProps, type ReactElement, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { beginThreadNavigation } from "@/components/workspace/chats";
import { useDeleteAgent, useUpdateAgent } from "@/core/agents";
import type { Agent } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import type { Model } from "@/core/models/types";
import { cn } from "@/lib/utils";

import { ModelConfigDialog } from "./model-config-dialog";

interface AgentCardProps {
  agent: Agent;
  models: Model[];
}

/**
 * Reveals the full text in a tooltip ONLY when its trigger is actually clipped.
 * Clipping is measured on pointer enter against the trigger's own box, covering
 * both single-line `truncate` (width) and multi-line `line-clamp` (height), so
 * untruncated content never pops a redundant tooltip.
 */
function TruncatedTooltip({
  text,
  children,
}: {
  text: string;
  children: ReactElement;
}) {
  const [truncated, setTruncated] = useState(false);
  return (
    <Tooltip>
      <TooltipTrigger
        asChild
        onPointerEnter={(e) => {
          const el = e.currentTarget;
          setTruncated(
            el.scrollWidth > el.clientWidth ||
              el.scrollHeight > el.clientHeight,
          );
        }}
      >
        {children}
      </TooltipTrigger>
      {truncated && (
        <TooltipContent className="max-w-xs text-wrap break-words">
          {text}
        </TooltipContent>
      )}
    </Tooltip>
  );
}

/**
 * Long, user-controlled labels (agent model, skills, tool groups) that must
 * never break the card layout: width is capped to the parent and the text is
 * truncated with an ellipsis, with the full value revealed on hover.
 */
function TruncatedBadge({
  label,
  variant,
  className,
}: {
  label: string;
  variant: ComponentProps<typeof Badge>["variant"];
  className?: string;
}) {
  return (
    <TruncatedTooltip text={label}>
      <Badge
        variant={variant}
        className={cn("block max-w-full truncate", className)}
      >
        {label}
      </Badge>
    </TruncatedTooltip>
  );
}

export function AgentCard({ agent, models }: AgentCardProps) {
  const { t } = useI18n();
  const router = useRouter();
  const deleteAgent = useDeleteAgent();
  const updateAgent = useUpdateAgent();
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [configOpen, setConfigOpen] = useState(false);

  function handleChat() {
    beginThreadNavigation();
    router.push(`/workspace/agents/${agent.name}/chats/new`);
  }

  async function handleDelete() {
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast.success(t.agents.deleteSuccess);
      setDeleteOpen(false);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleConfigSave(
    model: string,
    reasoningEffort: Agent["reasoning_effort"],
  ) {
    try {
      await updateAgent.mutateAsync({
        name: agent.name,
        request: { model, reasoning_effort: reasoningEffort },
      });
      toast.success(t.agents.configurationSaved);
      setConfigOpen(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <>
      <Card className="group hover:border-foreground/20 flex flex-col transition-colors">
        <CardHeader className="pb-3">
          <div className="flex min-w-0 items-start justify-between gap-2">
            <div className="flex min-w-0 items-center gap-2">
              <div className="bg-primary/10 text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                <BotIcon className="h-5 w-5" />
              </div>
              <div className="min-w-0">
                <TruncatedTooltip text={agent.name}>
                  <CardTitle className="truncate text-base">
                    {agent.name}
                  </CardTitle>
                </TruncatedTooltip>
                {agent.model && (
                  <TruncatedBadge
                    label={agent.model}
                    variant="secondary"
                    className="mt-0.5 text-xs"
                  />
                )}
                {agent.reasoning_effort ? (
                  <TruncatedBadge
                    label={agent.reasoning_effort}
                    variant="outline"
                    className="mt-0.5 text-xs"
                  />
                ) : null}
              </div>
            </div>
          </div>
          {agent.description && (
            <TruncatedTooltip text={agent.description}>
              <CardDescription className="mt-2 line-clamp-2 text-sm">
                {agent.description}
              </CardDescription>
            </TruncatedTooltip>
          )}
        </CardHeader>

        {(agent.tool_groups?.length ?? agent.skills?.length ?? 0) > 0 && (
          <CardContent className="pt-0 pb-3">
            <div className="flex flex-wrap gap-1">
              {agent.tool_groups?.map((group) => (
                <TruncatedBadge
                  key={`tg:${group}`}
                  label={group}
                  variant="outline"
                  className="text-xs"
                />
              ))}
              {agent.skills?.map((skill) => (
                <TruncatedBadge
                  key={`sk:${skill}`}
                  label={skill}
                  variant="secondary"
                  className="text-xs"
                />
              ))}
            </div>
          </CardContent>
        )}

        <CardFooter className="mt-auto flex items-center justify-between gap-2 pt-3">
          <Button size="sm" className="flex-1" onClick={handleChat}>
            <MessageSquareIcon className="mr-1.5 h-3.5 w-3.5" />
            {t.agents.chat}
          </Button>
          <div className="flex gap-1">
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8 shrink-0"
              onClick={() => setConfigOpen(true)}
              disabled={models.length === 0}
              title={t.agents.configure}
              aria-label={t.agents.configure}
            >
              <Settings2Icon className="h-3.5 w-3.5" />
            </Button>
            {!agent.system ? (
              <Button
                size="icon"
                variant="ghost"
                className="text-destructive hover:text-destructive h-8 w-8 shrink-0"
                onClick={() => setDeleteOpen(true)}
                title={t.agents.delete}
                aria-label={t.agents.delete}
              >
                <Trash2Icon className="h-3.5 w-3.5" />
              </Button>
            ) : null}
          </div>
        </CardFooter>
      </Card>

      {/* Delete Confirm */}
      <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.agents.delete}</DialogTitle>
            <DialogDescription>{t.agents.deleteConfirm}</DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteOpen(false)}
              disabled={deleteAgent.isPending}
            >
              {t.common.cancel}
            </Button>
            <Button
              variant="destructive"
              onClick={handleDelete}
              disabled={deleteAgent.isPending}
            >
              {deleteAgent.isPending ? t.common.loading : t.common.delete}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {configOpen ? (
        <ModelConfigDialog
          title={`${t.agents.configure}: ${agent.name}`}
          description={t.agents.agentConfigurationDescription}
          models={models}
          currentModel={agent.model}
          currentReasoningEffort={agent.reasoning_effort}
          saving={updateAgent.isPending}
          onOpenChange={setConfigOpen}
          onSave={handleConfigSave}
        />
      ) : null}
    </>
  );
}
