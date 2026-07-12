export function staticDemoIndexMetadata(thread) {
  const metadata = {};
  const agentName = thread.context?.agent_name ?? thread.metadata?.agent_name;
  if (typeof agentName === "string" && agentName.trim()) {
    metadata.agent_name = agentName;
  }

  const channelSource = thread.metadata?.channel_source;
  if (
    channelSource &&
    typeof channelSource === "object" &&
    !Array.isArray(channelSource) &&
    channelSource.type === "im_channel" &&
    typeof channelSource.provider === "string" &&
    channelSource.provider.trim()
  ) {
    metadata.channel_source = {
      type: "im_channel",
      provider: channelSource.provider,
    };
  }
  return metadata;
}

export function staticDemoIndexEntry(threadId, thread) {
  return {
    thread_id: threadId,
    created_at: thread.created_at ?? null,
    updated_at: thread.updated_at ?? thread.created_at ?? null,
    metadata: staticDemoIndexMetadata(thread),
    status: "idle",
    values: {
      title: thread.values?.title ?? "Untitled",
      messages: [],
      artifacts: [],
    },
    interrupts: {},
  };
}
