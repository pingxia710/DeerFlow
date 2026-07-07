export function shouldDeselectArtifactForThreadChange(
  previousThreadId: string,
  nextThreadId: string,
) {
  return previousThreadId !== nextThreadId;
}
