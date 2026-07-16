import { FilesIcon, XIcon } from "lucide-react";
import { usePathname } from "next/navigation";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
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
import {
  ThreadWorkActivityTrigger,
  ThreadWorkRecordPanel,
  ThreadWorkRecordTrigger,
} from "./thread-work-record";

const CLOSE_MODE = { artifacts: 0, chat: 100, workRecord: 0 };
const ARTIFACT_OPEN_MODE = { artifacts: 40, chat: 60, workRecord: 0 };
const WORK_RECORD_OPEN_MODE = { artifacts: 0, chat: 68, workRecord: 32 };
const BOTH_PANELS_OPEN_MODE = { artifacts: 26, chat: 48, workRecord: 26 };
const MOBILE_ARTIFACT_OPEN_MODE = { artifacts: 100, chat: 0, workRecord: 0 };

const WorkRecordContext = createContext<{
  open: boolean;
  setOpen: (open: boolean) => void;
  threadId: string;
  enabled: boolean;
} | null>(null);

export function WorkRecordTrigger() {
  const controls = useContext(WorkRecordContext);
  if (!controls) {
    return null;
  }
  return (
    <ThreadWorkRecordTrigger
      open={controls.open}
      onOpenChange={controls.setOpen}
    />
  );
}

export function WorkRecordActivityTrigger() {
  const controls = useContext(WorkRecordContext);
  if (!controls) {
    return null;
  }
  return (
    <ThreadWorkActivityTrigger
      enabled={controls.enabled}
      threadId={controls.threadId}
      onOpen={() => controls.setOpen(true)}
    />
  );
}

const ChatBoxContent: React.FC<{
  children: React.ReactNode;
  isNewThread: boolean;
  threadId: string;
}> = ({ children, isNewThread, threadId }) => {
  const { thread } = useThread();
  const pathname = usePathname();
  const threadIdRef = useRef(threadId);
  const workRecordThreadIdRef = useRef(threadId);
  const layoutRef = useRef<GroupImperativeHandle>(null);
  const isMobileViewport = useIsMobile();
  const useMobileArtifactLayout = isMobileViewport;

  const {
    open: artifactsOpen,
    setOpen: setArtifactsOpen,
    setArtifacts,
    select: selectArtifact,
    deselect,
    selectedArtifact,
  } = useArtifacts();

  const [autoSelectFirstArtifact, setAutoSelectFirstArtifact] = useState(true);
  const [workRecordOpen, setWorkRecordOpen] = useState(false);
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

  useEffect(() => {
    if (workRecordThreadIdRef.current === threadId) {
      return;
    }
    workRecordThreadIdRef.current = threadId;
    setWorkRecordOpen(false);
  }, [threadId]);

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
      const layout = useMobileArtifactLayout
        ? artifactPanelOpen
          ? MOBILE_ARTIFACT_OPEN_MODE
          : CLOSE_MODE
        : artifactPanelOpen && workRecordOpen
          ? BOTH_PANELS_OPEN_MODE
          : artifactPanelOpen
            ? ARTIFACT_OPEN_MODE
            : workRecordOpen
              ? WORK_RECORD_OPEN_MODE
              : CLOSE_MODE;
      layoutRef.current.setLayout(layout);
    }
  }, [artifactPanelOpen, useMobileArtifactLayout, workRecordOpen]);

  return (
    <WorkRecordContext.Provider
      value={{
        open: workRecordOpen,
        setOpen: setWorkRecordOpen,
        threadId,
        enabled: !isNewThread,
      }}
    >
      <ResizablePanelGroup
        id={`${resizableIdBase}-panels`}
        orientation="horizontal"
        defaultLayout={CLOSE_MODE}
        groupRef={layoutRef}
      >
        <ResizablePanel className="relative" defaultSize={100} id="chat">
          {children}
        </ResizablePanel>
        <ResizableHandle
          id={`${resizableIdBase}-artifact-separator`}
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
        <ResizableHandle
          id={`${resizableIdBase}-work-record-separator`}
          className={cn(
            "opacity-33 hover:opacity-100",
            (!workRecordOpen || useMobileArtifactLayout) &&
              "pointer-events-none opacity-0",
          )}
        />
        <ResizablePanel
          className={cn(
            "overflow-hidden transition-opacity duration-300",
            (!workRecordOpen || useMobileArtifactLayout) && "opacity-0",
          )}
          id="workRecord"
        >
          {!useMobileArtifactLayout && (
            <ThreadWorkRecordPanel
              enabled={workRecordOpen && !isNewThread}
              mobile={false}
              open={workRecordOpen}
              threadId={threadId}
              onOpenChange={setWorkRecordOpen}
            />
          )}
        </ResizablePanel>
      </ResizablePanelGroup>
      {useMobileArtifactLayout && (
        <ThreadWorkRecordPanel
          enabled={workRecordOpen && !isNewThread}
          mobile
          open={workRecordOpen}
          threadId={threadId}
          onOpenChange={setWorkRecordOpen}
        />
      )}
    </WorkRecordContext.Provider>
  );
};

const ChatBox: React.FC<{
  children: React.ReactNode;
  isNewThread: boolean;
  threadId: string;
}> = (props) => (
  <ArtifactsProvider>
    <ChatBoxContent {...props} />
  </ArtifactsProvider>
);

export { ChatBox };
