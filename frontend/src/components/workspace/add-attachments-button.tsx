"use client";

import { PaperclipIcon } from "lucide-react";

import {
  PromptInputButton,
  usePromptInputAttachments,
} from "@/components/ai-elements/prompt-input";
import { useI18n } from "@/core/i18n/hooks";
import { cn } from "@/lib/utils";

import { Tooltip } from "./tooltip";

export function AddAttachmentsButton({ className }: { className?: string }) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  return (
    <Tooltip content={t.inputBox.addAttachments}>
      <PromptInputButton
        aria-label={t.inputBox.addAttachments}
        className={cn("px-2!", className)}
        onClick={() => attachments.openFileDialog()}
      >
        <PaperclipIcon className="size-3" />
      </PromptInputButton>
    </Tooltip>
  );
}
