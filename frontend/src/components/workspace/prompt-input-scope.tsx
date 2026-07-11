"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  type MutableRefObject,
  type ReactNode,
} from "react";

import {
  usePromptInputAttachments,
  usePromptInputController,
} from "@/components/ai-elements/prompt-input";

type PromptInputScopeContextValue = {
  activeScopeKeyRef: MutableRefObject<string | null>;
};

const PromptInputScopeContext =
  createContext<PromptInputScopeContextValue | null>(null);

export function shouldResetPromptInputScope(
  previousScopeKey: string | null,
  nextScopeKey: string,
) {
  return previousScopeKey !== null && previousScopeKey !== nextScopeKey;
}

export function PromptInputScopeProvider({
  children,
}: {
  children: ReactNode;
}) {
  const activeScopeKeyRef = useRef<string | null>(null);
  const value = useMemo(() => ({ activeScopeKeyRef }), []);
  return (
    <PromptInputScopeContext.Provider value={value}>
      {children}
    </PromptInputScopeContext.Provider>
  );
}

export function usePromptInputScope(scopeKey: string) {
  const scope = useContext(PromptInputScopeContext);
  const { textInput } = usePromptInputController();
  const attachments = usePromptInputAttachments();
  const clearInput = textInput.clear;
  const clearAttachments = attachments.clear;

  if (!scope) {
    throw new Error(
      "usePromptInputScope must be used within PromptInputScopeProvider",
    );
  }

  useEffect(() => {
    const previousScopeKey = scope.activeScopeKeyRef.current;
    scope.activeScopeKeyRef.current = scopeKey;
    if (!shouldResetPromptInputScope(previousScopeKey, scopeKey)) {
      return;
    }
    clearInput();
    clearAttachments();
  }, [clearAttachments, clearInput, scope, scopeKey]);
}
