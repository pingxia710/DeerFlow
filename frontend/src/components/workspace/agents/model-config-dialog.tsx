"use client";

import { useState } from "react";

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
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useI18n } from "@/core/i18n/hooks";
import {
  getModelReasoningEfforts,
  resolveModelReasoningEffort,
} from "@/core/models/reasoning-efforts";
import type { Model } from "@/core/models/types";
import type { ReasoningEffort } from "@/core/threads/types";

interface ModelConfigDialogProps {
  title: string;
  description: string;
  models: Model[];
  currentModel: string | null;
  currentReasoningEffort: ReasoningEffort | null;
  saving: boolean;
  onOpenChange: (open: boolean) => void;
  onSave: (
    model: string,
    reasoningEffort: ReasoningEffort | null,
  ) => Promise<void>;
}

export function ModelConfigDialog({
  title,
  description,
  models,
  currentModel,
  currentReasoningEffort,
  saving,
  onOpenChange,
  onSave,
}: ModelConfigDialogProps) {
  const { t } = useI18n();
  const initialModel =
    models.find((model) => model.name === currentModel) ?? models[0];
  const [modelName, setModelName] = useState(initialModel?.name ?? "");
  const [reasoningEffort, setReasoningEffort] = useState<
    ReasoningEffort | undefined
  >(() =>
    resolveModelReasoningEffort(
      initialModel,
      currentReasoningEffort ?? undefined,
    ),
  );
  const selectedModel = models.find((model) => model.name === modelName);
  const reasoningEfforts = getModelReasoningEfforts(selectedModel);

  function handleModelChange(nextModelName: string) {
    const nextModel = models.find((model) => model.name === nextModelName);
    setModelName(nextModelName);
    setReasoningEffort(resolveModelReasoningEffort(nextModel, undefined));
  }

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-2">
          <label className="grid gap-2 text-sm font-medium">
            {t.agents.model}
            <Select value={modelName} onValueChange={handleModelChange}>
              <SelectTrigger className="w-full">
                <SelectValue placeholder={t.agents.noModels} />
              </SelectTrigger>
              <SelectContent>
                {models.map((model) => (
                  <SelectItem key={model.name} value={model.name}>
                    {model.display_name ?? model.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>

          {reasoningEfforts.length > 0 ? (
            <label className="grid gap-2 text-sm font-medium">
              {t.agents.reasoningEffort}
              <Select
                value={reasoningEffort}
                onValueChange={(value) =>
                  setReasoningEffort(value as ReasoningEffort)
                }
              >
                <SelectTrigger className="w-full">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {reasoningEfforts.map((effort) => (
                    <SelectItem key={effort} value={effort}>
                      {effort}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
          ) : null}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={saving}
          >
            {t.common.cancel}
          </Button>
          <Button
            onClick={() => onSave(modelName, reasoningEffort ?? null)}
            disabled={!modelName || saving}
          >
            {saving ? t.common.loading : t.common.save}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
