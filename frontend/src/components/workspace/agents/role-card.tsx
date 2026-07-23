"use client";

import { Settings2Icon, UserRoundCogIcon } from "lucide-react";
import { useState } from "react";
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
import { useUpdateRole } from "@/core/agents";
import type { Role } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import type { Model } from "@/core/models/types";

import { ModelConfigDialog } from "./model-config-dialog";

interface RoleCardProps {
  role: Role;
  models: Model[];
}

export function RoleCard({ role, models }: RoleCardProps) {
  const { t } = useI18n();
  const updateRole = useUpdateRole();
  const [configOpen, setConfigOpen] = useState(false);
  const localizedRole = t.agents.roleCopy?.[role.name];
  const displayName = localizedRole
    ? `${role.name}（${localizedRole.name}）`
    : role.name;

  async function handleSave(
    model: string,
    reasoningEffort: Role["reasoning_effort"],
  ) {
    try {
      await updateRole.mutateAsync({
        name: role.name,
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
      <Card className="hover:border-foreground/20 flex flex-col transition-colors">
        <CardHeader className="pb-3">
          <div className="flex min-w-0 items-center gap-2">
            <div className="bg-primary/10 text-primary flex size-9 shrink-0 items-center justify-center rounded-lg">
              <UserRoundCogIcon className="size-5" />
            </div>
            <div className="min-w-0">
              <CardTitle className="truncate text-base">
                {displayName}
              </CardTitle>
              <Badge
                variant="outline"
                className="mt-1 max-w-full truncate text-xs"
              >
                {role.skill}
              </Badge>
            </div>
          </div>
          <CardDescription className="mt-2 line-clamp-3 text-sm">
            {localizedRole?.description ?? role.description}
          </CardDescription>
        </CardHeader>

        <CardContent className="flex flex-wrap gap-1 pt-0 pb-3">
          {role.model ? <Badge variant="secondary">{role.model}</Badge> : null}
          {role.reasoning_effort ? (
            <Badge variant="outline">{role.reasoning_effort}</Badge>
          ) : null}
        </CardContent>

        <CardFooter className="mt-auto pt-3">
          <Button
            size="sm"
            variant="outline"
            className="w-full"
            onClick={() => setConfigOpen(true)}
            disabled={models.length === 0}
          >
            <Settings2Icon className="mr-1.5 size-3.5" />
            {t.agents.configure}
          </Button>
        </CardFooter>
      </Card>

      {configOpen ? (
        <ModelConfigDialog
          title={`${t.agents.configure}: ${displayName}`}
          description={t.agents.roleConfigurationDescription}
          models={models}
          currentModel={role.model}
          currentReasoningEffort={role.reasoning_effort}
          saving={updateRole.isPending}
          onOpenChange={setConfigOpen}
          onSave={handleSave}
        />
      ) : null}
    </>
  );
}
