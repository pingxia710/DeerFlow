import type { LucideIcon } from "lucide-react";

export interface Translations {
  // Locale meta
  locale: {
    localName: string;
  };

  // Common
  common: {
    home: string;
    settings: string;
    delete: string;
    edit: string;
    rename: string;
    renameFailed: string;
    share: string;
    openInNewWindow: string;
    close: string;
    more: string;
    search: string;
    loadMore: string;
    download: string;
    thinking: string;
    artifacts: string;
    public: string;
    custom: string;
    notAvailableInDemoMode: string;
    loading: string;
    version: string;
    lastUpdated: string;
    code: string;
    preview: string;
    cancel: string;
    save: string;
    install: string;
    create: string;
    import: string;
    export: string;
    exportAsMarkdown: string;
    exportAsJSON: string;
    exportSuccess: string;
    regenerate: string;
    retry: string;
    submit: string;
    stop: string;
  };

  home: {
    docs: string;
    blog: string;
  };

  // Welcome
  welcome: {
    greeting: string;
    description: string;
    createYourOwnSkill: string;
    createYourOwnSkillDescription: string;
  };

  // Clipboard
  clipboard: {
    copyToClipboard: string;
    copiedToClipboard: string;
    failedToCopyToClipboard: string;
    linkCopied: string;
  };

  // Input Box
  inputBox: {
    placeholder: string;
    createSkillPrompt: string;
    addAttachments: string;
    mode: string;
    flashMode: string;
    flashModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    proMode: string;
    proModeDescription: string;
    ultraMode: string;
    ultraModeDescription: string;
    reasoningEffort: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortMediumShort: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    reasoningEffortHighShort: string;
    reasoningEffortPro: string;
    reasoningEffortProDescription: string;
    reasoningEffortProShort: string;
    reasoningEffortMax: string;
    reasoningEffortMaxDescription: string;
    reasoningEffortMaxShort: string;
    reasoningEffortUltra: string;
    reasoningEffortUltraDescription: string;
    reasoningEffortUltraShort: string;
    reasoningSummary: string;
    reasoningSummaryAuto: string;
    reasoningSummaryAutoDescription: string;
    reasoningSummaryConcise: string;
    reasoningSummaryConciseDescription: string;
    reasoningSummaryDetailed: string;
    reasoningSummaryDetailedDescription: string;
    textVerbosity: string;
    textVerbosityLow: string;
    textVerbosityLowDescription: string;
    textVerbosityMedium: string;
    textVerbosityMediumDescription: string;
    textVerbosityHigh: string;
    textVerbosityHighDescription: string;
    model: string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
    followupLoading: string;
    followupConfirmTitle: string;
    followupConfirmDescription: string;
    followupConfirmAppend: string;
    followupConfirmReplace: string;
    waitForCurrentResponse: string;
    suggestionPlaceholderRequired: string;
    suggestions: {
      suggestion: string;
      prompt: string;
      icon: LucideIcon;
    }[];
    suggestionsCreate: (
      | {
          suggestion: string;
          prompt: string;
          icon: LucideIcon;
        }
      | {
          type: "separator";
        }
    )[];
  };

  // Sidebar
  sidebar: {
    recentChats: string;
    newChat: string;
    chats: string;
    demoChats: string;
    chatRunning: string;
    chatFinished: string;
    agents: string;
    channels: string;
  };

  // Agents
  agents: {
    title: string;
    description: string;
    agentsTab: string;
    rolesTab: string;
    emptyRoles: string;
    roleCopy: Record<string, { name: string; description: string }>;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    chat: string;
    configure: string;
    model: string;
    reasoningEffort: string;
    noModels: string;
    agentConfigurationDescription: string;
    roleConfigurationDescription: string;
    configurationSaved: string;
    delete: string;
    deleteConfirm: string;
    deleteSuccess: string;
    newChat: string;
    createPageTitle: string;
    createPageSubtitle: string;
    nameStepTitle: string;
    nameStepHint: string;
    nameStepPlaceholder: string;
    nameStepContinue: string;
    nameStepInvalidError: string;
    nameStepAlreadyExistsError: string;
    nameStepNetworkError: string;
    nameStepCheckError: string;
    nameStepCheckErrorWithDetail: string;
    nameStepApiDisabledError: string;
    nameStepBootstrapMessage: string;
    save: string;
    saving: string;
    saveRequested: string;
    saveHint: string;
    saveCommandMessage: string;
    agentCreatedPendingRefresh: string;
    more: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
  };

  capabilities: {
    title: string;
    ready: string;
    attention: string;
    model: string;
    childModel: string;
    reasoningEffort: string;
    timeout: string;
    sandbox: string;
    directTools: string;
    skills: string;
    missingSkills: string;
    unavailable: string;
    none: string;
  };

  // Breadcrumb
  breadcrumb: {
    workspace: string;
    chats: string;
  };

  // Workspace
  workspace: {
    officialWebsite: string;
    githubTooltip: string;
    settingsAndMore: string;
    visitGithub: string;
    reportIssue: string;
    contactUs: string;
    about: string;
    logout: string;
    gatewayUnavailable: string;
    gatewayUnavailableHelp: string;
  };

  // Conversation
  conversation: {
    noMessages: string;
    startConversation: string;
    turn: (number: number) => string;
    completedAt: (time: string) => string;
    elapsed: (minutes: number, seconds: number) => string;
    inProgress: string;
    failed: string;
    returnToCurrentReply: string;
    deleteTitle: string;
    deleteDescription: string;
    deleteTarget: string;
    deleteWillRemove: string;
    deleteMessagesAndConversation: string;
    deleteLocalThreadData: string;
    deleteActiveRuns: string;
    deleteRunRecords: string;
    deleteWillNotRemove: string;
    deleteSavedMemory: string;
    deleteExternalMessages: string;
    deleteDeletingConversation: string;
    deleteFinishingLocalCleanup: string;
    deleteInProgress: string;
    deletePartialFailure: string;
    deleteFailed: string;
    deleteSuccess: string;
    retryDelete: string;
  };

  // Chats
  chats: {
    searchChats: string;
    loadMoreToSearch: string;
    loadingMore: string;
    loadOlderChats: string;
    historyNotFoundTitle: string;
    historyNotFoundDescription: string;
    historyForbiddenTitle: string;
    historyForbiddenDescription: string;
    historyLoadFailedTitle: string;
    historyLoadFailedDescription: string;
    runTerminalNoticeTitle: string;
    runTerminalNoticeDescription: (status: string, reason?: string) => string;
    runRecoveryRepairingTitle: string;
    runRecoveryFailedTitle: string;
    runRecoveryTerminalTitle: string;
    runRecoveryRepairingDescription: string;
    runRecoveryTerminalDescription: (reason?: string) => string;
    retryRecovery: string;
    backToTurnStart: string;
    commandRoomUpdate: string;
    steps: {
      count: (count: number) => string;
      toggle: (count: number) => string;
    };
    workRecord: {
      title: string;
      open: string;
      close: string;
      retry: string;
      loading: string;
      empty: string;
      unavailable: string;
      truncated: string;
      taskStarted: string;
      taskCompleted: string;
      taskFailed: string;
      taskCancelled: string;
      taskTimedOut: string;
      runLifecycle: string;
      artifactRecorded: string;
      runRunning: string;
      tasksRunning: (count: number) => string;
      eventHistory: string;
      backgroundWakeFailed: (
        taskId: string,
        sourceRunId: string,
        roundId: string,
        attempts: number,
      ) => string;
    };
  };

  // Channels
  channels: {
    title: string;
    connect: string;
    modify: string;
    reconnect: string;
    disconnect: string;
    connected: string;
    notConnected: string;
    pending: string;
    revoked: string;
    disabled: string;
    unconfigured: string;
    unavailable: string;
    unavailableShort: string;
    setupTitle: (name: string) => string;
    setupEditTitle: (name: string) => string;
    setupDescription: string;
    saveAndConnect: string;
    saveChanges: string;
    descriptions: Record<string, string>;
    connectedAs: (name: string) => string;
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    newChat: string;
    untitled: string;
  };

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => string;
    lessSteps: string;
    executeCommand: string;
    presentFiles: string;
    needYourHelp: string;
    useTool: (toolName: string) => string;
    searchForRelatedInfo: string;
    searchForRelatedImages: string;
    searchFor: (query: string) => string;
    searchForRelatedImagesFor: (query: string) => string;
    searchOnWebFor: (query: string) => string;
    viewWebPage: string;
    listFolder: string;
    readFile: string;
    writeFile: string;
    clickToViewContent: string;
    writeTodos: string;
    skillInstallTooltip: string;
  };

  // Uploads
  uploads: {
    uploading: string;
    uploadingFiles: string;
  };

  // Subtasks
  subtasks: {
    subtask: string;
    executing: (count: number) => string;
    in_progress: string;
    completed: string;
    failed: string;
    unknown: string;
    recoveryFailedUnknown: string;
    backgroundWakeFailed: (attempts: number) => string;
    artifactPending: string;
    artifactLoading: string;
    artifactUnavailable: string;
    artifactCheckFailed: string;
    viewArtifact: (name: string) => string;
    downloadArtifact: (name: string) => string;
  };

  // Token Usage
  tokenUsage: {
    title: string;
    label: string;
    input: string;
    output: string;
    total: string;
    callerBreakdown: string;
    callerLeadAgentShort: string;
    callerLeadAgent: string;
    callerSubagent: string;
    callerMiddleware: string;
    view: string;
    unavailable: string;
    unavailableShort: string;
    note: string;
    presets: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    presetDescriptions: {
      off: string;
      summary: string;
      perTurn: string;
      debug: string;
    };
    finalAnswer: string;
    stepTotal: string;
    sharedAttribution: string;
    subagent: (description: string) => string;
    startTodo: (content: string) => string;
    completeTodo: (content: string) => string;
    updateTodo: (content: string) => string;
    removeTodo: (content: string) => string;
  };

  contextUsage: {
    title: string;
    label: string;
    estimated: string;
    messages: string;
    tools: string;
    chars: string;
    charUnit: string;
    byCaller: string;
    unavailable: string;
    openFullText: string;
    complete: string;
    calls: string;
    modelMessages: string;
    toolSchemas: string;
    loading: string;
    fullTextUnavailable: string;
  };

  // Shortcuts
  shortcuts: {
    searchActions: string;
    noResults: string;
    actions: string;
    keyboardShortcuts: string;
    keyboardShortcutsDescription: string;
    openCommandPalette: string;
    toggleSidebar: string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      account: string;
      appearance: string;
      channels: string;
      memory: string;
      tools: string;
      skills: string;
      notification: string;
      about: string;
    };
    memory: {
      title: string;
      description: string;
      empty: string;
      rawJson: string;
      exportButton: string;
      exportSuccess: string;
      importButton: string;
      importConfirmTitle: string;
      importConfirmDescription: string;
      importFileLabel: string;
      importInvalidFile: string;
      importSuccess: string;
      manualFactSource: string;
      addFact: string;
      addFactTitle: string;
      editFactTitle: string;
      addFactSuccess: string;
      editFactSuccess: string;
      clearAll: string;
      clearAllConfirmTitle: string;
      clearAllConfirmDescription: string;
      clearAllSuccess: string;
      factDeleteConfirmTitle: string;
      factDeleteConfirmDescription: string;
      factDeleteSuccess: string;
      factContentLabel: string;
      factCategoryLabel: string;
      factConfidenceLabel: string;
      factContentPlaceholder: string;
      factCategoryPlaceholder: string;
      factConfidenceHint: string;
      factSave: string;
      factValidationContent: string;
      factValidationConfidence: string;
      noFacts: string;
      summaryReadOnly: string;
      memoryFullyEmpty: string;
      factPreviewLabel: string;
      searchPlaceholder: string;
      filterAll: string;
      filterFacts: string;
      filterSummaries: string;
      noMatches: string;
      markdown: {
        overview: string;
        userContext: string;
        work: string;
        personal: string;
        topOfMind: string;
        historyBackground: string;
        recentMonths: string;
        earlierContext: string;
        longTermBackground: string;
        updatedAt: string;
        facts: string;
        empty: string;
        table: {
          category: string;
          confidence: string;
          confidenceLevel: {
            veryHigh: string;
            high: string;
            normal: string;
            unknown: string;
          };
          content: string;
          source: string;
          createdAt: string;
          view: string;
        };
      };
    };
    appearance: {
      themeTitle: string;
      themeDescription: string;
      system: string;
      light: string;
      dark: string;
      systemDescription: string;
      lightDescription: string;
      darkDescription: string;
      languageTitle: string;
      languageDescription: string;
    };
    tools: {
      title: string;
      description: string;
      adminRequired: string;
      empty: string;
    };
    channels: {
      title: string;
      description: string;
      disabled: string;
    };
    skills: {
      title: string;
      description: string;
      createSkill: string;
      adminRequired: string;
      emptyTitle: string;
      emptyDescription: string;
      emptyButton: string;
    };
    notification: {
      title: string;
      description: string;
      requestPermission: string;
      deniedHint: string;
      testButton: string;
      testTitle: string;
      testBody: string;
      notSupported: string;
      disableNotification: string;
    };
    account: {
      profileTitle: string;
      email: string;
      role: string;
      changePasswordTitle: string;
      changePasswordDescription: string;
      ssoProvider: string;
      ssoPasswordDescription: string;
      ssoPasswordMessage: string;
      currentPassword: string;
      newPassword: string;
      confirmNewPassword: string;
      passwordMismatch: string;
      passwordTooShort: string;
      passwordChangedSuccess: string;
      networkError: string;
      updating: string;
      updatePassword: string;
      signOut: string;
    };
    acknowledge: {
      emptyTitle: string;
      emptyDescription: string;
    };
  };

  // Login / Auth
  login: {
    signInTitle: string;
    createAccountTitle: string;
    email: string;
    emailPlaceholder: string;
    password: string;
    passwordPlaceholder: string;
    pleaseWait: string;
    signIn: string;
    createAccount: string;
    orContinueWith: string;
    ssoHint: string;
    continueWith: (provider: string) => string;
    noAccountSignUp: string;
    haveAccountSignIn: string;
    backToHome: string;
    networkError: string;
    authFailed: string;
    errors: {
      sso_failed: string;
      sso_cancelled: string;
      sso_account_exists: string;
      sso_not_allowed: string;
    };
  };
}
