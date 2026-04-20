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

export const zhCN: Translations = {
  // Locale meta
  locale: {
    localName: "中文",
  },

  // Common
  common: {
    home: "首页",
    settings: "设置",
    delete: "删除",
    rename: "重命名",
    share: "分享",
    openInNewWindow: "在新窗口打开",
    close: "关闭",
    more: "更多",
    search: "搜索",
    download: "下载",
    thinking: "思考",
    artifacts: "文件",
    public: "公共",
    custom: "自定义",
    notAvailableInDemoMode: "在演示模式下不可用",
    loading: "加载中...",
    version: "版本",
    lastUpdated: "最后更新",
    code: "代码",
    preview: "预览",
    cancel: "取消",
    save: "保存",
    install: "安装",
    create: "创建",
  },

  // Welcome
  welcome: {
    greeting: "你好，欢迎回来！",
    description:
      "欢迎使用DeerFlow，一个完全开源的超级智能体。通过内置和自定义的 Skills，\nDeerFlow 可以帮你搜索网络、分析数据，还能为你生成幻灯片、\n图片、视频、播客及网页等，几乎可以做任何事情。",

    createYourOwnSkill: "创建你自己的 Agent SKill",
    createYourOwnSkillDescription:
      "创建你的 Agent Skill 来释放 DeerFlow 的潜力。通过自定义技能，DeerFlow\n可以帮你搜索网络、分析数据，还能为你生成幻灯片、\n网页等作品，几乎可以做任何事情。",
  },

  // Clipboard
  clipboard: {
    copyToClipboard: "复制到剪贴板",
    copiedToClipboard: "已复制到剪贴板",
    failedToCopyToClipboard: "复制到剪贴板失败",
    linkCopied: "链接已复制到剪贴板",
  },

  // Input Box
  inputBox: {
    placeholder: "今天我能为你做些什么？",
    createSkillPrompt:
      "我们一起用 skill-creator 技能来创建一个技能吧。先问问我希望这个技能能做什么。",
    addAttachments: "添加附件",
    mode: "模式",
    orchestrationMode: "编排",
    flashMode: "闪速",
    flashModeDescription: "快速且高效的完成任务，但可能不够精准",
    reasoningMode: "思考",
    reasoningModeDescription: "思考后再行动，在时间与准确性之间取得平衡",
    proMode: "Pro",
    proModeDescription: "思考、计划再执行，获得更精准的结果，可能需要更多时间",
    ultraMode: "Ultra",
    ultraModeDescription:
      "继承自 Pro 模式，可调用子代理分工协作，适合复杂多步骤任务，能力最强",
    autoOrchestrationMode: "自动",
    autoOrchestrationModeDescription: "由系统为本轮自动选择 Leader 或 Workflow",
    leaderOrchestrationMode: "Leader",
    leaderOrchestrationModeDescription: "适合开放式、探索式或工具驱动任务",
    workflowOrchestrationMode: "Workflow",
    workflowOrchestrationModeDescription: "适合结构化、多步骤、可恢复任务",
    reasoningEffort: "推理深度",
    reasoningEffortMinimal: "最低",
    reasoningEffortMinimalDescription: "检索 + 直接输出",
    reasoningEffortLow: "低",
    reasoningEffortLowDescription: "简单逻辑校验 + 浅层推演",
    reasoningEffortMedium: "中",
    reasoningEffortMediumDescription: "多层逻辑分析 + 基础验证",
    reasoningEffortHigh: "高",
    reasoningEffortHighDescription: "全维度逻辑推演 + 多路径验证 + 反推校验",
    searchModels: "搜索模型...",
    surpriseMe: "小惊喜",
    surpriseMePrompt: "给我一个小惊喜吧",
    suggestions: [
      {
        suggestion: "写作",
        prompt: "撰写一篇关于[主题]的博客文章",
        icon: PenLineIcon,
      },
      {
        suggestion: "研究",
        prompt: "深入浅出的研究一下[主题]，并总结发现。",
        icon: MicroscopeIcon,
      },
      {
        suggestion: "收集",
        prompt: "从[来源]收集数据并创建报告。",
        icon: ShapesIcon,
      },
      {
        suggestion: "学习",
        prompt: "学习关于[主题]并创建教程。",
        icon: GraduationCapIcon,
      },
    ],
    suggestionsCreate: [
      {
        suggestion: "网页",
        prompt: "生成一个关于[主题]的网页",
        icon: CompassIcon,
      },
      {
        suggestion: "图片",
        prompt: "生成一个关于[主题]的图片",
        icon: ImageIcon,
      },
      {
        suggestion: "视频",
        prompt: "生成一个关于[主题]的视频",
        icon: VideoIcon,
      },
      {
        type: "separator",
      },
      {
        suggestion: "技能",
        prompt:
          "我们一起用 skill-creator 技能来创建一个技能吧。先问问我希望这个技能能做什么。",
        icon: SparklesIcon,
      },
    ],
  },

  // Sidebar
  sidebar: {
    newChat: "新对话",
    chats: "对话",
    recentChats: "最近的对话",
    demoChats: "演示对话",
    agents: "智能体",
    governance: "治理台",
  },

  // Agents
  agents: {
    title: "智能体",
    description: "创建和管理具有专属 Prompt 与能力的自定义智能体。",
    newAgent: "新建智能体",
    emptyTitle: "还没有自定义智能体",
    emptyDescription: "创建你的第一个自定义智能体，设置专属系统提示词。",
    chat: "对话",
    delete: "删除",
    deleteConfirm: "确定要删除该智能体吗？此操作不可撤销。",
    deleteSuccess: "智能体已删除",
    newChat: "新对话",
    createPageTitle: "设计你的智能体",
    createPageSubtitle: "描述你想要的智能体，我来帮你通过对话创建。",
    nameStepTitle: "给新智能体起个名字",
    nameStepHint:
      "只允许字母、数字和连字符，存储时自动转为小写（例如 code-reviewer）",
    nameStepPlaceholder: "例如 code-reviewer",
    nameStepContinue: "继续",
    nameStepInvalidError: "名称无效，只允许字母、数字和连字符",
    nameStepAlreadyExistsError: "已存在同名智能体",
    nameStepCheckError: "无法验证名称可用性，请稍后重试",
    nameStepBootstrapMessage:
      "新智能体的名称是 {name}，现在开始为它生成 **SOUL**。",
    agentCreated: "智能体已创建！",
    startChatting: "开始对话",
    backToGallery: "返回 Gallery",
  },

  // Breadcrumb
  breadcrumb: {
    workspace: "工作区",
    chats: "对话",
    governance: "治理台",
  },

  // Workspace
  workspace: {
    officialWebsite: "访问 DeerFlow 官方网站",
    githubTooltip: "访问 DeerFlow 的 Github 仓库",
    settingsAndMore: "设置和更多",
    visitGithub: "在 Github 上查看 DeerFlow",
    reportIssue: "报告问题",
    contactUs: "联系我们",
    about: "关于 DeerFlow",
  },

  // Conversation
  conversation: {
    noMessages: "还没有消息",
    startConversation: "开始新的对话以查看消息",
  },

  // Chats
  chats: {
    searchChats: "搜索对话",
  },

  governance: {
      title: "治理操作台",
      description:
        "集中查看真正需要额外处理的治理任务和审计记录。普通对话中的即时补充信息通常仍建议回到原对话完成。",
      queueTab: "待处理任务",
      historyTab: "处理记录",
      refresh: "刷新",
      queueCount: (count: number) => `${count} 条待处理任务`,
      historyCount: (count: number) => `${count} 条处理记录`,
      filters: {
        risk: "风险等级",
        status: "状态",
        agent: "来源 Agent",
      threadId: "Thread ID",
      runId: "Run ID",
      dateFrom: "开始日期",
      dateTo: "结束日期",
      allRisks: "全部风险",
      allStatuses: "全部状态",
      allAgents: "全部 Agent",
      reset: "重置筛选",
    },
      labels: {
        sourceAgent: "来源 Agent",
        createdAt: "创建时间",
        resolvedAt: "处理时间",
        status: "状态",
        risk: "风险",
        eventType: "事件类型",
        currentSituation: "当前情况",
        nextStep: "建议处理",
        relatedContext: "关联信息",
        technicalDetail: "技术细节",
        category: "分类",
        hook: "Hook",
        thread: "Thread",
        run: "Run",
        task: "Task",
      request: "Request",
      fingerprint: "Fingerprint",
      tool: "工具",
      actionSummary: "动作摘要",
      riskTip: "风险提示",
      reason: "原因",
      detail: "详情",
      operatorAction: "处理动作",
    },
      actions: {
        openThread: "打开对话",
        approve: "批准",
        resolve: "提交处理",
        technicalDetail: "查看技术细节",
      },
      states: {
        loadingQueue: "正在加载待处理队列...",
        loadingHistory: "正在加载处理历史...",
        loadingDetail: "正在加载详情...",
        selectQueueItem: "选择一条待处理任务，查看发生了什么以及该如何处理。",
        selectHistoryItem: "选择一条历史记录，查看对应的处理结果和审计上下文。",
        emptyQueueTitle: "当前没有待处理任务",
        emptyQueueDescription: "当前筛选条件下没有需要额外处理的治理任务。",
        emptyHistoryTitle: "当前没有历史记录",
        emptyHistoryDescription: "当前筛选条件下没有处理历史记录。",
        noActionSchema:
          "该事项当前不支持在治理台直接处理，请打开原对话继续补充或确认。",
      },
      result: {
        success: "治理处理已提交",
        resumeFailed: "治理决策已保存，但工作流续跑未能自动触发。",
        stale: "该治理项已过期，请刷新后重试。",
        invalid: "提交的治理处理参数无效。",
        failed: "提交治理处理失败。",
      },
      kindText: {
        clarification: "补充信息",
        dependency: "等待协助",
        approval: "人工审批",
        review: "审计记录",
      },
      guidance: {
        resolveInConsole: "可直接在下方提交处理；提交成功后，系统会继续后续流程。",
        continueInThread: "当前治理台没有可直接执行的处理动作，请打开原对话继续补充或确认。",
        auditOnly: "这条事项已经处理完成，当前主要用于审计追踪和结果复盘。",
        noReason: "后端没有提供更详细的原因说明。",
        noActionSummary: "后端没有提供更详细的动作摘要。",
      },
      readable: {
        titleClarification: (agent: string) => `${agent} 需要补充信息`,
        titleDependency: (agent: string) => `${agent} 正在等待协助`,
        titleApproval: (agent: string) => `${agent} 等待人工处理`,
        titleReview: (agent: string) => `${agent} 的治理记录`,
        summaryClarification: (agent: string) =>
          `${agent} 当前信息不足，任务已暂停，等待补充信息后继续执行。`,
        summaryDependency: (agent: string) =>
          `${agent} 依赖外部协助或上游结果，当前任务暂时挂起。`,
        summaryApproval: (agent: string) =>
          `${agent} 的下一步动作需要人工确认后才能继续执行。`,
        summaryReview: (agent: string) =>
          `${agent} 的这次人工介入已经形成审计记录，可用于追踪和复盘。`,
        situationClarification: (agent: string) =>
          `${agent} 发起了一次补充信息请求。只有补充完必要信息，当前任务才会继续。`,
        situationDependency: (agent: string) =>
          `${agent} 当前还不能独立完成这一步，正在等待外部协助、上游结果或额外输入。`,
        situationApproval: (agent: string) =>
          `${agent} 的下一步动作被治理策略拦下，需要人工确认后才能继续执行。`,
        situationReview: (agent: string) =>
          `${agent} 的这次人工介入已经结束，当前页面展示的是处理结果和审计上下文。`,
      },
      statusText: {
        pending_intervention: "待处理",
        resolved: "已处理",
      rejected: "已拒绝",
      failed: "失败",
      expired: "已过期",
      decided: "已决策",
    },
  },

  // Page titles (document title)
  pages: {
    appName: "LaiFuAI",
    chats: "对话",
    governance: "治理台",
    newChat: "新对话",
    untitled: "未命名",
  },

  // Tool calls
  toolCalls: {
    moreSteps: (count: number) => `查看其他 ${count} 个步骤`,
    lessSteps: "隐藏步骤",
    executeCommand: "执行命令",
    presentFiles: "展示文件",
    needYourHelp: "需要你的协助",
    useTool: (toolName: string) => `使用 “${toolName}” 工具`,
    searchFor: (query: string) => `搜索 “${query}”`,
    searchForRelatedInfo: "搜索相关信息",
    searchForRelatedImages: "搜索相关图片",
    searchForRelatedImagesFor: (query: string) => `搜索相关图片 “${query}”`,
    searchOnWebFor: (query: string) => `在网络上搜索 “${query}”`,
    viewWebPage: "查看网页",
    listFolder: "列出文件夹",
    readFile: "读取文件",
    writeFile: "写入文件",
    clickToViewContent: "点击查看文件内容",
    writeTodos: "更新 To-do 列表",
    skillInstallTooltip: "安装技能并使其可在 DeerFlow 中使用",
  },

  uploads: {
    uploading: "上传中...",
    uploadingFiles: "文件上传中，请稍候...",
  },

  subtasks: {
    subtask: "子任务",
    executing: (count: number) =>
      `${count > 1 ? "并行" : ""}执行 ${count} 个子任务`,
    pending: "待处理",
    in_progress: "子任务运行中",
    waiting_dependency: "等待依赖",
    waiting_intervention: "等待你的决策",
    waiting_clarification: "等待澄清",
    completed: "子任务已完成",
    failed: "子任务失败",
    errorGeneric: "任务执行出错，请重试",
    statusDetail: {
      taskStarted: "开始执行",
      dispatching: "正在分发给执行者",
      waitingDependency: "等待相关任务完成",
      waitingClarification: "等待补充信息",
      completed: "已完成",
      failed: "执行出错",
      dependencyResolved: "依赖已就绪，继续执行",
      assigned: (agent: string) => `已分配给 ${agent}`,
      waitingHelper: (agent: string) => `等待 ${agent} 协助`,
      retryingHelper: (agent: string) => `正在重试 ${agent} 协助`,
    },
    needCapability: (capability: string) => `所需能力：${capability}`,
    requestedBy: (agent: string) => `求助来源：${agent}`,
    resolvedInputs: "已解析的依赖结果",
    resumed: "依赖已解析，任务继续执行",
    interventionRisk: (riskLevel: string) => `风险：${riskLevel}`,
    interventionPlaceholder: "请输入继续执行所需的补充信息",
    interventionSubmitted: "已提交决策",
    interventionStale: "当前干预请求已过期",
    interventionInvalid: "请先补全必填信息再提交",
    interventionSubmitFailed: "提交决策失败",
    interventionActionFallback: "提交",
    interventionRequiredLabel: "需要你的决策",
    interventionNextActionLabel: "下一步动作",
    interventionDecisionLabel: "请做出选择",
    interventionDetailsLabel: "详细信息",
    interventionDebugLabel: "技术细节",
    interventionDebugSummary: "查看内部详情",
    interventionCopy: {
      confirmTitle: "请确认是否继续",
      confirmHint: "确认后，我们会继续执行当前任务。",
      inputTitle: "请补充必要信息",
      singleSelectHint: "请选择一项；如果列表里没有合适的内容，也可以直接填写。",
      multiSelectHint: "可选择多项；如果列表里没有合适的内容，也可以手动补充。",
      customSectionTitle: "自定义补充",
      singleCustomPlaceholder: "请输入你的选择",
      multiCustomPlaceholder: "可逐行输入，或用逗号分隔多个自定义选项",
      clarificationConfirmTitle: "请确认补充信息",
      clarificationConfirmHint: "确认后，我们会根据你补充的信息继续执行。",
      clarificationSingleSelectHint: "请补充或选择一项信息；如果列表里没有合适的内容，也可以直接填写。",
      clarificationMultiSelectHint: "请补充可用的信息，可选择多项；如果列表里没有合适的内容，也可以手动补充。",
      clarificationCustomSectionTitle: "补充信息",
      clarificationSubmitLabel: "确认并继续",
      nextStepLabel: "下一步",
    },
  },

  workflowStatus: {
    initializing: "任务规划中",
    queued: "已提交，正在排队启动...",
    acknowledged: "已进入工作流，正在理解需求...",
    planning: "正在理解你的需求，规划执行步骤…",
    routing: "计划已生成，正在分发子任务...",
    resuming: "正在恢复之前的任务进度…",
    processing: "正在处理你的请求…",
    executing: "子任务已开始执行...",
    summarizing: "任务已完成，正在汇总结果…",
    waitingDependency: "正在等待相关任务完成…",
    waitingIntervention: "等待你的决策",
    waitingClarification: "需要你提供更多信息",
    stopped: "对话已终止",
    stoppedDescription: "当前对话已被手动终止。",
    running: (count: number) => `正在执行 ${count} 个子任务`,
    completedSummary: (completed: number, total: number) =>
      `已完成 ${completed} / ${total}`,
  },

  // Settings
  settings: {
    title: "设置",
    description: "根据你的偏好调整 DeerFlow 的界面和行为。",
    sections: {
      appearance: "外观",
      memory: "记忆",
      tools: "工具",
      skills: "技能",
      notification: "通知",
      about: "关于",
    },
    memory: {
      title: "记忆",
      description:
        "DeerFlow 会在后台不断从你的对话中自动学习。这些记忆能帮助 DeerFlow 更好地理解你，并提供更个性化的体验。",
      empty: "暂无可展示的记忆数据。",
      rawJson: "原始 JSON",
      markdown: {
        overview: "概览",
        userContext: "用户上下文",
        work: "工作",
        personal: "个人",
        topOfMind: "近期关注（Top of mind）",
        historyBackground: "历史背景",
        recentMonths: "近几个月",
        earlierContext: "更早上下文",
        longTermBackground: "长期背景",
        updatedAt: "更新于",
        facts: "事实",
        empty: "（空）",
        table: {
          category: "类别",
          confidence: "置信度",
          confidenceLevel: {
            veryHigh: "极高",
            high: "较高",
            normal: "一般",
            unknown: "未知",
          },
          content: "内容",
          source: "来源",
          createdAt: "创建时间",
          view: "查看",
        },
      },
    },
    appearance: {
      themeTitle: "主题",
      themeDescription: "跟随系统或选择固定的界面模式。",
      system: "系统",
      light: "浅色",
      dark: "深色",
      systemDescription: "自动跟随系统主题。",
      lightDescription: "更明亮的配色，适合日间使用。",
      darkDescription: "更暗的配色，减少眩光方便专注。",
      languageTitle: "语言",
      languageDescription: "在不同语言之间切换。",
    },
    tools: {
      title: "工具",
      description: "管理 MCP 工具的配置和启用状态。",
    },
    skills: {
      title: "技能",
      description: "管理 Agent Skill 配置和启用状态。",
      createSkill: "新建技能",
      emptyTitle: "还没有技能",
      emptyDescription:
        "将你的 Agent Skill 文件夹放在 DeerFlow 根目录下的 `/skills/custom` 文件夹中。",
      emptyButton: "创建你的第一个技能",
    },
    notification: {
      title: "通知",
      description:
        "DeerFlow 只会在窗口不活跃时发送完成通知，特别适合长时间任务：你可以先去做别的事，完成后会收到提醒。",
      requestPermission: "请求通知权限",
      deniedHint:
        "通知权限已被拒绝。可在浏览器的网站设置中重新开启，以接收完成提醒。",
      testButton: "发送测试通知",
      testTitle: "DeerFlow",
      testBody: "这是一条测试通知。",
      notSupported: "当前浏览器不支持通知功能。",
      disableNotification: "关闭通知",
    },
    acknowledge: {
      emptyTitle: "致谢",
      emptyDescription: "相关的致谢信息会展示在这里。",
    },
  },

  // SSO
  sso: {
    callback: {
      pageTitle: "SSO 登录中",
      pending: "正在登录…",
      pendingHint: "正在为你校验登录凭证，请稍候。",
      invalidEntryTitle: "登录入口无效",
      invalidEntryDescription: "缺少登录参数，请从 moss-hub 重新进入。",
      expiredTitle: "登录链接已失效",
      expiredDescription: "此登录链接已失效或已被使用，请从 moss-hub 重新进入。",
      unavailableTitle: "SSO 服务暂不可用",
      unavailableDescription: "登录服务暂时不可用，请稍后从 moss-hub 重新进入。",
      networkTitle: "网络异常",
      networkDescription: "无法连接到登录服务，请检查网络后从 moss-hub 重新进入。",
      backToMossHubHint: "请回到 moss-hub 重新点击入口，不要手动刷新本页。",
    },
  },
};
