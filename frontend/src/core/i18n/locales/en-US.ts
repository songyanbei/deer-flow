import {
  CompassIcon,
  GraduationCapIcon,
  ImageIcon,
  MicroscopeIcon,
  PenLineIcon,
  ShapesIcon,
  SparklesIcon,
  VideoIcon,
} from "lucide-react";

import type { Translations } from "./types";

export const enUS: Translations = {
  // Locale meta
  locale: {
    localName: "English",
  },

  // Common
  common: {
    home: "Home",
    settings: "Settings",
    delete: "Delete",
    rename: "Rename",
    share: "Share",
    openInNewWindow: "Open in new window",
    close: "Close",
    more: "More",
    search: "Search",
    download: "Download",
    thinking: "Thinking",
    artifacts: "Artifacts",
    public: "Public",
    custom: "Custom",
    notAvailableInDemoMode: "Not available in demo mode",
    loading: "Loading...",
    version: "Version",
    lastUpdated: "Last updated",
    code: "Code",
    preview: "Preview",
    cancel: "Cancel",
    save: "Save",
    install: "Install",
    create: "Create",
  },

  // Welcome
  welcome: {
    greeting: "Hello, again!",
    description:
      "Welcome to 🦌 DeerFlow, an open source super agent. With built-in and custom skills, DeerFlow helps you search on the web, analyze data, and generate artifacts like slides, web pages and do almost anything.",

    createYourOwnSkill: "Create Your Own Skill",
    createYourOwnSkillDescription:
      "Create your own skill to release the power of DeerFlow. With customized skills,\nDeerFlow can help you search on the web, analyze data, and generate\n artifacts like slides, web pages and do almost anything.",
  },

  // Clipboard
  clipboard: {
    copyToClipboard: "Copy to clipboard",
    copiedToClipboard: "Copied to clipboard",
    failedToCopyToClipboard: "Failed to copy to clipboard",
    linkCopied: "Link copied to clipboard",
  },

  // Input Box
  inputBox: {
    placeholder: "How can I assist you today?",
    createSkillPrompt:
      "We're going to build a new skill step by step with `skill-creator`. To start, what do you want this skill to do?",
    addAttachments: "Add attachments",
    mode: "Mode",
    orchestrationMode: "Orchestration",
    flashMode: "Flash",
    flashModeDescription: "Fast and efficient, but may not be accurate",
    reasoningMode: "Reasoning",
    reasoningModeDescription:
      "Reasoning before action, balance between time and accuracy",
    proMode: "Pro",
    proModeDescription:
      "Reasoning, planning and executing, get more accurate results, may take more time",
    ultraMode: "Ultra",
    ultraModeDescription:
      "Pro mode with subagents to divide work; best for complex multi-step tasks",
    autoOrchestrationMode: "Auto",
    autoOrchestrationModeDescription:
      "Let the system choose between Leader and Workflow for this turn",
    leaderOrchestrationMode: "Leader",
    leaderOrchestrationModeDescription:
      "Best for open-ended, exploratory, or tool-centric tasks",
    workflowOrchestrationMode: "Workflow",
    workflowOrchestrationModeDescription:
      "Best for structured, multi-step, and recoverable tasks",
    reasoningEffort: "Reasoning Effort",
    reasoningEffortMinimal: "Minimal",
    reasoningEffortMinimalDescription: "Retrieval + Direct Output",
    reasoningEffortLow: "Low",
    reasoningEffortLowDescription: "Simple Logic Check + Shallow Deduction",
    reasoningEffortMedium: "Medium",
    reasoningEffortMediumDescription:
      "Multi-layer Logic Analysis + Basic Verification",
    reasoningEffortHigh: "High",
    reasoningEffortHighDescription:
      "Full-dimensional Logic Deduction + Multi-path Verification + Backward Check",
    searchModels: "Search models...",
    surpriseMe: "Surprise",
    surpriseMePrompt: "Surprise me",
    suggestions: [
      {
        suggestion: "Write",
        prompt: "Write a blog post about the latest trends on [topic]",
        icon: PenLineIcon,
      },
      {
        suggestion: "Research",
        prompt:
          "Conduct a deep dive research on [topic], and summarize the findings.",
        icon: MicroscopeIcon,
      },
      {
        suggestion: "Collect",
        prompt: "Collect data from [source] and create a report.",
        icon: ShapesIcon,
      },
      {
        suggestion: "Learn",
        prompt: "Learn about [topic] and create a tutorial.",
        icon: GraduationCapIcon,
      },
    ],
    suggestionsCreate: [
      {
        suggestion: "Webpage",
        prompt: "Create a webpage about [topic]",
        icon: CompassIcon,
      },
      {
        suggestion: "Image",
        prompt: "Create an image about [topic]",
        icon: ImageIcon,
      },
      {
        suggestion: "Video",
        prompt: "Create a video about [topic]",
        icon: VideoIcon,
      },
      {
        type: "separator",
      },
      {
        suggestion: "Skill",
        prompt:
          "We're going to build a new skill step by step with `skill-creator`. To start, what do you want this skill to do?",
        icon: SparklesIcon,
      },
    ],
  },

  // Sidebar
  sidebar: {
    newChat: "New chat",
    chats: "Chats",
    recentChats: "Recent chats",
    demoChats: "Demo chats",
    agents: "Agents",
    governance: "Governance",
  },

  // Agents
  agents: {
    title: "Agents",
    description:
      "Create and manage custom agents with specialized prompts and capabilities.",
    newAgent: "New Agent",
    emptyTitle: "No custom agents yet",
    emptyDescription:
      "Create your first custom agent with a specialized system prompt.",
    chat: "Chat",
    delete: "Delete",
    deleteConfirm:
      "Are you sure you want to delete this agent? This action cannot be undone.",
    deleteSuccess: "Agent deleted",
    newChat: "New chat",
    createPageTitle: "Design your Agent",
    createPageSubtitle:
      "Describe the agent you want — I'll help you create it through conversation.",
    nameStepTitle: "Name your new Agent",
    nameStepHint:
      "Letters, digits, and hyphens only — stored lowercase (e.g. code-reviewer)",
    nameStepPlaceholder: "e.g. code-reviewer",
    nameStepContinue: "Continue",
    nameStepInvalidError:
      "Invalid name — use only letters, digits, and hyphens",
    nameStepAlreadyExistsError: "An agent with this name already exists",
    nameStepCheckError: "Could not verify name availability — please try again",
    nameStepBootstrapMessage:
      "The new custom agent name is {name}. Let's bootstrap it's **SOUL**.",
    agentCreated: "Agent created!",
    startChatting: "Start chatting",
    backToGallery: "Back to Gallery",
  },

  // Breadcrumb
  breadcrumb: {
    workspace: "Workspace",
    chats: "Chats",
    governance: "Governance",
  },

  // Workspace
  workspace: {
    officialWebsite: "DeerFlow's official website",
    githubTooltip: "DeerFlow on Github",
    settingsAndMore: "Settings and more",
    visitGithub: "DeerFlow on GitHub",
    reportIssue: "Report a issue",
    contactUs: "Contact us",
    about: "About DeerFlow",
  },

  // Conversation
  conversation: {
    noMessages: "No messages yet",
    startConversation: "Start a conversation to see messages here",
  },

  // Chats
  chats: {
    searchChats: "Search chats",
  },

  governance: {
    title: "Governance Console",
    description:
      "Review pending interventions, inspect audit history, and resolve operator actions from the governance ledger.",
    queueTab: "Queue",
    historyTab: "History",
    refresh: "Refresh",
    queueCount: (count: number) => `${count} pending`,
    historyCount: (count: number) => `${count} records`,
    filters: {
      risk: "Risk",
      status: "Status",
      agent: "Agent",
      threadId: "Thread ID",
      runId: "Run ID",
      dateFrom: "From",
      dateTo: "To",
      allRisks: "All risks",
      allStatuses: "All statuses",
      allAgents: "All agents",
      reset: "Reset filters",
    },
    labels: {
      sourceAgent: "Source agent",
      createdAt: "Created",
      resolvedAt: "Resolved",
      status: "Status",
      risk: "Risk",
      category: "Category",
      hook: "Hook",
      thread: "Thread",
      run: "Run",
      task: "Task",
      request: "Request",
      fingerprint: "Fingerprint",
      tool: "Tool",
      actionSummary: "Action summary",
      riskTip: "Risk tip",
      reason: "Reason",
      detail: "Detail",
      operatorAction: "Operator action",
    },
    actions: {
      openThread: "Open thread",
      approve: "Approve",
      resolve: "Resolve",
    },
    states: {
      loadingQueue: "Loading queue...",
      loadingHistory: "Loading history...",
      loadingDetail: "Loading detail...",
      selectQueueItem: "Select a pending governance item to inspect and resolve.",
      selectHistoryItem: "Select a resolved governance item to inspect its audit detail.",
      emptyQueueTitle: "Queue is clear",
      emptyQueueDescription:
        "No pending governance items match the current filters.",
      emptyHistoryTitle: "No history records",
      emptyHistoryDescription:
        "No resolved governance items match the current filters.",
      noActionSchema: "No operator action schema is available for this item.",
    },
    result: {
      success: "Governance action submitted",
      resumeFailed:
        "The governance decision was saved, but workflow continuation did not resume automatically.",
      stale: "This governance item is stale. Refresh and try again.",
      invalid: "The submitted governance payload is invalid.",
      failed: "Failed to submit governance action.",
    },
    statusText: {
      pending_intervention: "Pending",
      resolved: "Resolved",
      rejected: "Rejected",
      failed: "Failed",
      expired: "Expired",
      decided: "Decided",
    },
  },

  // Page titles (document title)
  pages: {
    appName: "DeerFlow",
    chats: "Chats",
    governance: "Governance",
    newChat: "New chat",
    untitled: "Untitled",
  },

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => `${count} more step${count === 1 ? "" : "s"}`,
    lessSteps: "Less steps",
    executeCommand: "Execute command",
    presentFiles: "Present files",
    needYourHelp: "Need your help",
    useTool: (toolName: string) => `Use "${toolName}" tool`,
    searchFor: (query: string) => `Search for "${query}"`,
    searchForRelatedInfo: "Search for related information",
    searchForRelatedImages: "Search for related images",
    searchForRelatedImagesFor: (query: string) =>
      `Search for related images for "${query}"`,
    searchOnWebFor: (query: string) => `Search on the web for "${query}"`,
    viewWebPage: "View web page",
    listFolder: "List folder",
    readFile: "Read file",
    writeFile: "Write file",
    clickToViewContent: "Click to view file content",
    writeTodos: "Update to-do list",
    skillInstallTooltip: "Install skill and make it available to DeerFlow",
  },

  // Subtasks
  uploads: {
    uploading: "Uploading...",
    uploadingFiles: "Uploading files, please wait...",
  },

  subtasks: {
    subtask: "Subtask",
    executing: (count: number) =>
      `Executing ${count === 1 ? "" : count + " "}subtask${count === 1 ? "" : "s in parallel"}`,
    pending: "Pending",
    in_progress: "Running subtask",
    waiting_dependency: "Waiting for dependency",
    waiting_intervention: "Waiting for your decision",
    waiting_clarification: "Waiting for clarification",
    completed: "Subtask completed",
    failed: "Subtask failed",
    errorGeneric: "Subtask error, please retry",
    statusDetail: {
      taskStarted: "Starting",
      dispatching: "Dispatching to agent",
      waitingDependency: "Waiting for related task",
      waitingClarification: "Waiting for more info",
      completed: "Completed",
      failed: "Error occurred",
      dependencyResolved: "Dependency ready, resuming",
      assigned: (agent: string) => `Assigned to ${agent}`,
      waitingHelper: (agent: string) => `Waiting for ${agent}`,
      retryingHelper: (agent: string) => `Retrying ${agent}`,
    },
    needCapability: (capability: string) => `Need capability: ${capability}`,
    requestedBy: (agent: string) => `Requested by: ${agent}`,
    resolvedInputs: "Resolved inputs",
    resumed: "Dependency resolved; resumed execution",
    interventionRisk: (riskLevel: string) => `Risk: ${riskLevel}`,
    interventionPlaceholder: "Add the information needed to continue",
    interventionSubmitted: "Decision submitted",
    interventionStale: "This intervention is no longer current",
    interventionInvalid: "Please complete the required input before submitting",
    interventionSubmitFailed: "Failed to submit your decision",
    interventionActionFallback: "Submit",
    interventionRequiredLabel: "Intervention Required",
    interventionNextActionLabel: "Next Action",
    interventionDecisionLabel: "Your Decision",
    interventionDetailsLabel: "Details",
    interventionDebugLabel: "Technical details",
    interventionDebugSummary: "Show internal details",
    interventionCopy: {
      confirmTitle: "Please confirm whether to continue",
      confirmHint: "After you confirm, we'll continue with the current task.",
      inputTitle: "Please provide the required information",
      singleSelectHint:
        "Choose one option. If none of them fit, you can also enter your own.",
      multiSelectHint:
        "Choose one or more options. If the list is incomplete, you can add your own items.",
      customSectionTitle: "Custom input",
      singleCustomPlaceholder: "Enter your choice",
      multiCustomPlaceholder:
        "Enter one item per line, or separate multiple custom items with commas",
      clarificationConfirmTitle: "Please confirm the information you provided",
      clarificationConfirmHint:
        "After you confirm, we'll continue using the information you provided.",
      clarificationSingleSelectHint:
        "Please provide or choose one item. If none of them fit, you can also enter your own.",
      clarificationMultiSelectHint:
        "Please provide any useful information. You can choose multiple items, or add your own if needed.",
      clarificationCustomSectionTitle: "Additional information",
      clarificationSubmitLabel: "Confirm and continue",
      nextStepLabel: "Next",
    },
  },

  workflowStatus: {
    initializing: "Planning",
    queued: "Queued and waiting to start...",
    acknowledged: "Workflow started, understanding your request...",
    planning: "Understanding your request, planning steps…",
    routing: "Plan ready, dispatching subtasks...",
    resuming: "Resuming previous progress…",
    processing: "Working on your request…",
    executing: "Subtasks are underway...",
    summarizing: "Tasks done, summarizing results…",
    waitingDependency: "Waiting for a related task to finish…",
    waitingIntervention: "Waiting for your decision",
    waitingClarification: "Need more information from you",
    stopped: "Conversation stopped",
    stoppedDescription: "This conversation was stopped manually.",
    running: (count: number) =>
      `Running ${count} subtask${count === 1 ? "" : "s"}`,
    completedSummary: (completed: number, total: number) =>
      `${completed} of ${total} done`,
  },

  // Settings
  settings: {
    title: "Settings",
    description: "Adjust how DeerFlow looks and behaves for you.",
    sections: {
      appearance: "Appearance",
      memory: "Memory",
      tools: "Tools",
      skills: "Skills",
      notification: "Notification",
      about: "About",
    },
    memory: {
      title: "Memory",
      description:
        "DeerFlow automatically learns from your conversations in the background. These memories help DeerFlow understand you better and deliver a more personalized experience.",
      empty: "No memory data to display.",
      rawJson: "Raw JSON",
      markdown: {
        overview: "Overview",
        userContext: "User context",
        work: "Work",
        personal: "Personal",
        topOfMind: "Top of mind",
        historyBackground: "History",
        recentMonths: "Recent months",
        earlierContext: "Earlier context",
        longTermBackground: "Long-term background",
        updatedAt: "Updated at",
        facts: "Facts",
        empty: "(empty)",
        table: {
          category: "Category",
          confidence: "Confidence",
          confidenceLevel: {
            veryHigh: "Very high",
            high: "High",
            normal: "Normal",
            unknown: "Unknown",
          },
          content: "Content",
          source: "Source",
          createdAt: "CreatedAt",
          view: "View",
        },
      },
    },
    appearance: {
      themeTitle: "Theme",
      themeDescription:
        "Choose how the interface follows your device or stays fixed.",
      system: "System",
      light: "Light",
      dark: "Dark",
      systemDescription: "Match the operating system preference automatically.",
      lightDescription: "Bright palette with higher contrast for daytime.",
      darkDescription: "Dim palette that reduces glare for focus.",
      languageTitle: "Language",
      languageDescription: "Switch between languages.",
    },
    tools: {
      title: "Tools",
      description: "Manage the configuration and enabled status of MCP tools.",
    },
    skills: {
      title: "Agent Skills",
      description:
        "Manage the configuration and enabled status of the agent skills.",
      createSkill: "Create skill",
      emptyTitle: "No agent skill yet",
      emptyDescription:
        "Put your agent skill folders under the `/skills/custom` folder under the root folder of DeerFlow.",
      emptyButton: "Create Your First Skill",
    },
    notification: {
      title: "Notification",
      description:
        "DeerFlow only sends a completion notification when the window is not active. This is especially useful for long-running tasks so you can switch to other work and get notified when done.",
      requestPermission: "Request notification permission",
      deniedHint:
        "Notification permission was denied. You can enable it in your browser's site settings to receive completion alerts.",
      testButton: "Send test notification",
      testTitle: "DeerFlow",
      testBody: "This is a test notification.",
      notSupported: "Your browser does not support notifications.",
      disableNotification: "Disable notification",
    },
    acknowledge: {
      emptyTitle: "Acknowledgements",
      emptyDescription: "Credits and acknowledgements will show here.",
    },
  },
};
