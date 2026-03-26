"use client";

import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangleIcon,
  ExternalLinkIcon,
  Loader2Icon,
  RefreshCwIcon,
  ShieldCheckIcon,
} from "lucide-react";
import Link from "next/link";
import type { Dispatch, SetStateAction } from "react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs";
import {
  WorkspaceBody,
  WorkspaceContainer,
  WorkspaceHeader,
} from "@/components/workspace/workspace-container";
import {
  getGovernanceDisplaySummary,
  getGovernanceDisplayTitle,
  pathOfGovernanceThread,
  resumeGovernanceThread,
  toGovernanceFilterEndISO,
  toGovernanceFilterStartISO,
  type GovernanceItem,
  useGovernanceDetail,
  useGovernanceHistory,
  useGovernanceQueue,
  useResolveGovernanceItem,
} from "@/core/governance";
import { useI18n } from "@/core/i18n/hooks";
import { useLocalSettings } from "@/core/settings";
import { formatTimeAgo } from "@/core/utils/datetime";
import { cn } from "@/lib/utils";

import { GovernanceActionPanel } from "./governance-action-panel";

type ConsoleTab = "queue" | "history";

type QueueFilterState = {
  riskLevel: string;
  sourceAgent: string;
  threadId: string;
  runId: string;
};

type HistoryFilterState = QueueFilterState & {
  status: string;
  dateFrom: string;
  dateTo: string;
};

const DEFAULT_QUEUE_FILTERS: QueueFilterState = {
  riskLevel: "all",
  sourceAgent: "all",
  threadId: "",
  runId: "",
};

const DEFAULT_HISTORY_FILTERS: HistoryFilterState = {
  ...DEFAULT_QUEUE_FILTERS,
  status: "all",
  dateFrom: "",
  dateTo: "",
};

const EMPTY_GOVERNANCE_ITEMS: GovernanceItem[] = [];
const QUEUE_LIMIT = 100;
const HISTORY_LIMIT = 200;

function getRiskBadgeClass(riskLevel: string | null | undefined) {
  switch (riskLevel) {
    case "critical":
      return "border-red-300/80 bg-red-500/10 text-red-700";
    case "high":
      return "border-amber-300/80 bg-amber-500/10 text-amber-700";
    case "medium":
      return "border-sky-300/80 bg-sky-500/10 text-sky-700";
    default:
      return "border-border bg-muted text-muted-foreground";
  }
}

function getStatusBadgeClass(status: string | null | undefined) {
  switch (status) {
    case "pending_intervention":
      return "border-sky-300/80 bg-sky-500/10 text-sky-700";
    case "resolved":
      return "border-emerald-300/80 bg-emerald-500/10 text-emerald-700";
    case "rejected":
      return "border-amber-300/80 bg-amber-500/10 text-amber-700";
    case "failed":
      return "border-red-300/80 bg-red-500/10 text-red-700";
    case "expired":
      return "border-zinc-300/80 bg-zinc-500/10 text-zinc-700";
    default:
      return "border-border bg-muted text-muted-foreground";
  }
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return "--";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString();
}

function MetaRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | null | undefined;
  mono?: boolean;
}) {
  return (
    <div className="rounded-xl border border-border/60 bg-muted/10 p-3">
      <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-muted-foreground">
        {label}
      </div>
      <div
        className={cn(
          "mt-1 break-all text-sm text-foreground",
          mono && "font-mono text-[12px]",
        )}
      >
        {value ?? "--"}
      </div>
    </div>
  );
}

function getStatusLabel(
  item: GovernanceItem,
  t: ReturnType<typeof useI18n>["t"],
) {
  const status = item.status as keyof typeof t.governance.statusText;
  return t.governance.statusText[status] ?? item.status;
}

function GovernanceList({
  items,
  selectedId,
  onSelect,
  t,
  emptyTitle,
  emptyDescription,
  loadingText,
  isLoading,
}: {
  items: GovernanceItem[];
  selectedId: string | null;
  onSelect: (governanceId: string) => void;
  t: ReturnType<typeof useI18n>["t"];
  emptyTitle: string;
  emptyDescription: string;
  loadingText: string;
  isLoading: boolean;
}) {
  if (isLoading && items.length === 0) {
    return (
      <div className="flex min-h-[280px] items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" />
        <span>{loadingText}</span>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <Empty className="min-h-[280px] border-border/70 bg-background">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ShieldCheckIcon />
          </EmptyMedia>
          <EmptyTitle>{emptyTitle}</EmptyTitle>
          <EmptyDescription>{emptyDescription}</EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="space-y-3 p-1">
        {items.map((item) => {
          const summary = getGovernanceDisplaySummary(item);
          const title = getGovernanceDisplayTitle(item) ?? item.governance_id;
          const isActive = item.governance_id === selectedId;

          return (
            <button
              key={item.governance_id}
              type="button"
              className={cn(
                "w-full rounded-2xl border p-4 text-left transition-colors",
                isActive
                  ? "border-primary bg-primary/5 shadow-sm"
                  : "border-border/70 bg-background hover:bg-muted/30",
              )}
              onClick={() => onSelect(item.governance_id)}
            >
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className={getRiskBadgeClass(item.risk_level)}>
                  {item.risk_level}
                </Badge>
                <Badge
                  variant="outline"
                  className={getStatusBadgeClass(item.status)}
                >
                  {getStatusLabel(item, t)}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {item.source_agent}
                </span>
              </div>
              <div className="mt-3 text-sm font-semibold text-foreground">
                {title}
              </div>
              {summary ? (
                <div className="mt-2 line-clamp-3 text-sm leading-6 text-muted-foreground">
                  {summary}
                </div>
              ) : null}
              <div className="mt-3 flex items-center justify-between gap-3 text-xs text-muted-foreground">
                <span>{formatTimeAgo(item.created_at)}</span>
                <span className="font-mono">{item.thread_id}</span>
              </div>
            </button>
          );
        })}
      </div>
    </ScrollArea>
  );
}

function QueueFilters({
  filters,
  setFilters,
  agentOptions,
  t,
}: {
  filters: QueueFilterState;
  setFilters: Dispatch<SetStateAction<QueueFilterState>>;
  agentOptions: string[];
  t: ReturnType<typeof useI18n>["t"];
}) {
  return (
    <div className="grid gap-3">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
        <Select
          value={filters.riskLevel}
          onValueChange={(value) =>
            setFilters((current) => ({ ...current, riskLevel: value }))
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder={t.governance.filters.risk} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t.governance.filters.allRisks}</SelectItem>
            <SelectItem value="medium">medium</SelectItem>
            <SelectItem value="high">high</SelectItem>
            <SelectItem value="critical">critical</SelectItem>
          </SelectContent>
        </Select>

        <Select
          value={filters.sourceAgent}
          onValueChange={(value) =>
            setFilters((current) => ({ ...current, sourceAgent: value }))
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder={t.governance.filters.agent} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t.governance.filters.allAgents}</SelectItem>
            {agentOptions.map((agent) => (
              <SelectItem key={agent} value={agent}>
                {agent}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
        <Input
          value={filters.threadId}
          placeholder={t.governance.filters.threadId}
          onChange={(event) =>
            setFilters((current) => ({ ...current, threadId: event.target.value }))
          }
        />
        <Input
          value={filters.runId}
          placeholder={t.governance.filters.runId}
          onChange={(event) =>
            setFilters((current) => ({ ...current, runId: event.target.value }))
          }
        />
      </div>

      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="justify-start px-0"
        onClick={() => setFilters(DEFAULT_QUEUE_FILTERS)}
      >
        {t.governance.filters.reset}
      </Button>
    </div>
  );
}

function HistoryFilters({
  filters,
  setFilters,
  agentOptions,
  t,
}: {
  filters: HistoryFilterState;
  setFilters: Dispatch<SetStateAction<HistoryFilterState>>;
  agentOptions: string[];
  t: ReturnType<typeof useI18n>["t"];
}) {
  return (
    <div className="grid gap-3">
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
        <Select
          value={filters.status}
          onValueChange={(value) =>
            setFilters((current) => ({ ...current, status: value }))
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder={t.governance.filters.status} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t.governance.filters.allStatuses}</SelectItem>
            <SelectItem value="resolved">resolved</SelectItem>
            <SelectItem value="rejected">rejected</SelectItem>
            <SelectItem value="failed">failed</SelectItem>
            <SelectItem value="expired">expired</SelectItem>
            <SelectItem value="decided">decided</SelectItem>
          </SelectContent>
        </Select>

        <Select
          value={filters.riskLevel}
          onValueChange={(value) =>
            setFilters((current) => ({ ...current, riskLevel: value }))
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder={t.governance.filters.risk} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t.governance.filters.allRisks}</SelectItem>
            <SelectItem value="medium">medium</SelectItem>
            <SelectItem value="high">high</SelectItem>
            <SelectItem value="critical">critical</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
        <Select
          value={filters.sourceAgent}
          onValueChange={(value) =>
            setFilters((current) => ({ ...current, sourceAgent: value }))
          }
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder={t.governance.filters.agent} />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">{t.governance.filters.allAgents}</SelectItem>
            {agentOptions.map((agent) => (
              <SelectItem key={agent} value={agent}>
                {agent}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Input
          value={filters.threadId}
          placeholder={t.governance.filters.threadId}
          onChange={(event) =>
            setFilters((current) => ({ ...current, threadId: event.target.value }))
          }
        />
      </div>

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-1">
        <Input
          value={filters.runId}
          placeholder={t.governance.filters.runId}
          onChange={(event) =>
            setFilters((current) => ({ ...current, runId: event.target.value }))
          }
        />
        <div className="grid gap-3 md:grid-cols-2">
          <Input
            type="date"
            value={filters.dateFrom}
            onChange={(event) =>
              setFilters((current) => ({ ...current, dateFrom: event.target.value }))
            }
          />
          <Input
            type="date"
            value={filters.dateTo}
            onChange={(event) =>
              setFilters((current) => ({ ...current, dateTo: event.target.value }))
            }
          />
        </div>
      </div>

      <div className="space-y-2">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="justify-start px-0"
          onClick={() => setFilters(DEFAULT_HISTORY_FILTERS)}
        >
          {t.governance.filters.reset}
        </Button>
      </div>
    </div>
  );
}

function GovernanceDetail({
  item,
  t,
  showActions,
  isResolving,
  onResolve,
  isLoadingDetail,
}: {
  item: GovernanceItem | null;
  t: ReturnType<typeof useI18n>["t"];
  showActions: boolean;
  isResolving: boolean;
  onResolve: (
    actionKey: string,
    payload: Record<string, unknown>,
    fingerprint?: string,
  ) => Promise<void>;
  isLoadingDetail: boolean;
}) {
  if (!item) {
    return (
      <Empty className="m-4 flex-1 border-border/70 bg-background">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <ShieldCheckIcon />
          </EmptyMedia>
          <EmptyTitle>
            {showActions
              ? t.governance.labels.operatorAction
              : t.governance.historyTab}
          </EmptyTitle>
          <EmptyDescription>
            {showActions
              ? t.governance.states.selectQueueItem
              : t.governance.states.selectHistoryItem}
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    );
  }

  return (
    <ScrollArea className="min-h-0 flex-1">
      <div className="space-y-4 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3 rounded-2xl border border-border/70 bg-muted/10 p-4">
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline" className={getRiskBadgeClass(item.risk_level)}>
                {item.risk_level}
              </Badge>
              <Badge variant="outline" className={getStatusBadgeClass(item.status)}>
                {getStatusLabel(item, t)}
              </Badge>
              <span className="text-sm text-muted-foreground">{item.source_agent}</span>
            </div>
            <div className="text-xl font-semibold text-foreground">
              {getGovernanceDisplayTitle(item) ?? item.governance_id}
            </div>
            {getGovernanceDisplaySummary(item) ? (
              <div className="max-w-3xl text-sm leading-7 text-muted-foreground">
                {getGovernanceDisplaySummary(item)}
              </div>
            ) : null}
          </div>
          <Button variant="outline" asChild>
            <Link href={pathOfGovernanceThread(item)}>
              <ExternalLinkIcon />
              {t.governance.actions.openThread}
            </Link>
          </Button>
        </div>

        {isLoadingDetail ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2Icon className="size-4 animate-spin" />
            {t.governance.states.loadingDetail}
          </div>
        ) : null}

        <div className="grid gap-3 lg:grid-cols-2 xl:grid-cols-3">
          <MetaRow
            label={t.governance.labels.createdAt}
            value={formatTimestamp(item.created_at)}
          />
          <MetaRow
            label={t.governance.labels.resolvedAt}
            value={formatTimestamp(item.resolved_at)}
          />
          <MetaRow
            label={t.governance.labels.status}
            value={getStatusLabel(item, t)}
          />
          <MetaRow label={t.governance.labels.category} value={item.category} />
          <MetaRow label={t.governance.labels.hook} value={item.hook_name} />
          <MetaRow label={t.governance.labels.tool} value={item.intervention_tool_name} />
          <MetaRow label={t.governance.labels.thread} value={item.thread_id} mono />
          <MetaRow label={t.governance.labels.run} value={item.run_id} mono />
          <MetaRow label={t.governance.labels.task} value={item.task_id} mono />
          <MetaRow label={t.governance.labels.request} value={item.request_id} mono />
          <MetaRow
            label={t.governance.labels.fingerprint}
            value={item.intervention_fingerprint}
            mono
          />
          <MetaRow label={t.governance.labels.sourceAgent} value={item.source_agent} />
        </div>

        {item.intervention_display?.sections?.length ? (
          <Card className="border-border/70 bg-background/80 py-0">
            <CardHeader>
              <CardTitle className="text-base">{t.governance.labels.detail}</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {item.intervention_display.sections.map((section, index) => (
                <div
                  key={`${section.title ?? "section"}-${index}`}
                  className="space-y-3 rounded-xl border border-border/60 bg-muted/12 p-3"
                >
                  {section.title ? (
                    <div className="text-sm font-semibold text-foreground">
                      {section.title}
                    </div>
                  ) : null}
                  <div className="grid gap-3 lg:grid-cols-2">
                    {section.items.map((entry) => (
                      <MetaRow
                        key={`${entry.label}-${entry.value}`}
                        label={entry.label}
                        value={entry.value}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="border-border/70 bg-background/80 py-0">
            <CardHeader>
              <CardTitle className="text-base">{t.governance.labels.reason}</CardTitle>
            </CardHeader>
            <CardContent className="text-sm leading-7 text-muted-foreground">
              {item.reason ?? "--"}
            </CardContent>
          </Card>
          <Card className="border-border/70 bg-background/80 py-0">
            <CardHeader>
              <CardTitle className="text-base">
                {t.governance.labels.actionSummary}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm leading-7 text-muted-foreground">
              {item.action_summary ?? "--"}
            </CardContent>
          </Card>
        </div>

        {item.intervention_display?.risk_tip ? (
          <Card className="border-amber-300/60 bg-amber-500/5 py-0">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <AlertTriangleIcon className="size-4 text-amber-600" />
                {t.governance.labels.riskTip}
              </CardTitle>
            </CardHeader>
            <CardContent className="text-sm leading-7 text-amber-900/80">
              {item.intervention_display.risk_tip}
            </CardContent>
          </Card>
        ) : null}

        {showActions ? (
          <Card className="border-border/70 bg-background/80 py-0">
            <CardHeader>
              <CardTitle className="text-base">
                {t.governance.labels.operatorAction}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <GovernanceActionPanel
                item={item}
                isPending={isResolving}
                onSubmit={onResolve}
              />
            </CardContent>
          </Card>
        ) : null}
      </div>
    </ScrollArea>
  );
}

export function GovernanceConsole() {
  const queryClient = useQueryClient();
  const { t } = useI18n();
  const [settings] = useLocalSettings();
  const [activeTab, setActiveTab] = useState<ConsoleTab>("queue");
  const [queueFilters, setQueueFilters] =
    useState<QueueFilterState>(DEFAULT_QUEUE_FILTERS);
  const [historyFilters, setHistoryFilters] =
    useState<HistoryFilterState>(DEFAULT_HISTORY_FILTERS);
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(null);
  const [selectedHistoryId, setSelectedHistoryId] = useState<string | null>(null);

  useEffect(() => {
    document.title = `${t.pages.governance} - ${t.pages.appName}`;
  }, [t.pages.appName, t.pages.governance]);

  const queueQuery = useGovernanceQueue({
    limit: QUEUE_LIMIT,
    riskLevel:
      queueFilters.riskLevel === "all" ? undefined : queueFilters.riskLevel,
    sourceAgent:
      queueFilters.sourceAgent === "all" ? undefined : queueFilters.sourceAgent,
    threadId: queueFilters.threadId || undefined,
    runId: queueFilters.runId || undefined,
  });
  const historyQuery = useGovernanceHistory({
    limit: HISTORY_LIMIT,
    riskLevel:
      historyFilters.riskLevel === "all" ? undefined : historyFilters.riskLevel,
    sourceAgent:
      historyFilters.sourceAgent === "all"
        ? undefined
        : historyFilters.sourceAgent,
    status: historyFilters.status === "all" ? undefined : historyFilters.status,
    threadId: historyFilters.threadId || undefined,
    runId: historyFilters.runId || undefined,
    resolvedFrom: toGovernanceFilterStartISO(historyFilters.dateFrom),
    resolvedTo: toGovernanceFilterEndISO(historyFilters.dateTo),
  });
  const resolveMutation = useResolveGovernanceItem();

  const queueItems = queueQuery.data?.items ?? EMPTY_GOVERNANCE_ITEMS;
  const rawHistoryItems = historyQuery.data?.items ?? EMPTY_GOVERNANCE_ITEMS;
  const historyItems = rawHistoryItems;

  useEffect(() => {
    if (queueItems.length === 0) {
      if (selectedQueueId !== null) {
        setSelectedQueueId(null);
      }
      return;
    }
    if (!queueItems.some((item) => item.governance_id === selectedQueueId)) {
      setSelectedQueueId(queueItems[0]?.governance_id ?? null);
    }
  }, [queueItems, selectedQueueId]);

  useEffect(() => {
    if (historyItems.length === 0) {
      if (selectedHistoryId !== null) {
        setSelectedHistoryId(null);
      }
      return;
    }
    if (!historyItems.some((item) => item.governance_id === selectedHistoryId)) {
      setSelectedHistoryId(historyItems[0]?.governance_id ?? null);
    }
  }, [historyItems, selectedHistoryId]);

  const selectedId =
    activeTab === "queue" ? selectedQueueId : selectedHistoryId;
  const selectedListItem =
    (activeTab === "queue" ? queueItems : historyItems).find(
      (item) => item.governance_id === selectedId,
    ) ?? null;
  const detailQuery = useGovernanceDetail(selectedId);
  const detailItem = detailQuery.data ?? selectedListItem;

  const agentOptions = useMemo(
    () =>
      Array.from(
        new Set(
          [...queueItems, ...rawHistoryItems]
            .map((item) => item.source_agent)
            .filter(Boolean),
        ),
      ).sort((left, right) => left.localeCompare(right)),
    [queueItems, rawHistoryItems],
  );

  const handleResolve = async (
    actionKey: string,
    payload: Record<string, unknown>,
    fingerprint?: string,
  ) => {
    if (!detailItem) {
      return;
    }

    try {
      const response = await resolveMutation.mutateAsync({
        governanceId: detailItem.governance_id,
        actionKey,
        payload,
        fingerprint,
      });

      if (
        response.resume_action === "submit_resume" &&
        typeof response.resume_payload?.message === "string" &&
        response.resume_payload.message.trim()
      ) {
        try {
          await resumeGovernanceThread(
            detailItem,
            settings.context,
            response.resume_payload.message,
          );
        } catch {
          toast.error(t.governance.result.resumeFailed);
        }
      }

      toast.success(t.governance.result.success);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["governance", "queue"] }),
        queryClient.invalidateQueries({ queryKey: ["governance", "history"] }),
        queryClient.invalidateQueries({
          queryKey: ["governance", "detail", detailItem.governance_id],
        }),
      ]);
    } catch (error) {
      const status =
        typeof error === "object" &&
        error !== null &&
        "status" in error &&
        typeof error.status === "number"
          ? error.status
          : undefined;

      if (status === 409) {
        toast.error(t.governance.result.stale);
        return;
      }
      if (status === 422) {
        toast.error(t.governance.result.invalid);
        return;
      }
      toast.error(t.governance.result.failed);
    }
  };

  const handleRefresh = async () => {
    await Promise.all([
      activeTab === "queue" ? queueQuery.refetch() : historyQuery.refetch(),
      selectedId ? detailQuery.refetch() : Promise.resolve(),
    ]);
  };

  return (
    <WorkspaceContainer>
      <WorkspaceHeader />
      <WorkspaceBody className="bg-muted/20">
        <div className="flex size-full flex-col gap-4 p-4 md:p-6">
          <Card className="border-border/70 bg-background/90">
            <CardHeader className="gap-4">
              <div className="space-y-1">
                <CardTitle className="text-xl">{t.governance.title}</CardTitle>
                <CardDescription className="max-w-3xl text-sm leading-6">
                  {t.governance.description}
                </CardDescription>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <Badge variant="outline" className="border-border/70 bg-muted/20">
                  {t.governance.queueCount(queueQuery.data?.total ?? 0)}
                </Badge>
                <Badge variant="outline" className="border-border/70 bg-muted/20">
                  {t.governance.historyCount(historyQuery.data?.total ?? 0)}
                </Badge>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => void handleRefresh()}
                >
                  <RefreshCwIcon />
                  {t.governance.refresh}
                </Button>
              </div>
            </CardHeader>
          </Card>

          <Tabs
            value={activeTab}
            onValueChange={(value) => setActiveTab(value as ConsoleTab)}
            className="min-h-0 flex-1"
          >
            <TabsList variant="line" className="w-fit">
              <TabsTrigger value="queue">{t.governance.queueTab}</TabsTrigger>
              <TabsTrigger value="history">{t.governance.historyTab}</TabsTrigger>
            </TabsList>

            <TabsContent value="queue" className="min-h-0">
              <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
                <Card className="min-h-[420px] border-border/70 bg-background/90 py-0">
                  <CardHeader className="border-b border-border/60 pb-4">
                    <QueueFilters
                      filters={queueFilters}
                      setFilters={setQueueFilters}
                      agentOptions={agentOptions}
                      t={t}
                    />
                  </CardHeader>
                  <CardContent className="flex min-h-0 flex-1 flex-col p-4">
                    <GovernanceList
                      items={queueItems}
                      selectedId={selectedQueueId}
                      onSelect={setSelectedQueueId}
                      t={t}
                      emptyTitle={t.governance.states.emptyQueueTitle}
                      emptyDescription={t.governance.states.emptyQueueDescription}
                      loadingText={t.governance.states.loadingQueue}
                      isLoading={queueQuery.isLoading}
                    />
                  </CardContent>
                </Card>

                <Card className="min-h-[420px] border-border/70 bg-background/90 py-0">
                  <CardContent className="flex min-h-[420px] flex-1 p-0">
                    <GovernanceDetail
                      item={detailItem}
                      t={t}
                      showActions
                      isResolving={resolveMutation.isPending}
                      onResolve={handleResolve}
                      isLoadingDetail={detailQuery.isFetching}
                    />
                  </CardContent>
                </Card>
              </div>
            </TabsContent>

            <TabsContent value="history" className="min-h-0">
              <div className="grid min-h-0 flex-1 gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
                <Card className="min-h-[420px] border-border/70 bg-background/90 py-0">
                  <CardHeader className="border-b border-border/60 pb-4">
                    <HistoryFilters
                      filters={historyFilters}
                      setFilters={setHistoryFilters}
                      agentOptions={agentOptions}
                      t={t}
                    />
                  </CardHeader>
                  <CardContent className="flex min-h-0 flex-1 flex-col p-4">
                    <GovernanceList
                      items={historyItems}
                      selectedId={selectedHistoryId}
                      onSelect={setSelectedHistoryId}
                      t={t}
                      emptyTitle={t.governance.states.emptyHistoryTitle}
                      emptyDescription={t.governance.states.emptyHistoryDescription}
                      loadingText={t.governance.states.loadingHistory}
                      isLoading={historyQuery.isLoading}
                    />
                  </CardContent>
                </Card>

                <Card className="min-h-[420px] border-border/70 bg-background/90 py-0">
                  <CardContent className="flex min-h-[420px] flex-1 p-0">
                    <GovernanceDetail
                      item={detailItem}
                      t={t}
                      showActions={false}
                      isResolving={false}
                      onResolve={handleResolve}
                      isLoadingDetail={detailQuery.isFetching}
                    />
                  </CardContent>
                </Card>
              </div>
            </TabsContent>
          </Tabs>
        </div>
      </WorkspaceBody>
    </WorkspaceContainer>
  );
}
