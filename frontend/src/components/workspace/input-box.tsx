"use client";

import type { ChatStatus } from "ai";
import {
  CheckIcon,
  ChevronDownIcon,
  GraduationCapIcon,
  LightbulbIcon,
  PaperclipIcon,
  PlusIcon,
  RocketIcon,
  SparklesIcon,
  XIcon,
  ZapIcon,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ComponentProps,
  type FormEvent,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { toast } from "sonner";

import {
  PromptInput,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInputAttachment,
  PromptInputAttachments,
  PromptInputBody,
  PromptInputButton,
  PromptInputFooter,
  PromptInputSubmit,
  PromptInputTextarea,
  PromptInputTools,
  usePromptInputAttachments,
  usePromptInputController,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { Button } from "@/components/ui/button";
import { ConfettiButton } from "@/components/ui/confetti-button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenuGroup,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";
import { fetch } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";
import { isHiddenFromUIMessage } from "@/core/messages/utils";
import { useModels } from "@/core/models/hooks";
import type { Model } from "@/core/models/types";
import type { Skill } from "@/core/skills";
import { useSkills } from "@/core/skills/hooks";
import { useSuggestionsConfig } from "@/core/suggestions/hooks";
import type {
  AgentThreadContext,
  ReasoningEffort,
  ReasoningSummary,
  TextVerbosity,
} from "@/core/threads";
import { textOfMessage } from "@/core/threads/utils";
import { isIMEComposing } from "@/lib/ime";
import { cn } from "@/lib/utils";

import { Suggestion, Suggestions } from "../ai-elements/suggestion";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "../ui/dropdown-menu";

import {
  getFollowupSuggestionsErrorAction,
  getPromptInputComposerKey,
  shouldApplyPromptInputSubmitContinuation,
} from "./input-box-state";
import { useThread } from "./messages/context";
import { ModeHoverGuide } from "./mode-hover-guide";
import { usePromptInputScope } from "./prompt-input-scope";
import { Tooltip } from "./tooltip";

type InputMode = "flash" | "thinking" | "pro" | "ultra";
type VisibleReasoningEffort = Extract<
  ReasoningEffort,
  "medium" | "high" | "xhigh" | "max" | "ultra"
>;

const MAX_SKILL_SUGGESTIONS = 6;
const SUGGESTION_TEMPLATE_PLACEHOLDER_PATTERN =
  /\[(?:主题|来源|topic|source)\]/i;

function getCompactModelLabel(
  displayName: string | undefined,
  modelName: string | undefined,
) {
  const raw = displayName ?? modelName ?? "";
  const compact = raw
    .replace(/\s*\([^)]*\)\s*$/u, "")
    .replace(/^GPT-/iu, "")
    .trim();

  return compact || raw;
}

function getModelProviderLabel(model: Model, fallbackProviderLabel: string) {
  const provider = model.provider?.trim();
  if (provider) {
    return provider;
  }
  if (/^gpt-5\.\d+/u.test(model.model)) {
    return "Codex CLI";
  }
  return fallbackProviderLabel;
}

function groupModelsByProvider(models: Model[], fallbackProviderLabel: string) {
  const groups = new Map<string, Model[]>();
  for (const model of models) {
    const provider = getModelProviderLabel(model, fallbackProviderLabel);
    const providerModels = groups.get(provider);
    if (providerModels) {
      providerModels.push(model);
    } else {
      groups.set(provider, [model]);
    }
  }
  return Array.from(groups, ([provider, providerModels]) => ({
    provider,
    models: providerModels,
  }));
}

function findSuggestionTemplatePlaceholder(text: string) {
  const match = SUGGESTION_TEMPLATE_PLACEHOLDER_PATTERN.exec(text);
  if (!match) {
    return null;
  }

  return {
    start: match.index,
    end: match.index + match[0].length,
  };
}

function getLeadingSlashSkillQuery(value: string): string | null {
  if (!value.startsWith("/")) {
    return null;
  }

  const query = value.slice(1);
  if (query.includes("/") || /\s/.test(query)) {
    return null;
  }

  return query;
}

function getMatchingSkillSuggestions(skills: Skill[], query: string): Skill[] {
  const normalizedQuery = query.toLowerCase();

  return skills
    .map((skill, index) => ({
      skill,
      index,
      name: skill.name.toLowerCase(),
    }))
    .filter(({ skill, name }) => {
      if (!skill.enabled) {
        return false;
      }
      return !normalizedQuery || name.includes(normalizedQuery);
    })
    .sort((a, b) => {
      const aStartsWith = a.name.startsWith(normalizedQuery);
      const bStartsWith = b.name.startsWith(normalizedQuery);
      if (aStartsWith !== bStartsWith) {
        return aStartsWith ? -1 : 1;
      }
      return a.index - b.index;
    })
    .slice(0, MAX_SKILL_SUGGESTIONS)
    .map(({ skill }) => skill);
}

function getResolvedMode(
  mode: InputMode | undefined,
  supportsThinking: boolean,
): InputMode {
  if (!supportsThinking && mode !== "flash") {
    return "flash";
  }
  if (mode) {
    return mode;
  }
  return supportsThinking ? "ultra" : "flash";
}

export function InputBox({
  className,
  disabled,
  autoFocus,
  status = "ready",
  context,
  extraHeader,
  isWelcomeMode,
  threadId,
  composerSessionId,
  initialValue,
  onContextChange,
  onFollowupsVisibilityChange,
  onSubmit,
  onStop,
  ...props
}: Omit<ComponentProps<typeof PromptInput>, "onSubmit"> & {
  assistantId?: string | null;
  status?: ChatStatus;
  disabled?: boolean;
  context: Omit<
    AgentThreadContext,
    "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
  > & {
    mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
    reasoning_effort?: ReasoningEffort;
    reasoning_summary?: ReasoningSummary;
    text_verbosity?: TextVerbosity;
  };
  extraHeader?: React.ReactNode;
  /**
   * Whether to render the input in welcome layout (vertically centered,
   * with hero + quick action suggestions).  This is purely a visual flag,
   * decoupled from "the backend has created the thread" — see issue #2746.
   */
  isWelcomeMode?: boolean;
  threadId: string;
  composerSessionId?: string | null;
  initialValue?: string;
  onContextChange?: (
    context: Omit<
      AgentThreadContext,
      "thread_id" | "is_plan_mode" | "thinking_enabled" | "subagent_enabled"
    > & {
      mode: "flash" | "thinking" | "pro" | "ultra" | undefined;
      reasoning_effort?: ReasoningEffort;
      reasoning_summary?: ReasoningSummary;
      text_verbosity?: TextVerbosity;
    },
  ) => void;
  onFollowupsVisibilityChange?: (visible: boolean) => void;
  onSubmit?: (message: PromptInputMessage) => void | Promise<void>;
  onStop?: () => void;
}) {
  const { t } = useI18n();
  const searchParams = useSearchParams();
  const { models } = useModels();
  const { thread, isMock } = useThread();
  const { refreshUser } = useAuth();
  const { textInput } = usePromptInputController();
  const { skills } = useSkills();
  const promptRootRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const composerKey = useMemo(
    () => getPromptInputComposerKey({ threadId, composerSessionId }),
    [composerSessionId, threadId],
  );
  const composerKeyRef = useRef(composerKey);
  composerKeyRef.current = composerKey;
  usePromptInputScope(composerKey);
  const promptHistoryIndexRef = useRef<number | null>(null);
  const promptHistoryDraftRef = useRef("");

  const [followups, setFollowups] = useState<string[]>([]);
  const { data: suggestionsConfig } = useSuggestionsConfig();
  const suggestionsConfigLoaded = suggestionsConfig !== undefined;
  const suggestionsEnabled = suggestionsConfig?.enabled;
  const [followupsHidden, setFollowupsHidden] = useState(false);
  const [followupsLoading, setFollowupsLoading] = useState(false);
  const [textareaFocused, setTextareaFocused] = useState(false);
  const [skillSuggestionIndex, setSkillSuggestionIndex] = useState(0);
  const [dismissedSkillSuggestionValue, setDismissedSkillSuggestionValue] =
    useState<string | null>(null);
  const lastGeneratedForAiIdRef = useRef<string | null>(null);
  const wasStreamingRef = useRef(false);
  const messagesRef = useRef(thread.messages);

  const [confirmOpen, setConfirmOpen] = useState(false);
  const [pendingSuggestion, setPendingSuggestion] = useState<string | null>(
    null,
  );

  useEffect(() => {
    if (models.length === 0) {
      return;
    }
    const currentModel = models.find((m) => m.name === context.model_name);
    const fallbackModel = currentModel ?? models[0]!;
    const supportsThinking = fallbackModel.supports_thinking ?? false;
    const nextModelName = fallbackModel.name;
    const nextMode = getResolvedMode(context.mode, supportsThinking);

    if (context.model_name === nextModelName && context.mode === nextMode) {
      return;
    }

    onContextChange?.({
      ...context,
      model_name: nextModelName,
      mode: nextMode,
    });
  }, [context, models, onContextChange]);

  const selectedModel = useMemo(() => {
    if (models.length === 0) {
      return undefined;
    }
    return models.find((m) => m.name === context.model_name) ?? models[0];
  }, [context.model_name, models]);

  const resolvedModelName = selectedModel?.name;

  const supportThinking = useMemo(
    () => selectedModel?.supports_thinking ?? false,
    [selectedModel],
  );

  const supportReasoningEffort = useMemo(
    () => selectedModel?.supports_reasoning_effort ?? false,
    [selectedModel],
  );
  const visibleReasoningEffort: VisibleReasoningEffort =
    context.reasoning_effort === "medium" ||
    context.reasoning_effort === "high" ||
    context.reasoning_effort === "xhigh" ||
    context.reasoning_effort === "max" ||
    context.reasoning_effort === "ultra"
      ? context.reasoning_effort
      : "xhigh";
  const compactModelLabel = getCompactModelLabel(
    selectedModel?.display_name,
    selectedModel?.model ?? selectedModel?.name,
  );
  const modelProviderGroups = useMemo(
    () => groupModelsByProvider(models, t.inputBox.model),
    [models, t.inputBox.model],
  );

  const promptHistory = useMemo(() => {
    const history: string[] = [];
    for (const message of thread.messages) {
      if (message.type !== "human") {
        continue;
      }
      const additionalKwargs = message.additional_kwargs;
      if (
        additionalKwargs &&
        typeof additionalKwargs === "object" &&
        Reflect.get(additionalKwargs, "hide_from_ui") === true
      ) {
        continue;
      }
      const text = textOfMessage(message)?.trim();
      if (!text) {
        continue;
      }
      if (history.at(-1) !== text) {
        history.push(text);
      }
    }
    return history;
  }, [thread.messages]);

  useEffect(() => {
    promptHistoryIndexRef.current = null;
    promptHistoryDraftRef.current = "";
    setFollowups([]);
    setFollowupsHidden(false);
    setFollowupsLoading(false);
    setPendingSuggestion(null);
  }, [composerKey]);

  useEffect(() => {
    const currentIndex = promptHistoryIndexRef.current;
    if (currentIndex !== null && currentIndex >= promptHistory.length) {
      promptHistoryIndexRef.current = null;
      promptHistoryDraftRef.current = "";
    }
  }, [promptHistory.length]);

  const handleModelSelect = useCallback(
    (model_name: string) => {
      const model = models.find((m) => m.name === model_name);
      if (!model) {
        return;
      }
      onContextChange?.({
        ...context,
        model_name,
        mode: getResolvedMode(context.mode, model.supports_thinking ?? false),
        reasoning_effort: context.reasoning_effort,
      });
    },
    [onContextChange, context, models],
  );

  const handleModeSelect = useCallback(
    (mode: InputMode) => {
      onContextChange?.({
        ...context,
        mode: getResolvedMode(mode, supportThinking),
        reasoning_effort:
          mode === "pro" || mode === "thinking" || mode === "ultra"
            ? "xhigh"
            : undefined,
      });
    },
    [onContextChange, context, supportThinking],
  );

  const handleReasoningEffortSelect = useCallback(
    (effort: ReasoningEffort) => {
      onContextChange?.({
        ...context,
        reasoning_effort: effort,
      });
    },
    [onContextChange, context],
  );

  const handleReasoningSummarySelect = useCallback(
    (reasoning_summary: ReasoningSummary) => {
      onContextChange?.({
        ...context,
        reasoning_summary,
      });
    },
    [onContextChange, context],
  );

  const handleTextVerbositySelect = useCallback(
    (text_verbosity: TextVerbosity) => {
      onContextChange?.({
        ...context,
        text_verbosity,
      });
    },
    [onContextChange, context],
  );

  const reasoningSummaryValue = context.reasoning_summary ?? "detailed";
  const textVerbosityValue = context.text_verbosity ?? "medium";
  const reasoningEffortOptions: {
    value: VisibleReasoningEffort;
    label: string;
    compactLabel: string;
    description: string;
  }[] = [
    {
      value: "medium",
      label: t.inputBox.reasoningEffortMedium,
      compactLabel: t.inputBox.reasoningEffortMediumShort,
      description: t.inputBox.reasoningEffortMediumDescription,
    },
    {
      value: "high",
      label: t.inputBox.reasoningEffortHigh,
      compactLabel: t.inputBox.reasoningEffortHighShort,
      description: t.inputBox.reasoningEffortHighDescription,
    },
    {
      value: "xhigh",
      label: t.inputBox.reasoningEffortPro,
      compactLabel: t.inputBox.reasoningEffortProShort,
      description: t.inputBox.reasoningEffortProDescription,
    },
    {
      value: "max",
      label: t.inputBox.reasoningEffortMax,
      compactLabel: t.inputBox.reasoningEffortMaxShort,
      description: t.inputBox.reasoningEffortMaxDescription,
    },
    {
      value: "ultra",
      label: t.inputBox.reasoningEffortUltra,
      compactLabel: t.inputBox.reasoningEffortUltraShort,
      description: t.inputBox.reasoningEffortUltraDescription,
    },
  ];
  const compactReasoningEffortLabel =
    reasoningEffortOptions.find(
      (option) => option.value === visibleReasoningEffort,
    )?.compactLabel ?? t.inputBox.reasoningEffortProShort;
  const reasoningSummaryOptions: {
    value: ReasoningSummary;
    label: string;
    description: string;
  }[] = [
    {
      value: "auto",
      label: t.inputBox.reasoningSummaryAuto,
      description: t.inputBox.reasoningSummaryAutoDescription,
    },
    {
      value: "concise",
      label: t.inputBox.reasoningSummaryConcise,
      description: t.inputBox.reasoningSummaryConciseDescription,
    },
    {
      value: "detailed",
      label: t.inputBox.reasoningSummaryDetailed,
      description: t.inputBox.reasoningSummaryDetailedDescription,
    },
  ];
  const textVerbosityOptions: {
    value: TextVerbosity;
    label: string;
    description: string;
  }[] = [
    {
      value: "low",
      label: t.inputBox.textVerbosityLow,
      description: t.inputBox.textVerbosityLowDescription,
    },
    {
      value: "medium",
      label: t.inputBox.textVerbosityMedium,
      description: t.inputBox.textVerbosityMediumDescription,
    },
    {
      value: "high",
      label: t.inputBox.textVerbosityHigh,
      description: t.inputBox.textVerbosityHighDescription,
    },
  ];
  const reasoningSummaryLabel =
    reasoningSummaryOptions.find(
      (option) => option.value === reasoningSummaryValue,
    )?.label ?? t.inputBox.reasoningSummaryDetailed;
  const textVerbosityLabel =
    textVerbosityOptions.find((option) => option.value === textVerbosityValue)
      ?.label ?? t.inputBox.textVerbosityMedium;

  const handleSubmit = useCallback(
    (message: PromptInputMessage, event: FormEvent<HTMLFormElement>) => {
      if (status === "streaming") {
        const submitter = (event.nativeEvent as SubmitEvent).submitter;
        const explicitStopClick = submitter instanceof HTMLButtonElement;
        const hasDraft =
          Boolean(message.text.trim()) || message.files.length > 0;
        if (explicitStopClick) {
          onStop?.();
          return;
        }
        if (!hasDraft) {
          return;
        }
      }
      if (!message.text.trim() && message.files.length === 0) {
        return;
      }
      const placeholder = findSuggestionTemplatePlaceholder(message.text);
      if (placeholder) {
        toast.error(t.inputBox.suggestionPlaceholderRequired);
        requestAnimationFrame(() => {
          const textarea = textareaRef.current;
          if (!textarea) {
            return;
          }
          textarea.focus();
          textarea.setSelectionRange(placeholder.start, placeholder.end);
        });
        return Promise.reject(
          new Error("Suggestion template placeholder is unresolved."),
        );
      }
      promptHistoryIndexRef.current = null;
      promptHistoryDraftRef.current = "";
      setFollowups([]);
      setFollowupsHidden(false);
      setFollowupsLoading(false);
      const submitComposerKey = composerKey;
      const submitIfCurrent = () =>
        shouldApplyPromptInputSubmitContinuation(
          composerKeyRef.current,
          submitComposerKey,
        )
          ? onSubmit?.(message)
          : undefined;

      // Guard against submitting before the initial model auto-selection
      // effect has flushed thread settings to storage/state.
      if (resolvedModelName && context.model_name !== resolvedModelName) {
        onContextChange?.({
          ...context,
          model_name: resolvedModelName,
          mode: getResolvedMode(
            context.mode,
            selectedModel?.supports_thinking ?? false,
          ),
        });
        return new Promise<void>((resolve, reject) => {
          setTimeout(() => {
            Promise.resolve(submitIfCurrent()).then(resolve).catch(reject);
          }, 0);
        });
      }

      return submitIfCurrent();
    },
    [
      composerKey,
      context,
      onContextChange,
      onSubmit,
      onStop,
      resolvedModelName,
      selectedModel?.supports_thinking,
      status,
      t.inputBox.suggestionPlaceholderRequired,
    ],
  );

  const requestFormSubmit = useCallback(() => {
    const form = promptRootRef.current?.querySelector("form");
    form?.requestSubmit();
  }, []);

  const handleFollowupClick = useCallback(
    (suggestion: string) => {
      if (status === "streaming") {
        return;
      }
      const current = (textInput.value ?? "").trim();
      if (current) {
        setPendingSuggestion(suggestion);
        setConfirmOpen(true);
        return;
      }
      textInput.setInput(suggestion);
      setFollowupsHidden(true);
      setTimeout(() => requestFormSubmit(), 0);
    },
    [requestFormSubmit, status, textInput],
  );

  const confirmReplaceAndSend = useCallback(() => {
    if (!pendingSuggestion) {
      setConfirmOpen(false);
      return;
    }
    textInput.setInput(pendingSuggestion);
    setFollowupsHidden(true);
    setConfirmOpen(false);
    setPendingSuggestion(null);
    setTimeout(() => requestFormSubmit(), 0);
  }, [pendingSuggestion, requestFormSubmit, textInput]);

  const confirmAppendAndSend = useCallback(() => {
    if (!pendingSuggestion) {
      setConfirmOpen(false);
      return;
    }
    const current = (textInput.value ?? "").trim();
    const next = current
      ? `${current}\n${pendingSuggestion}`
      : pendingSuggestion;
    textInput.setInput(next);
    setFollowupsHidden(true);
    setConfirmOpen(false);
    setPendingSuggestion(null);
    setTimeout(() => requestFormSubmit(), 0);
  }, [pendingSuggestion, requestFormSubmit, textInput]);

  const slashSkillQuery = useMemo(
    () => getLeadingSlashSkillQuery(textInput.value ?? ""),
    [textInput.value],
  );
  const skillSuggestions = useMemo(
    () =>
      slashSkillQuery === null
        ? []
        : getMatchingSkillSuggestions(skills, slashSkillQuery),
    [skills, slashSkillQuery],
  );
  const showSkillSuggestions =
    !disabled &&
    textareaFocused &&
    slashSkillQuery !== null &&
    skillSuggestions.length > 0 &&
    dismissedSkillSuggestionValue !== textInput.value;

  useEffect(() => {
    setSkillSuggestionIndex(0);
  }, [slashSkillQuery, skillSuggestions.length]);

  const applySkillSuggestion = useCallback(
    (skill: Skill) => {
      const nextValue = `/${skill.name} `;
      textInput.setInput(nextValue);
      setDismissedSkillSuggestionValue(nextValue);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) {
          return;
        }
        textarea.focus();
        textarea.setSelectionRange(nextValue.length, nextValue.length);
      });
    },
    [textInput],
  );

  const handleSkillSuggestionKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (!showSkillSuggestions) {
        return;
      }

      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSkillSuggestionIndex(
          (index) => (index + 1) % skillSuggestions.length,
        );
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSkillSuggestionIndex(
          (index) =>
            (index - 1 + skillSuggestions.length) % skillSuggestions.length,
        );
        return;
      }

      if (event.key === "Enter" || event.key === "Tab") {
        if (event.shiftKey) {
          return;
        }
        event.preventDefault();
        const selectedSkill = skillSuggestions[skillSuggestionIndex];
        if (selectedSkill) {
          applySkillSuggestion(selectedSkill);
        }
        return;
      }

      if (event.key === "Escape") {
        event.preventDefault();
        setDismissedSkillSuggestionValue(textInput.value);
      }
    },
    [
      applySkillSuggestion,
      showSkillSuggestions,
      skillSuggestionIndex,
      skillSuggestions,
      textInput.value,
    ],
  );

  const setPromptHistoryValue = useCallback(
    (value: string) => {
      textInput.setInput(value);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        if (!textarea) {
          return;
        }
        textarea.focus();
        textarea.setSelectionRange(value.length, value.length);
      });
    },
    [textInput],
  );

  const handlePromptHistoryKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      if (
        event.altKey ||
        event.ctrlKey ||
        event.metaKey ||
        event.shiftKey ||
        isIMEComposing(event) ||
        promptHistory.length === 0 ||
        (event.key !== "ArrowUp" && event.key !== "ArrowDown")
      ) {
        return;
      }

      const currentValue = textInput.value ?? "";
      const currentHistoryIndex = promptHistoryIndexRef.current;
      const isBrowsingHistory = currentHistoryIndex !== null;

      if (!isBrowsingHistory && currentValue.length > 0) {
        return;
      }

      if (event.key === "ArrowUp") {
        event.preventDefault();
        const nextIndex = isBrowsingHistory
          ? Math.max(currentHistoryIndex - 1, 0)
          : promptHistory.length - 1;
        if (!isBrowsingHistory) {
          promptHistoryDraftRef.current = currentValue;
        }
        promptHistoryIndexRef.current = nextIndex;
        setPromptHistoryValue(promptHistory[nextIndex] ?? "");
        return;
      }

      if (!isBrowsingHistory) {
        return;
      }

      event.preventDefault();
      if (currentHistoryIndex >= promptHistory.length - 1) {
        promptHistoryIndexRef.current = null;
        setPromptHistoryValue(promptHistoryDraftRef.current);
        promptHistoryDraftRef.current = "";
        return;
      }

      const nextIndex = currentHistoryIndex + 1;
      promptHistoryIndexRef.current = nextIndex;
      setPromptHistoryValue(promptHistory[nextIndex] ?? "");
    },
    [promptHistory, setPromptHistoryValue, textInput.value],
  );

  const handlePromptTextareaKeyDown = useCallback(
    (event: KeyboardEvent<HTMLTextAreaElement>) => {
      handleSkillSuggestionKeyDown(event);
      if (event.defaultPrevented) {
        return;
      }
      handlePromptHistoryKeyDown(event);
    },
    [handlePromptHistoryKeyDown, handleSkillSuggestionKeyDown],
  );

  const handlePromptTextareaChange = useCallback(() => {
    promptHistoryIndexRef.current = null;
    promptHistoryDraftRef.current = "";
  }, []);

  const showFollowups =
    !disabled &&
    !isWelcomeMode &&
    !showSkillSuggestions &&
    !followupsHidden &&
    (followupsLoading || followups.length > 0);

  useEffect(() => {
    onFollowupsVisibilityChange?.(showFollowups);
  }, [onFollowupsVisibilityChange, showFollowups]);

  useEffect(() => {
    return () => onFollowupsVisibilityChange?.(false);
  }, [onFollowupsVisibilityChange]);

  useEffect(() => {
    messagesRef.current = thread.messages;
  }, [thread.messages]);

  useEffect(() => {
    const streaming = status === "streaming";
    const wasStreaming = wasStreamingRef.current;
    wasStreamingRef.current = streaming;
    if (!wasStreaming || streaming) {
      return;
    }

    if (disabled || isMock) {
      return;
    }

    const lastAi = [...messagesRef.current]
      .reverse()
      .find((m) => m.type === "ai");
    const lastAiId = lastAi?.id ?? null;
    if (!lastAiId || lastAiId === lastGeneratedForAiIdRef.current) {
      return;
    }
    if (!suggestionsConfigLoaded) {
      return;
    }
    lastGeneratedForAiIdRef.current = lastAiId;

    const recent = messagesRef.current
      .filter((m) => m.type === "human" || m.type === "ai")
      .filter((m) => !isHiddenFromUIMessage(m))
      .map((m) => {
        const role = m.type === "human" ? "user" : "assistant";
        const content = textOfMessage(m) ?? "";
        return { role, content };
      })
      .filter((m) => m.content.trim().length > 0)
      .slice(-6);

    if (recent.length === 0) {
      return;
    }

    if (!suggestionsEnabled) {
      setFollowups([]);
      return;
    }

    const controller = new AbortController();
    setFollowupsHidden(false);
    setFollowupsLoading(true);
    setFollowups([]);

    fetch(
      `${getBackendBaseURL()}/api/threads/${encodeURIComponent(
        threadId,
      )}/suggestions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: recent,
          n: 3,
          model_name: context.model_name ?? undefined,
        }),
        signal: controller.signal,
      },
    )
      .then(async (res) => {
        if (!res.ok) {
          return { suggestions: [] as string[] };
        }
        return (await res.json()) as { suggestions?: string[] };
      })
      .then((data) => {
        const suggestions = (data.suggestions ?? [])
          .map((s) => (typeof s === "string" ? s.trim() : ""))
          .filter((s) => s.length > 0)
          .slice(0, 5);
        setFollowups(suggestions);
      })
      .catch((error: unknown) => {
        if (getFollowupSuggestionsErrorAction(error) === "refresh-auth") {
          void refreshUser();
          return;
        }
        setFollowups([]);
      })
      .finally(() => {
        setFollowupsLoading(false);
      });

    return () => controller.abort();
  }, [
    context.model_name,
    disabled,
    isMock,
    status,
    suggestionsConfigLoaded,
    suggestionsEnabled,
    refreshUser,
    threadId,
  ]);

  return (
    <div
      ref={promptRootRef}
      className={cn(
        "relative flex min-w-0 flex-col",
        isWelcomeMode ? "gap-4" : "gap-2",
      )}
    >
      {showFollowups && (
        <div className="flex items-center justify-center pb-1">
          <div className="flex items-center gap-2">
            {followupsLoading ? (
              <div className="text-muted-foreground bg-background/80 rounded-full border px-4 py-1.5 text-xs backdrop-blur-sm">
                {t.inputBox.followupLoading}
              </div>
            ) : (
              <Suggestions className="w-fit items-center">
                {followups.map((s) => (
                  <Suggestion
                    key={s}
                    className="py-1.5"
                    suggestion={s}
                    onClick={() => handleFollowupClick(s)}
                  />
                ))}
                <Button
                  aria-label={t.common.close}
                  className="text-muted-foreground h-auto cursor-pointer rounded-full px-2.5 py-1.5 text-xs font-normal"
                  variant="outline"
                  size="sm"
                  type="button"
                  onClick={() => setFollowupsHidden(true)}
                >
                  <XIcon className="size-4" />
                </Button>
              </Suggestions>
            )}
          </div>
        </div>
      )}
      {showSkillSuggestions && (
        <div className="absolute right-0 bottom-full left-0 z-40 mb-2 px-1">
          <div
            aria-label="Skill suggestions"
            className="bg-popover/95 text-popover-foreground border-border max-h-72 overflow-y-auto rounded-xl border p-1 shadow-lg backdrop-blur-sm"
            role="listbox"
          >
            {skillSuggestions.map((skill, index) => {
              const selected = index === skillSuggestionIndex;
              return (
                <button
                  aria-selected={selected}
                  className={cn(
                    "flex min-h-12 w-full min-w-0 cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-left transition-colors",
                    selected
                      ? "bg-accent text-accent-foreground"
                      : "text-popover-foreground hover:bg-accent/70 hover:text-accent-foreground",
                  )}
                  key={skill.name}
                  onClick={() => applySkillSuggestion(skill)}
                  onMouseDown={(event) => event.preventDefault()}
                  onMouseEnter={() => setSkillSuggestionIndex(index)}
                  role="option"
                  type="button"
                >
                  <SparklesIcon className="text-muted-foreground size-4 shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">
                      /{skill.name}
                    </span>
                    {skill.description && (
                      <span className="text-muted-foreground block truncate text-xs">
                        {skill.description}
                      </span>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      )}
      <PromptInput
        className={cn(
          "bg-background/85 rounded-2xl backdrop-blur-sm transition-all duration-300 ease-out *:data-[slot='input-group']:rounded-2xl",
          className,
        )}
        disabled={disabled}
        globalDrop
        multiple
        onSubmit={handleSubmit}
        {...props}
      >
        {extraHeader && (
          <div className="absolute top-0 right-0 left-0 z-10">
            <div className="absolute right-0 bottom-0 left-0 flex items-center justify-center">
              {extraHeader}
            </div>
          </div>
        )}
        <PromptInputAttachments>
          {(attachment) => <PromptInputAttachment data={attachment} />}
        </PromptInputAttachments>
        <PromptInputBody className="absolute top-0 right-0 left-0 z-3">
          <PromptInputTextarea
            className={cn("size-full")}
            disabled={disabled}
            placeholder={t.inputBox.placeholder}
            autoFocus={autoFocus}
            defaultValue={initialValue}
            onBlur={() => setTextareaFocused(false)}
            onChange={handlePromptTextareaChange}
            onFocus={() => setTextareaFocused(true)}
            onKeyDown={handlePromptTextareaKeyDown}
            ref={textareaRef}
          />
        </PromptInputBody>
        <PromptInputFooter className="flex flex-wrap gap-2 sm:flex-nowrap">
          <PromptInputTools className="min-w-0 flex-1 flex-wrap">
            {/* TODO: Add more connectors here
          <PromptInputActionMenu>
            <PromptInputActionMenuTrigger className="px-2!" />
            <PromptInputActionMenuContent>
              <PromptInputActionAddAttachments
                label={t.inputBox.addAttachments}
              />
            </PromptInputActionMenuContent>
          </PromptInputActionMenu> */}
            <AddAttachmentsButton className="px-2!" />
            <PromptInputActionMenu>
              <ModeHoverGuide
                mode={
                  context.mode === "flash" ||
                  context.mode === "thinking" ||
                  context.mode === "pro" ||
                  context.mode === "ultra"
                    ? context.mode
                    : "flash"
                }
              >
                <PromptInputActionMenuTrigger className="max-w-28 gap-1! px-2! sm:max-w-none">
                  <div>
                    {context.mode === "flash" && <ZapIcon className="size-3" />}
                    {context.mode === "thinking" && (
                      <LightbulbIcon className="size-3" />
                    )}
                    {context.mode === "pro" && (
                      <GraduationCapIcon className="size-3" />
                    )}
                    {context.mode === "ultra" && (
                      <RocketIcon className="size-3 text-[#dabb5e]" />
                    )}
                  </div>
                  <div
                    className={cn(
                      "truncate text-xs font-normal",
                      context.mode === "ultra" ? "golden-text" : "",
                    )}
                  >
                    {(context.mode === "flash" && t.inputBox.flashMode) ||
                      (context.mode === "thinking" &&
                        t.inputBox.reasoningMode) ||
                      (context.mode === "pro" && t.inputBox.proMode) ||
                      (context.mode === "ultra" && t.inputBox.ultraMode)}
                  </div>
                </PromptInputActionMenuTrigger>
              </ModeHoverGuide>
              <PromptInputActionMenuContent className="w-80">
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="text-muted-foreground text-xs">
                    {t.inputBox.mode}
                  </DropdownMenuLabel>
                  <PromptInputActionMenu>
                    <PromptInputActionMenuItem
                      className={cn(
                        context.mode === "flash"
                          ? "text-accent-foreground"
                          : "text-muted-foreground/65",
                      )}
                      onSelect={() => handleModeSelect("flash")}
                    >
                      <div className="flex flex-col gap-2">
                        <div className="flex items-center gap-1 font-bold">
                          <ZapIcon
                            className={cn(
                              "mr-2 size-4",
                              context.mode === "flash" &&
                                "text-accent-foreground",
                            )}
                          />
                          {t.inputBox.flashMode}
                        </div>
                        <div className="pl-7 text-xs">
                          {t.inputBox.flashModeDescription}
                        </div>
                      </div>
                      {context.mode === "flash" ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </PromptInputActionMenuItem>
                    {supportThinking && (
                      <PromptInputActionMenuItem
                        className={cn(
                          context.mode === "thinking"
                            ? "text-accent-foreground"
                            : "text-muted-foreground/65",
                        )}
                        onSelect={() => handleModeSelect("thinking")}
                      >
                        <div className="flex flex-col gap-2">
                          <div className="flex items-center gap-1 font-bold">
                            <LightbulbIcon
                              className={cn(
                                "mr-2 size-4",
                                context.mode === "thinking" &&
                                  "text-accent-foreground",
                              )}
                            />
                            {t.inputBox.reasoningMode}
                          </div>
                          <div className="pl-7 text-xs">
                            {t.inputBox.reasoningModeDescription}
                          </div>
                        </div>
                        {context.mode === "thinking" ? (
                          <CheckIcon className="ml-auto size-4" />
                        ) : (
                          <div className="ml-auto size-4" />
                        )}
                      </PromptInputActionMenuItem>
                    )}
                    <PromptInputActionMenuItem
                      className={cn(
                        context.mode === "pro"
                          ? "text-accent-foreground"
                          : "text-muted-foreground/65",
                      )}
                      onSelect={() => handleModeSelect("pro")}
                    >
                      <div className="flex flex-col gap-2">
                        <div className="flex items-center gap-1 font-bold">
                          <GraduationCapIcon
                            className={cn(
                              "mr-2 size-4",
                              context.mode === "pro" &&
                                "text-accent-foreground",
                            )}
                          />
                          {t.inputBox.proMode}
                        </div>
                        <div className="pl-7 text-xs">
                          {t.inputBox.proModeDescription}
                        </div>
                      </div>
                      {context.mode === "pro" ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </PromptInputActionMenuItem>
                    <PromptInputActionMenuItem
                      className={cn(
                        context.mode === "ultra"
                          ? "text-accent-foreground"
                          : "text-muted-foreground/65",
                      )}
                      onSelect={() => handleModeSelect("ultra")}
                    >
                      <div className="flex flex-col gap-2">
                        <div className="flex items-center gap-1 font-bold">
                          <RocketIcon
                            className={cn(
                              "mr-2 size-4",
                              context.mode === "ultra" && "text-[#dabb5e]",
                            )}
                          />
                          <div
                            className={cn(
                              context.mode === "ultra" && "golden-text",
                            )}
                          >
                            {t.inputBox.ultraMode}
                          </div>
                        </div>
                        <div className="pl-7 text-xs">
                          {t.inputBox.ultraModeDescription}
                        </div>
                      </div>
                      {context.mode === "ultra" ? (
                        <CheckIcon className="ml-auto size-4" />
                      ) : (
                        <div className="ml-auto size-4" />
                      )}
                    </PromptInputActionMenuItem>
                  </PromptInputActionMenu>
                </DropdownMenuGroup>
              </PromptInputActionMenuContent>
            </PromptInputActionMenu>
          </PromptInputTools>
          <PromptInputTools className="min-w-0 justify-end">
            <PromptInputActionMenu>
              <PromptInputActionMenuTrigger className="max-w-44 min-w-0 gap-1! px-2! sm:max-w-56">
                <ZapIcon className="size-3.5 shrink-0" />
                <span className="truncate text-xs font-normal">
                  {compactModelLabel}
                </span>
                {supportReasoningEffort && (
                  <span className="text-muted-foreground shrink-0 text-xs font-semibold">
                    {compactReasoningEffortLabel}
                  </span>
                )}
                <ChevronDownIcon className="text-muted-foreground size-3 shrink-0" />
              </PromptInputActionMenuTrigger>
              <PromptInputActionMenuContent align="end" className="w-80">
                {supportReasoningEffort && (
                  <>
                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger className="text-accent-foreground">
                        <LightbulbIcon className="size-4 shrink-0" />
                        <div className="flex min-w-0 flex-1 flex-col gap-1">
                          <div className="truncate font-bold">
                            {t.inputBox.reasoningEffort}
                          </div>
                          <div className="text-muted-foreground truncate text-xs">
                            {compactReasoningEffortLabel}
                          </div>
                        </div>
                      </DropdownMenuSubTrigger>
                      <DropdownMenuSubContent className="min-w-72">
                        {reasoningEffortOptions.map((option) => (
                          <PromptInputActionMenuItem
                            key={option.value}
                            className={cn(
                              visibleReasoningEffort === option.value
                                ? "text-accent-foreground"
                                : "text-muted-foreground/65",
                            )}
                            onSelect={() =>
                              handleReasoningEffortSelect(option.value)
                            }
                          >
                            <div className="flex min-w-0 flex-1 flex-col gap-1">
                              <div className="font-bold">{option.label}</div>
                              <div className="text-muted-foreground text-xs">
                                {option.description}
                              </div>
                            </div>
                            {visibleReasoningEffort === option.value ? (
                              <CheckIcon className="ml-auto size-4" />
                            ) : (
                              <div className="ml-auto size-4" />
                            )}
                          </PromptInputActionMenuItem>
                        ))}
                      </DropdownMenuSubContent>
                    </DropdownMenuSub>
                    <DropdownMenuSeparator />
                  </>
                )}
                <DropdownMenuGroup>
                  <DropdownMenuLabel className="text-muted-foreground text-xs">
                    {t.inputBox.model}
                  </DropdownMenuLabel>
                  {modelProviderGroups.map((group) => {
                    const hasSelectedModel = group.models.some(
                      (m) => m.name === context.model_name,
                    );
                    return (
                      <DropdownMenuSub key={group.provider}>
                        <DropdownMenuSubTrigger
                          className={cn(
                            hasSelectedModel
                              ? "text-accent-foreground"
                              : "text-muted-foreground/65",
                          )}
                        >
                          <ZapIcon className="size-4 shrink-0" />
                          <div className="flex min-w-0 flex-1 flex-col gap-1">
                            <div className="truncate font-bold">
                              {group.provider}
                            </div>
                          </div>
                        </DropdownMenuSubTrigger>
                        <DropdownMenuSubContent className="max-h-80 min-w-72 overflow-y-auto">
                          {group.models.map((m) => (
                            <PromptInputActionMenuItem
                              key={m.name}
                              className={cn(
                                m.name === context.model_name
                                  ? "text-accent-foreground"
                                  : "text-muted-foreground/65",
                              )}
                              onSelect={() => handleModelSelect(m.name)}
                            >
                              <ZapIcon className="size-4 shrink-0" />
                              <div className="flex min-w-0 flex-1 flex-col gap-1">
                                <div className="truncate font-bold">
                                  {getCompactModelLabel(
                                    m.display_name,
                                    m.model ?? m.name,
                                  )}
                                </div>
                                <div className="text-muted-foreground truncate text-xs">
                                  {m.model}
                                </div>
                              </div>
                              {m.name === context.model_name ? (
                                <CheckIcon className="ml-auto size-4" />
                              ) : (
                                <div className="ml-auto size-4" />
                              )}
                            </PromptInputActionMenuItem>
                          ))}
                        </DropdownMenuSubContent>
                      </DropdownMenuSub>
                    );
                  })}
                </DropdownMenuGroup>
                {supportReasoningEffort && (
                  <>
                    <DropdownMenuSeparator />
                    <DropdownMenuGroup>
                      <DropdownMenuSub>
                        <DropdownMenuSubTrigger className="text-accent-foreground">
                          <LightbulbIcon className="size-4 shrink-0" />
                          <div className="flex min-w-0 flex-1 flex-col gap-1">
                            <div className="truncate font-bold">
                              {t.inputBox.reasoningSummary}
                            </div>
                            <div className="text-muted-foreground truncate text-xs">
                              {reasoningSummaryLabel}
                            </div>
                          </div>
                        </DropdownMenuSubTrigger>
                        <DropdownMenuSubContent className="min-w-72">
                          {reasoningSummaryOptions.map((option) => (
                            <PromptInputActionMenuItem
                              key={option.value}
                              className={cn(
                                reasoningSummaryValue === option.value
                                  ? "text-accent-foreground"
                                  : "text-muted-foreground/65",
                              )}
                              onSelect={() =>
                                handleReasoningSummarySelect(option.value)
                              }
                            >
                              <div className="flex min-w-0 flex-1 flex-col gap-1">
                                <div className="font-bold">{option.label}</div>
                                <div className="text-muted-foreground text-xs">
                                  {option.description}
                                </div>
                              </div>
                              {reasoningSummaryValue === option.value ? (
                                <CheckIcon className="ml-auto size-4" />
                              ) : (
                                <div className="ml-auto size-4" />
                              )}
                            </PromptInputActionMenuItem>
                          ))}
                        </DropdownMenuSubContent>
                      </DropdownMenuSub>
                      <DropdownMenuSub>
                        <DropdownMenuSubTrigger className="text-accent-foreground">
                          <SparklesIcon className="size-4 shrink-0" />
                          <div className="flex min-w-0 flex-1 flex-col gap-1">
                            <div className="truncate font-bold">
                              {t.inputBox.textVerbosity}
                            </div>
                            <div className="text-muted-foreground truncate text-xs">
                              {textVerbosityLabel}
                            </div>
                          </div>
                        </DropdownMenuSubTrigger>
                        <DropdownMenuSubContent className="min-w-72">
                          {textVerbosityOptions.map((option) => (
                            <PromptInputActionMenuItem
                              key={option.value}
                              className={cn(
                                textVerbosityValue === option.value
                                  ? "text-accent-foreground"
                                  : "text-muted-foreground/65",
                              )}
                              onSelect={() =>
                                handleTextVerbositySelect(option.value)
                              }
                            >
                              <div className="flex min-w-0 flex-1 flex-col gap-1">
                                <div className="font-bold">{option.label}</div>
                                <div className="text-muted-foreground text-xs">
                                  {option.description}
                                </div>
                              </div>
                              {textVerbosityValue === option.value ? (
                                <CheckIcon className="ml-auto size-4" />
                              ) : (
                                <div className="ml-auto size-4" />
                              )}
                            </PromptInputActionMenuItem>
                          ))}
                        </DropdownMenuSubContent>
                      </DropdownMenuSub>
                    </DropdownMenuGroup>
                  </>
                )}
              </PromptInputActionMenuContent>
            </PromptInputActionMenu>
            <PromptInputSubmit
              className="rounded-full"
              disabled={disabled}
              variant="outline"
              status={status}
            />
          </PromptInputTools>
        </PromptInputFooter>
        {!isWelcomeMode && (
          <div className="bg-background absolute right-0 -bottom-[17px] left-0 z-0 h-4"></div>
        )}
      </PromptInput>

      {isWelcomeMode &&
        searchParams.get("mode") !== "skill" &&
        !showSkillSuggestions && (
          <div className="flex items-center justify-center pt-2">
            <SuggestionList textareaRef={textareaRef} />
          </div>
        )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t.inputBox.followupConfirmTitle}</DialogTitle>
            <DialogDescription>
              {t.inputBox.followupConfirmDescription}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              {t.common.cancel}
            </Button>
            <Button variant="secondary" onClick={confirmAppendAndSend}>
              {t.inputBox.followupConfirmAppend}
            </Button>
            <Button onClick={confirmReplaceAndSend}>
              {t.inputBox.followupConfirmReplace}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SuggestionList({
  textareaRef,
}: {
  textareaRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const { t } = useI18n();
  const { textInput } = usePromptInputController();
  const handleSuggestionClick = useCallback(
    (prompt: string | undefined) => {
      if (!prompt) return;
      textInput.setInput(prompt);
      requestAnimationFrame(() => {
        const textarea = textareaRef.current;
        const placeholder = findSuggestionTemplatePlaceholder(prompt);
        if (textarea && placeholder) {
          textarea.focus();
          textarea.setSelectionRange(placeholder.start, placeholder.end);
        }
      });
    },
    [textareaRef, textInput],
  );
  return (
    <Suggestions className="min-h-16 w-full max-w-full justify-center px-4 sm:w-fit sm:px-0">
      <ConfettiButton
        className="text-muted-foreground cursor-pointer rounded-full px-4 text-xs font-normal"
        variant="outline"
        size="sm"
        onClick={() => handleSuggestionClick(t.inputBox.surpriseMePrompt)}
      >
        <SparklesIcon className="size-4" /> {t.inputBox.surpriseMe}
      </ConfettiButton>
      {t.inputBox.suggestions.map((suggestion) => (
        <Suggestion
          key={suggestion.suggestion}
          icon={suggestion.icon}
          suggestion={suggestion.suggestion}
          onClick={() => handleSuggestionClick(suggestion.prompt)}
        />
      ))}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Suggestion icon={PlusIcon} suggestion={t.common.create} />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuGroup>
            {t.inputBox.suggestionsCreate.map((suggestion, index) =>
              "type" in suggestion && suggestion.type === "separator" ? (
                <DropdownMenuSeparator key={index} />
              ) : (
                !("type" in suggestion) && (
                  <DropdownMenuItem
                    key={suggestion.suggestion}
                    onClick={() => handleSuggestionClick(suggestion.prompt)}
                  >
                    {suggestion.icon && <suggestion.icon className="size-4" />}
                    {suggestion.suggestion}
                  </DropdownMenuItem>
                )
              ),
            )}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </Suggestions>
  );
}

function AddAttachmentsButton({ className }: { className?: string }) {
  const { t } = useI18n();
  const attachments = usePromptInputAttachments();
  return (
    <Tooltip content={t.inputBox.addAttachments}>
      <PromptInputButton
        className={cn("px-2!", className)}
        onClick={() => attachments.openFileDialog()}
      >
        <PaperclipIcon className="size-3" />
      </PromptInputButton>
    </Tooltip>
  );
}
