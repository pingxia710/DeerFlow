export function shouldDeselectArtifactForThreadChange(
  previousThreadId: string,
  nextThreadId: string,
) {
  return previousThreadId !== nextThreadId;
}

export function shouldAutoSelectStaticArtifact({
  artifactCount,
  autoSelectFirstArtifact,
  isMobileViewport,
  staticWebsiteOnly,
}: {
  artifactCount: number;
  autoSelectFirstArtifact: boolean;
  isMobileViewport: boolean;
  staticWebsiteOnly: boolean;
}) {
  return (
    staticWebsiteOnly &&
    autoSelectFirstArtifact &&
    !isMobileViewport &&
    artifactCount > 0
  );
}

export function getCurrentThreadArtifacts(
  artifacts: readonly string[] | null | undefined,
  messages: readonly {
    id?: unknown;
    type?: unknown;
    tool_calls?: readonly {
      id?: unknown;
      name?: unknown;
      args?: unknown;
    }[];
  }[] = [],
) {
  const current = new Set(artifacts ?? []);
  for (const message of messages) {
    if (message.type !== "ai" || typeof message.id !== "string") {
      continue;
    }
    for (const toolCall of message.tool_calls ?? []) {
      if (
        (toolCall.name !== "write_file" && toolCall.name !== "str_replace") ||
        typeof toolCall.id !== "string" ||
        typeof toolCall.args !== "object" ||
        toolCall.args === null
      ) {
        continue;
      }
      const path = Reflect.get(toolCall.args, "path");
      if (typeof path !== "string" || path.length === 0) {
        continue;
      }
      current.add(
        new URL(
          `write-file:${path}?message_id=${message.id}&tool_call_id=${toolCall.id}`,
        ).toString(),
      );
    }
  }
  return [...current];
}

export function getEffectiveSelectedArtifact(
  selectedArtifact: string | null | undefined,
  currentArtifacts: readonly string[] | null | undefined,
) {
  if (!selectedArtifact) {
    return null;
  }
  return currentArtifacts?.includes(selectedArtifact) ? selectedArtifact : null;
}

export function shouldShowArtifactPanel(
  artifactsOpen: boolean,
  currentArtifacts: readonly string[] | null | undefined,
  effectiveSelectedArtifact: string | null | undefined,
  staticWebsiteOnly: boolean,
) {
  if (!artifactsOpen) {
    return false;
  }
  if (!staticWebsiteOnly) {
    return true;
  }
  return (
    Boolean(effectiveSelectedArtifact) || Boolean(currentArtifacts?.length)
  );
}
