import { afterEach, expect, rs, test } from "@rstest/core";
import { createElement, isValidElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const SKILL_PATH = "/outputs/example.skill";

function containsText(node: ReactNode, text: string): boolean {
  if (typeof node === "string") return node.includes(text);
  if (Array.isArray(node))
    return node.some((child) => containsText(child, text));
  if (!isValidElement<{ children?: ReactNode }>(node)) return false;
  return containsText(node.props.children, text);
}

async function renderSkillInstallActions({
  role,
  staticWebsite,
}: {
  role: "admin" | "user" | null;
  staticWebsite: boolean;
}) {
  let listInstallAction:
    | ((event: {
        preventDefault: () => void;
        stopPropagation: () => void;
      }) => Promise<void>)
    | undefined;
  let detailInstallAction: (() => Promise<void>) | undefined;
  const installSkill = rs.fn(async () => ({
    success: true,
    message: "installed",
  }));
  rs.resetModules();
  rs.doMock("@/core/auth/AuthProvider", () => ({
    useAuth: () => ({
      user: role ? { system_role: role } : null,
    }),
  }));
  rs.doMock("@/env", () => ({
    env: {
      NEXT_PUBLIC_STATIC_WEBSITE_ONLY: staticWebsite ? "true" : undefined,
    },
  }));
  rs.doMock("@/core/i18n/hooks", () => ({
    useI18n: () => ({
      t: {
        clipboard: { copyToClipboard: "Copy" },
        common: {
          close: "Close",
          download: "Download",
          install: "Install",
          loading: "Loading",
          openInNewWindow: "Open",
        },
        toolCalls: { skillInstallTooltip: "Install skill" },
      },
    }),
  }));
  rs.doMock("@/components/workspace/artifacts/context", () => ({
    useArtifacts: () => ({
      artifacts: [SKILL_PATH],
      select: rs.fn(),
      setOpen: rs.fn(),
    }),
  }));
  rs.doMock("@/components/workspace/messages/context", () => ({
    useThread: () => ({
      isMock: false,
      thread: { messages: [] },
    }),
  }));
  rs.doMock("@/core/artifacts/hooks", () => ({
    useArtifactContent: () => ({
      content: undefined,
      error: null,
      isLoading: true,
      refetch: rs.fn(),
      url: undefined,
    }),
  }));
  rs.doMock("@/core/skills/api", () => ({ installSkill }));
  rs.doMock("sonner", () => ({
    toast: { error: rs.fn(), success: rs.fn() },
  }));
  rs.doMock("@/components/ui/button", () => ({
    Button: ({
      children,
      disabled,
      onClick,
    }: {
      children?: ReactNode;
      disabled?: boolean;
      onClick?: unknown;
    }) => {
      if (containsText(children, "Install")) {
        listInstallAction = onClick as typeof listInstallAction;
      }
      return createElement("button", { disabled }, children);
    },
  }));
  rs.doMock("@/components/ai-elements/artifact", () => {
    const Wrapper = ({ children }: { children?: ReactNode }) =>
      createElement("div", null, children);
    return {
      Artifact: Wrapper,
      ArtifactAction: ({
        disabled,
        label,
        onClick,
      }: {
        disabled?: boolean;
        label: string;
        onClick?: unknown;
      }) => {
        if (label === "Install") {
          detailInstallAction = onClick as typeof detailInstallAction;
        }
        return createElement("button", { disabled }, label);
      },
      ArtifactActions: Wrapper,
      ArtifactContent: Wrapper,
      ArtifactHeader: Wrapper,
      ArtifactTitle: Wrapper,
    };
  });
  rs.doMock("@/components/workspace/tooltip", () => ({
    Tooltip: ({ children }: { children?: ReactNode }) => children,
  }));

  const [{ ArtifactFileList }, { ArtifactFileDetail }] = await Promise.all([
    import("@/components/workspace/artifacts/artifact-file-list"),
    import("@/components/workspace/artifacts/artifact-file-detail"),
  ]);

  return {
    detail: renderToStaticMarkup(
      createElement(ArtifactFileDetail, {
        filepath: SKILL_PATH,
        threadId: "thread-1",
      }),
    ),
    list: renderToStaticMarkup(
      createElement(ArtifactFileList, {
        files: [SKILL_PATH],
        threadId: "thread-1",
      }),
    ),
    detailInstallAction,
    installSkill,
    listInstallAction,
  };
}

afterEach(() => {
  rs.doUnmock("@/core/auth/AuthProvider");
  rs.doUnmock("@/env");
  rs.doUnmock("@/core/i18n/hooks");
  rs.doUnmock("@/components/workspace/artifacts/context");
  rs.doUnmock("@/components/workspace/messages/context");
  rs.doUnmock("@/core/artifacts/hooks");
  rs.doUnmock("@/core/skills/api");
  rs.doUnmock("sonner");
  rs.doUnmock("@/components/ui/button");
  rs.doUnmock("@/components/ai-elements/artifact");
  rs.doUnmock("@/components/workspace/tooltip");
  rs.resetModules();
});

test.each([
  { role: "user" as const, staticWebsite: false, visible: false },
  { role: null, staticWebsite: false, visible: false },
  { role: "admin" as const, staticWebsite: false, visible: true },
  { role: "admin" as const, staticWebsite: true, visible: false },
])(
  "artifact list and detail expose skill install only when role=$role static=$staticWebsite",
  async ({ role, staticWebsite, visible }) => {
    const views = await renderSkillInstallActions({ role, staticWebsite });

    for (const html of [views.list, views.detail]) {
      expect(html.includes("Install")).toBe(visible);
    }
    if (!visible) {
      expect(views.listInstallAction).toBeUndefined();
      expect(views.detailInstallAction).toBeUndefined();
      expect(views.installSkill).not.toHaveBeenCalled();
      return;
    }

    expect(views.listInstallAction).toBeDefined();
    expect(views.detailInstallAction).toBeDefined();
    await views.listInstallAction!({
      preventDefault: rs.fn(),
      stopPropagation: rs.fn(),
    });
    await views.detailInstallAction!();
    expect(views.installSkill).toHaveBeenCalledTimes(2);
    expect(views.installSkill).toHaveBeenNthCalledWith(1, {
      path: SKILL_PATH,
      thread_id: "thread-1",
    });
    expect(views.installSkill).toHaveBeenNthCalledWith(2, {
      path: SKILL_PATH,
      thread_id: "thread-1",
    });
  },
);
