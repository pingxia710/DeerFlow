import { FilesIcon, XIcon } from "lucide-react";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import type { GroupImperativeHandle } from "react-resizable-panels";

import { ConversationEmptyState } from "@/components/ai-elements/conversation";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { env } from "@/env";
import { useIsMobile } from "@/hooks/use-mobile";
import { cn } from "@/lib/utils";

import {
  ArtifactFileDetail,
  ArtifactFileList,
  ArtifactsProvider,
  useArtifacts,
} from "../artifacts";
import { useThread } from "../messages/context";

import {
  getCurrentThreadArtifacts,
  getEffectiveSelectedArtifact,
  shouldAutoSelectStaticArtifact,
  shouldDeselectArtifactForThreadChange,
  shouldShowArtifactPanel,
} from "./chat-box-state";

const CLOSE_MODE = { chat: 100, artifacts: 0 };
const OPEN_MODE = { chat: 60, artifacts: 40 };
const MOBILE_OPEN_MODE = { chat: 0, artifacts: 100 };

const ChatBoxContent: React.FC<{
  children: React.ReactNode;
  threadId: string;
}> = ({ children, threadId }) => {
  const { thread } = useThread();
  const pathname = usePathname();
  const threadIdRef = useRef(threadId);
  const layoutRef = useRef<GroupImperativeHandle>(null);
  const isMobileViewport = useIsMobile();
  const useMobileArtifactLayout =
    typeof window !== "undefined" ? window.innerWidth < 768 : isMobileViewport;

  const {
    open: artifactsOpen,
    setOpen: setArtifactsOpen,
    setArtifacts,
    select: selectArtifact,
    deselect,
    selectedArtifact,
  } = useArtifacts();

  const [autoSelectFirstArtifact, setAutoSelectFirstArtifact] = useState(true);
  const currentArtifacts = useMemo(
    () => getCurrentThreadArtifacts(thread.values.artifacts, thread.messages),
    [thread.messages, thread.values.artifacts],
  );
  const effectiveSelectedArtifact = useMemo(
    () => getEffectiveSelectedArtifact(selectedArtifact, currentArtifacts),
    [selectedArtifact, currentArtifacts],
  );

  useEffect(() => {
    if (shouldDeselectArtifactForThreadChange(threadIdRef.current, threadId)) {
      threadIdRef.current = threadId;
      deselect();
    }

    // Update artifacts from the current thread only. The detail renderer below
    // still gates selectedArtifact against currentArtifacts so a stale global
    // selection cannot leak across thread/window switches.
    setArtifacts(currentArtifacts);

    if (
      shouldAutoSelectStaticArtifact({
        artifactCount: currentArtifacts.length,
        autoSelectFirstArtifact,
        isMobileViewport: useMobileArtifactLayout,
        staticWebsiteOnly: env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
      })
    ) {
      setAutoSelectFirstArtifact(false);
      selectArtifact(currentArtifacts[0]!);
      setArtifactsOpen(true);
    }
  }, [
    threadId,
    autoSelectFirstArtifact,
    deselect,
    selectArtifact,
    selectedArtifact,
    setArtifacts,
    setArtifactsOpen,
    currentArtifacts,
    useMobileArtifactLayout,
  ]);

  const artifactPanelOpen = useMemo(
    () =>
      shouldShowArtifactPanel(
        artifactsOpen,
        currentArtifacts,
        effectiveSelectedArtifact,
        env.NEXT_PUBLIC_STATIC_WEBSITE_ONLY === "true",
      ),
    [artifactsOpen, currentArtifacts, effectiveSelectedArtifact],
  );

  const resizableIdBase = useMemo(() => {
    return pathname.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
  }, [pathname]);

  useEffect(() => {
    if (layoutRef.current) {
      if (artifactPanelOpen) {
        layoutRef.current.setLayout(
          useMobileArtifactLayout ? MOBILE_OPEN_MODE : OPEN_MODE,
        );
      } else {
        layoutRef.current.setLayout(CLOSE_MODE);
      }
    }
  }, [artifactPanelOpen, useMobileArtifactLayout]);

  return (
    <ResizablePanelGroup
      id={`${resizableIdBase}-panels`}
      orientation="horizontal"
      defaultLayout={{ chat: 100, artifacts: 0 }}
      groupRef={layoutRef}
    >
      <ResizablePanel className="relative" defaultSize={100} id="chat">
        {children}
      </ResizablePanel>
      <ResizableHandle
        id={`${resizableIdBase}-separator`}
        className={cn(
          "opacity-33 hover:opacity-100",
          !artifactPanelOpen && "pointer-events-none opacity-0",
        )}
      />
      <ResizablePanel
        className={cn(
          "transition-all duration-300 ease-in-out",
          !artifactsOpen && "opacity-0",
        )}
        id="artifacts"
      >
        <div
          className={cn(
            "h-full p-4 transition-transform duration-300 ease-in-out",
            artifactPanelOpen ? "translate-x-0" : "translate-x-full",
          )}
        >
          {effectiveSelectedArtifact ? (
            <ArtifactFileDetail
              className="size-full"
              filepath={effectiveSelectedArtifact}
              threadId={threadId}
            />
          ) : (
            <div className="relative flex size-full justify-center">
              <div className="absolute top-1 right-1 z-30">
                <Button
                  size="icon-sm"
                  variant="ghost"
                  onClick={() => {
                    setArtifactsOpen(false);
                  }}
                >
                  <XIcon />
                </Button>
              </div>
              {currentArtifacts.length === 0 ? (
                <ConversationEmptyState
                  icon={<FilesIcon />}
                  title="No artifact selected"
                  description="Select an artifact to view its details"
                />
              ) : (
                <div className="flex size-full max-w-(--container-width-sm) flex-col justify-center p-4 pt-8">
                  <header className="shrink-0">
                    <h2 className="text-lg font-medium">Artifacts</h2>
                  </header>
                  <main className="min-h-0 grow">
                    <ArtifactFileList
                      className="max-w-(--container-width-sm) p-4 pt-12"
                      files={currentArtifacts}
                      threadId={threadId}
                    />
                  </main>
                </div>
              )}
            </div>
          )}
        </div>
      </ResizablePanel>
    </ResizablePanelGroup>
  );
};

const ChatBox: React.FC<{ children: React.ReactNode; threadId: string }> = (
  props,
) => (
  <ArtifactsProvider>
    <ChatBoxContent {...props} />
  </ArtifactsProvider>
);

export { ChatBox };
