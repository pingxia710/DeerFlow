export function shouldClearPromptInputForThreadChange(
  previousThreadId: string,
  nextThreadId: string,
) {
  return previousThreadId !== nextThreadId;
}
