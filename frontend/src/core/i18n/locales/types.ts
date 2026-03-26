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
    rename: string;
    share: string;
    openInNewWindow: string;
    close: string;
    more: string;
    search: string;
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
    orchestrationMode: string;
    flashMode: string;
    flashModeDescription: string;
    reasoningMode: string;
    reasoningModeDescription: string;
    proMode: string;
    proModeDescription: string;
    ultraMode: string;
    ultraModeDescription: string;
    autoOrchestrationMode: string;
    autoOrchestrationModeDescription: string;
    leaderOrchestrationMode: string;
    leaderOrchestrationModeDescription: string;
    workflowOrchestrationMode: string;
    workflowOrchestrationModeDescription: string;
    reasoningEffort: string;
    reasoningEffortMinimal: string;
    reasoningEffortMinimalDescription: string;
    reasoningEffortLow: string;
    reasoningEffortLowDescription: string;
    reasoningEffortMedium: string;
    reasoningEffortMediumDescription: string;
    reasoningEffortHigh: string;
    reasoningEffortHighDescription: string;
    searchModels: string;
    surpriseMe: string;
    surpriseMePrompt: string;
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
    agents: string;
    governance: string;
  };

  // Agents
  agents: {
    title: string;
    description: string;
    newAgent: string;
    emptyTitle: string;
    emptyDescription: string;
    chat: string;
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
    nameStepCheckError: string;
    nameStepBootstrapMessage: string;
    agentCreated: string;
    startChatting: string;
    backToGallery: string;
  };

  // Breadcrumb
  breadcrumb: {
    workspace: string;
    chats: string;
    governance: string;
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
  };

  // Conversation
  conversation: {
    noMessages: string;
    startConversation: string;
  };

  // Chats
  chats: {
    searchChats: string;
  };

  // Governance
  governance: {
    title: string;
    description: string;
    queueTab: string;
    historyTab: string;
    refresh: string;
    queueCount: (count: number) => string;
    historyCount: (count: number) => string;
    filters: {
      risk: string;
      status: string;
      agent: string;
      threadId: string;
      runId: string;
      dateFrom: string;
      dateTo: string;
      allRisks: string;
      allStatuses: string;
      allAgents: string;
      reset: string;
    };
    labels: {
      sourceAgent: string;
      createdAt: string;
      resolvedAt: string;
      status: string;
      risk: string;
      eventType: string;
      currentSituation: string;
      nextStep: string;
      relatedContext: string;
      technicalDetail: string;
      category: string;
      hook: string;
      thread: string;
      run: string;
      task: string;
      request: string;
      fingerprint: string;
      tool: string;
      actionSummary: string;
      riskTip: string;
      reason: string;
      detail: string;
      operatorAction: string;
    };
    actions: {
      openThread: string;
      approve: string;
      resolve: string;
      technicalDetail: string;
    };
    states: {
      loadingQueue: string;
      loadingHistory: string;
      loadingDetail: string;
      selectQueueItem: string;
      selectHistoryItem: string;
      emptyQueueTitle: string;
      emptyQueueDescription: string;
      emptyHistoryTitle: string;
      emptyHistoryDescription: string;
      noActionSchema: string;
    };
    result: {
      success: string;
      resumeFailed: string;
      stale: string;
      invalid: string;
      failed: string;
    };
    kindText: {
      clarification: string;
      dependency: string;
      approval: string;
      review: string;
    };
    guidance: {
      resolveInConsole: string;
      continueInThread: string;
      auditOnly: string;
      noReason: string;
      noActionSummary: string;
    };
    readable: {
      titleClarification: (agent: string) => string;
      titleDependency: (agent: string) => string;
      titleApproval: (agent: string) => string;
      titleReview: (agent: string) => string;
      summaryClarification: (agent: string) => string;
      summaryDependency: (agent: string) => string;
      summaryApproval: (agent: string) => string;
      summaryReview: (agent: string) => string;
      situationClarification: (agent: string) => string;
      situationDependency: (agent: string) => string;
      situationApproval: (agent: string) => string;
      situationReview: (agent: string) => string;
    };
    statusText: {
      pending_intervention: string;
      resolved: string;
      rejected: string;
      failed: string;
      expired: string;
      decided: string;
    };
  };

  // Page titles (document title)
  pages: {
    appName: string;
    chats: string;
    governance: string;
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
    pending: string;
    in_progress: string;
    waiting_dependency: string;
    waiting_intervention: string;
    waiting_clarification: string;
    completed: string;
    failed: string;
    errorGeneric: string;
    statusDetail?: {
      taskStarted: string;
      dispatching: string;
      waitingDependency: string;
      waitingClarification: string;
      completed: string;
      failed: string;
      dependencyResolved: string;
      assigned: (agent: string) => string;
      waitingHelper: (agent: string) => string;
      retryingHelper: (agent: string) => string;
    };
    needCapability?: (capability: string) => string;
    requestedBy?: (agent: string) => string;
    resolvedInputs?: string;
    resumed?: string;
    interventionRisk: (riskLevel: string) => string;
    interventionPlaceholder: string;
    interventionSubmitted: string;
    interventionStale: string;
    interventionInvalid: string;
    interventionSubmitFailed: string;
    interventionActionFallback: string;
    interventionRequiredLabel: string;
    interventionNextActionLabel: string;
    interventionDecisionLabel: string;
    interventionDetailsLabel: string;
    interventionDebugLabel: string;
    interventionDebugSummary: string;
    interventionCopy: {
      confirmTitle: string;
      confirmHint: string;
      inputTitle: string;
      singleSelectHint: string;
      multiSelectHint: string;
      customSectionTitle: string;
      singleCustomPlaceholder: string;
      multiCustomPlaceholder: string;
      clarificationConfirmTitle: string;
      clarificationConfirmHint: string;
      clarificationSingleSelectHint: string;
      clarificationMultiSelectHint: string;
      clarificationCustomSectionTitle: string;
      clarificationSubmitLabel: string;
      nextStepLabel: string;
    };
  };

  workflowStatus: {
    initializing: string;
    queued: string;
    acknowledged: string;
    planning: string;
    routing: string;
    resuming: string;
    processing: string;
    executing: string;
    summarizing: string;
    waitingDependency: string;
    waitingIntervention: string;
    waitingClarification: string;
    stopped: string;
    stoppedDescription: string;
    running: (count: number) => string;
    completedSummary: (completed: number, total: number) => string;
  };

  // Settings
  settings: {
    title: string;
    description: string;
    sections: {
      appearance: string;
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
    };
    skills: {
      title: string;
      description: string;
      createSkill: string;
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
    acknowledge: {
      emptyTitle: string;
      emptyDescription: string;
    };
  };
}
