import { createContext, useCallback, useContext, useMemo, useState } from "react";

import type { TaskSource, TaskUpsert, TaskViewModel } from "./types";

interface TaskStoreState {
  tasksById: Record<string, TaskViewModel>;
  orderedTaskIds: string[];
}

export interface SubtaskContextValue extends TaskStoreState {
  tasks: Record<string, TaskViewModel>;
  hydrateTasks: (
    tasks: TaskViewModel[],
    options?: { source?: TaskSource; runId?: string | null },
  ) => void;
  upsertTask: (task: TaskUpsert) => void;
  removeTasksByRunId: (runId: string, source?: TaskSource) => void;
  resetTasksBySource: (source: TaskSource, runId?: string | null) => void;
  clearAllTasks: () => void;
}

const noop = () => {
  /* noop */
};

const TASK_STATUS_PRIORITY: Record<TaskViewModel["status"], number> = {
  pending: 0,
  in_progress: 1,
  waiting_dependency: 2,
  waiting_clarification: 3,
  waiting_intervention: 4,
  completed: 5,
  failed: 6,
};

export const SubtaskContext = createContext<SubtaskContextValue>({
  tasksById: {},
  orderedTaskIds: [],
  tasks: {},
  hydrateTasks: noop,
  upsertTask: noop,
  removeTasksByRunId: noop,
  resetTasksBySource: noop,
  clearAllTasks: noop,
});

function matchesScope(
  task: TaskViewModel,
  options?: { source?: TaskSource; runId?: string | null },
) {
  if (!options?.source && !options?.runId) {
    return true;
  }
  if (options?.source && task.source !== options.source) {
    return false;
  }
  if (
    options?.runId !== undefined &&
    options?.runId !== null &&
    task.runId !== options.runId
  ) {
    return false;
  }
  return true;
}

function orderTaskIds(tasksById: Record<string, TaskViewModel>, ids: string[]) {
  return [...ids].sort((leftId, rightId) => {
    const left = tasksById[leftId];
    const right = tasksById[rightId];
    if (!left || !right) {
      return 0;
    }

    const leftTime = left.updatedAt ?? left.createdAt ?? "";
    const rightTime = right.updatedAt ?? right.createdAt ?? "";
    if (leftTime && rightTime && leftTime !== rightTime) {
      return leftTime.localeCompare(rightTime);
    }
    return leftId.localeCompare(rightId);
  });
}

export function shouldUseHydratedField(
  existing: TaskViewModel | undefined,
  hydrated: TaskViewModel,
) {
  if (!existing) {
    return true;
  }

  // Let authoritative thread hydration clear transient waiting states when the
  // backend has already resumed the task.
  if (
    existing.status === "waiting_intervention" &&
    hydrated.status === "in_progress"
  ) {
    return true;
  }

  const hydratedPriority = TASK_STATUS_PRIORITY[hydrated.status];
  const existingPriority = TASK_STATUS_PRIORITY[existing.status];
  return hydratedPriority > existingPriority;
}

export function mergeHydratedTask(
  existing: TaskViewModel | undefined,
  hydrated: TaskViewModel,
): TaskViewModel {
  if (!existing) {
    return hydrated;
  }

  const preferHydrated = shouldUseHydratedField(existing, hydrated);
  const selectRichField = <T,>(existingValue: T | undefined, hydratedValue: T | undefined) =>
    hydratedValue !== undefined && (preferHydrated || existingValue === undefined)
      ? hydratedValue
      : existingValue;
  const richFields = {
    latestMessage: selectRichField(existing.latestMessage, hydrated.latestMessage),
    latestUpdate: selectRichField(existing.latestUpdate, hydrated.latestUpdate),
    parentTaskId: selectRichField(existing.parentTaskId, hydrated.parentTaskId),
    requestedByAgent: selectRichField(
      existing.requestedByAgent,
      hydrated.requestedByAgent,
    ),
    requestHelp: selectRichField(existing.requestHelp, hydrated.requestHelp),
    resolvedInputs: selectRichField(
      existing.resolvedInputs,
      hydrated.resolvedInputs,
    ),
    blockedReason: selectRichField(
      existing.blockedReason,
      hydrated.blockedReason,
    ),
    resumeCount: selectRichField(existing.resumeCount, hydrated.resumeCount),
    clarificationPrompt: selectRichField(
      existing.clarificationPrompt,
      hydrated.clarificationPrompt,
    ),
    interventionRequest: selectRichField(
      existing.interventionRequest,
      hydrated.interventionRequest,
    ),
    interventionStatus: selectRichField(
      existing.interventionStatus,
      hydrated.interventionStatus,
    ),
    interventionFingerprint: selectRichField(
      existing.interventionFingerprint,
      hydrated.interventionFingerprint,
    ),
    statusDetail: selectRichField(existing.statusDetail, hydrated.statusDetail),
  };

  return {
    ...existing,
    ...hydrated,
    status: preferHydrated ? hydrated.status : existing.status,
    ...richFields,
  };
}


export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<TaskStoreState>({
    tasksById: {},
    orderedTaskIds: [],
  });

  const clearAllTasks = useCallback(() => {
    setState({ tasksById: {}, orderedTaskIds: [] });
  }, []);

  const resetTasksBySource = useCallback(
    (source: TaskSource, runId?: string | null) => {
      setState((prev) => {
        const tasksById = { ...prev.tasksById };
        const orderedTaskIds = prev.orderedTaskIds.filter((taskId) => {
          const task = tasksById[taskId];
          if (!task) {
            return false;
          }
          const shouldRemove =
            task.source === source &&
            (runId === undefined || runId === null || task.runId === runId);
          if (shouldRemove) {
            delete tasksById[taskId];
            return false;
          }
          return true;
        });

        return { tasksById, orderedTaskIds };
      });
    },
    [],
  );

  const removeTasksByRunId = useCallback((runId: string, source?: TaskSource) => {
    setState((prev) => {
      const tasksById = { ...prev.tasksById };
      const orderedTaskIds = prev.orderedTaskIds.filter((taskId) => {
        const task = tasksById[taskId];
        if (!task) {
          return false;
        }
        const shouldRemove =
          task.runId === runId && (source === undefined || task.source === source);
        if (shouldRemove) {
          delete tasksById[taskId];
          return false;
        }
        return true;
      });

      return { tasksById, orderedTaskIds };
    });
  }, []);

  const upsertTask = useCallback((task: TaskUpsert) => {
    setState((prev) => {
      const existing = prev.tasksById[task.id];
      const normalizedSource = task.source ?? existing?.source ?? "legacy_subagent";
      const nextTask: TaskViewModel = {
        ...existing,
        ...task,
        id: task.id,
        description: task.description ?? existing?.description ?? "",
        status: task.status ?? existing?.status ?? "pending",
        source: normalizedSource,
      } as TaskViewModel;

      const tasksById = {
        ...prev.tasksById,
        [task.id]: nextTask,
      };
      const orderedTaskIds = prev.orderedTaskIds.includes(task.id)
        ? orderTaskIds(tasksById, prev.orderedTaskIds)
        : orderTaskIds(tasksById, [...prev.orderedTaskIds, task.id]);

      return { tasksById, orderedTaskIds };
    });
  }, []);

  const hydrateTasks = useCallback(
    (
      tasks: TaskViewModel[],
      options?: { source?: TaskSource; runId?: string | null },
    ) => {
      setState((prev) => {
        const scopedTasks = tasks.filter((task) => matchesScope(task, options));
        const scopedTaskIds = new Set(scopedTasks.map((task) => task.id));
        const tasksById = { ...prev.tasksById };
        let orderedTaskIds = prev.orderedTaskIds.filter((taskId) => {
          const existing = tasksById[taskId];
          if (!existing) {
            return false;
          }
          const shouldRemove =
            matchesScope(existing, options) && !scopedTaskIds.has(taskId);
          if (shouldRemove) {
            delete tasksById[taskId];
            return false;
          }
          return true;
        });

        for (const task of scopedTasks) {
          tasksById[task.id] = mergeHydratedTask(tasksById[task.id], task);
          if (!orderedTaskIds.includes(task.id)) {
            orderedTaskIds.push(task.id);
          }
        }

        orderedTaskIds = orderTaskIds(tasksById, orderedTaskIds);
        return { tasksById, orderedTaskIds };
      });
    },
    [],
  );

  const value = useMemo<SubtaskContextValue>(
    () => ({
      ...state,
      tasks: state.tasksById,
      hydrateTasks,
      upsertTask,
      removeTasksByRunId,
      resetTasksBySource,
      clearAllTasks,
    }),
    [state, hydrateTasks, upsertTask, removeTasksByRunId, resetTasksBySource, clearAllTasks],
  );

  return (
    <SubtaskContext.Provider value={value}>{children}</SubtaskContext.Provider>
  );
}

export function useSubtaskContext() {
  const context = useContext(SubtaskContext);
  if (context === undefined) {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  }
  return context;
}

export function useSubtask(id: string) {
  const { tasksById } = useSubtaskContext();
  return tasksById[id];
}

export function useTaskActions() {
  const {
    hydrateTasks,
    upsertTask,
    removeTasksByRunId,
    resetTasksBySource,
    clearAllTasks,
  } = useSubtaskContext();

  return {
    hydrateTasks,
    upsertTask,
    removeTasksByRunId,
    resetTasksBySource,
    clearAllTasks,
  };
}

export function useUpdateSubtask() {
  const { upsertTask } = useTaskActions();
  return upsertTask;
}
