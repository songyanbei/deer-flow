import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import type { TaskSource, TaskUpsert, TaskViewModel } from "./types";

interface TaskStoreState {
  tasksById: Record<string, TaskViewModel>;
  orderedTaskIds: string[];
}

type TaskStoreListener = () => void;

interface TaskStore {
  getState: () => TaskStoreState;
  subscribe: (listener: TaskStoreListener) => () => void;
  hydrateTasks: (
    tasks: TaskViewModel[],
    options?: { source?: TaskSource; runId?: string | null },
  ) => void;
  upsertTask: (task: TaskUpsert) => void;
  removeTasksByRunId: (runId: string, source?: TaskSource) => void;
  resetTasksBySource: (source: TaskSource, runId?: string | null) => void;
  clearAllTasks: () => void;
}

export interface SubtaskContextValue extends TaskStoreState {
  tasks: Record<string, TaskViewModel>;
  hydrateTasks: TaskStore["hydrateTasks"];
  upsertTask: TaskStore["upsertTask"];
  removeTasksByRunId: TaskStore["removeTasksByRunId"];
  resetTasksBySource: TaskStore["resetTasksBySource"];
  clearAllTasks: TaskStore["clearAllTasks"];
}

const TASK_STATUS_PRIORITY: Record<TaskViewModel["status"], number> = {
  pending: 0,
  in_progress: 1,
  waiting_dependency: 2,
  waiting_clarification: 3,
  waiting_intervention: 4,
  completed: 5,
  failed: 6,
};

const EMPTY_TASK_STORE_STATE: TaskStoreState = {
  tasksById: {},
  orderedTaskIds: [],
};

const objectIs = Object.is;

const SubtaskContext = createContext<TaskStore | null>(null);

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

function compareTaskIds(
  tasksById: Record<string, TaskViewModel>,
  leftId: string,
  rightId: string,
) {
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
}

function orderTaskIds(tasksById: Record<string, TaskViewModel>, ids: string[]) {
  return [...ids].sort((leftId, rightId) =>
    compareTaskIds(tasksById, leftId, rightId),
  );
}

function insertTaskId(
  ids: string[],
  tasksById: Record<string, TaskViewModel>,
  taskId: string,
) {
  const nextIds = ids.filter((id) => id !== taskId);
  const insertAt = nextIds.findIndex(
    (candidateId) => compareTaskIds(tasksById, taskId, candidateId) < 0,
  );

  if (insertAt === -1) {
    nextIds.push(taskId);
    return nextIds;
  }

  nextIds.splice(insertAt, 0, taskId);
  return nextIds;
}

function haveSameTaskOrder(
  previous: TaskViewModel | undefined,
  next: TaskViewModel,
) {
  return (
    (previous?.updatedAt ?? previous?.createdAt ?? "") ===
    (next.updatedAt ?? next.createdAt ?? "")
  );
}

function shallowEqualTask(
  left: TaskViewModel | undefined,
  right: TaskViewModel | undefined,
) {
  if (left === right) {
    return true;
  }
  if (!left || !right) {
    return false;
  }

  const leftKeys = Object.keys(left) as Array<keyof TaskViewModel>;
  const rightKeys = Object.keys(right) as Array<keyof TaskViewModel>;
  if (leftKeys.length !== rightKeys.length) {
    return false;
  }

  for (const key of leftKeys) {
    if (!objectIs(left[key], right[key])) {
      return false;
    }
  }

  return true;
}

function shallowEqualStringArray(left: string[], right: string[]) {
  if (left === right) {
    return true;
  }
  if (left.length !== right.length) {
    return false;
  }
  for (let index = 0; index < left.length; index += 1) {
    if (left[index] !== right[index]) {
      return false;
    }
  }
  return true;
}

export function shouldUseHydratedField(
  existing: TaskViewModel | undefined,
  hydrated: TaskViewModel,
) {
  if (!existing) {
    return true;
  }

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
  const selectRichField = <T,>(
    existingValue: T | undefined,
    hydratedValue: T | undefined,
  ) =>
    hydratedValue !== undefined &&
    (preferHydrated || existingValue === undefined)
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
    blockedReason: selectRichField(existing.blockedReason, hydrated.blockedReason),
    resumeCount: selectRichField(existing.resumeCount, hydrated.resumeCount),
    clarificationPrompt: selectRichField(
      existing.clarificationPrompt,
      hydrated.clarificationPrompt,
    ),
    clarificationRequest: selectRichField(
      existing.clarificationRequest,
      hydrated.clarificationRequest,
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

function createTaskStore(): TaskStore {
  let state = EMPTY_TASK_STORE_STATE;
  const listeners = new Set<TaskStoreListener>();

  const getState = () => state;

  const setState = (
    updater: (current: TaskStoreState) => TaskStoreState,
  ) => {
    const nextState = updater(state);
    if (nextState === state) {
      return;
    }
    state = nextState;
    listeners.forEach((listener) => {
      listener();
    });
  };

  const subscribe = (listener: TaskStoreListener) => {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  };

  const clearAllTasks = () => {
    setState((current) => {
      if (
        current.orderedTaskIds.length === 0 &&
        Object.keys(current.tasksById).length === 0
      ) {
        return current;
      }
      return EMPTY_TASK_STORE_STATE;
    });
  };

  const resetTasksBySource = (source: TaskSource, runId?: string | null) => {
    setState((current) => {
      let tasksById = current.tasksById;
      const orderedTaskIds = current.orderedTaskIds.filter((taskId) => {
        const task = tasksById[taskId];
        if (!task) {
          return false;
        }
        const shouldRemove =
          task.source === source &&
          (runId === undefined || runId === null || task.runId === runId);
        if (!shouldRemove) {
          return true;
        }
        if (tasksById === current.tasksById) {
          tasksById = { ...current.tasksById };
        }
        delete tasksById[taskId];
        return false;
      });

      if (tasksById === current.tasksById) {
        return current;
      }

      return { tasksById, orderedTaskIds };
    });
  };

  const removeTasksByRunId = (runId: string, source?: TaskSource) => {
    setState((current) => {
      let tasksById = current.tasksById;
      const orderedTaskIds = current.orderedTaskIds.filter((taskId) => {
        const task = tasksById[taskId];
        if (!task) {
          return false;
        }
        const shouldRemove =
          task.runId === runId &&
          (source === undefined || task.source === source);
        if (!shouldRemove) {
          return true;
        }
        if (tasksById === current.tasksById) {
          tasksById = { ...current.tasksById };
        }
        delete tasksById[taskId];
        return false;
      });

      if (tasksById === current.tasksById) {
        return current;
      }

      return { tasksById, orderedTaskIds };
    });
  };

  const upsertTask = (task: TaskUpsert) => {
    setState((current) => {
      const existing = current.tasksById[task.id];
      const normalizedSource =
        task.source ?? existing?.source ?? "legacy_subagent";
      const nextTask: TaskViewModel = {
        ...existing,
        ...task,
        id: task.id,
        description: task.description ?? existing?.description ?? "",
        status: task.status ?? existing?.status ?? "pending",
        source: normalizedSource,
      } as TaskViewModel;

      if (shallowEqualTask(existing, nextTask)) {
        return current;
      }

      const tasksById = {
        ...current.tasksById,
        [task.id]: nextTask,
      };

      let orderedTaskIds = current.orderedTaskIds;
      if (!existing) {
        orderedTaskIds = insertTaskId(current.orderedTaskIds, tasksById, task.id);
      } else if (!haveSameTaskOrder(existing, nextTask)) {
        const reorderedIds = insertTaskId(
          current.orderedTaskIds,
          tasksById,
          task.id,
        );
        if (!shallowEqualStringArray(reorderedIds, current.orderedTaskIds)) {
          orderedTaskIds = reorderedIds;
        }
      }

      return { tasksById, orderedTaskIds };
    });
  };

  const hydrateTasks = (
    tasks: TaskViewModel[],
    options?: { source?: TaskSource; runId?: string | null },
  ) => {
    setState((current) => {
      const scopedTasks = tasks.filter((task) => matchesScope(task, options));
      const scopedTaskIds = new Set(scopedTasks.map((task) => task.id));
      let tasksById = current.tasksById;
      let orderedTaskIds = current.orderedTaskIds;
      let didChange = false;
      let shouldResort = false;

      const filteredTaskIds = orderedTaskIds.filter((taskId) => {
        const existing = tasksById[taskId];
        if (!existing) {
          didChange = true;
          shouldResort = true;
          return false;
        }
        const shouldRemove =
          matchesScope(existing, options) && !scopedTaskIds.has(taskId);
        if (!shouldRemove) {
          return true;
        }
        if (tasksById === current.tasksById) {
          tasksById = { ...current.tasksById };
        }
        delete tasksById[taskId];
        didChange = true;
        shouldResort = true;
        return false;
      });

      if (!shallowEqualStringArray(filteredTaskIds, orderedTaskIds)) {
        orderedTaskIds = filteredTaskIds;
      }

      const knownTaskIds = new Set(orderedTaskIds);
      for (const task of scopedTasks) {
        const existing = tasksById[task.id];
        const mergedTask = mergeHydratedTask(existing, task);

        if (!shallowEqualTask(existing, mergedTask)) {
          if (tasksById === current.tasksById) {
            tasksById = { ...current.tasksById };
          }
          tasksById[task.id] = mergedTask;
          didChange = true;
          if (!existing || !haveSameTaskOrder(existing, mergedTask)) {
            shouldResort = true;
          }
        }

        if (!knownTaskIds.has(task.id)) {
          if (orderedTaskIds === current.orderedTaskIds) {
            orderedTaskIds = [...current.orderedTaskIds];
          }
          orderedTaskIds.push(task.id);
          knownTaskIds.add(task.id);
          didChange = true;
          shouldResort = true;
        }
      }

      if (!didChange) {
        return current;
      }

      if (shouldResort) {
        const reorderedIds = orderTaskIds(tasksById, orderedTaskIds);
        if (!shallowEqualStringArray(reorderedIds, orderedTaskIds)) {
          orderedTaskIds = reorderedIds;
        }
      }

      return { tasksById, orderedTaskIds };
    });
  };

  const store: TaskStore = {
    getState,
    subscribe,
    hydrateTasks,
    upsertTask,
    removeTasksByRunId,
    resetTasksBySource,
    clearAllTasks,
  };

  return store;
}

export function SubtasksProvider({ children }: { children: React.ReactNode }) {
  const [store] = useState(createTaskStore);
  return (
    <SubtaskContext.Provider value={store}>{children}</SubtaskContext.Provider>
  );
}

function useTaskStore() {
  const store = useContext(SubtaskContext);
  if (!store) {
    throw new Error(
      "useSubtaskContext must be used within a SubtaskContext.Provider",
    );
  }
  return store;
}

function useTaskStoreSelector<T>(
  selector: (state: TaskStoreState) => T,
  isEqual: (left: T, right: T) => boolean = objectIs,
) {
  const store = useTaskStore();
  const [selected, setSelected] = useState(() => selector(store.getState()));
  const selectorRef = useRef(selector);
  const isEqualRef = useRef(isEqual);

  useEffect(() => {
    selectorRef.current = selector;
    isEqualRef.current = isEqual;

    const nextSelected = selector(store.getState());
    setSelected((currentSelected) =>
      isEqual(currentSelected, nextSelected)
        ? currentSelected
        : nextSelected,
    );
  }, [isEqual, selector, store]);

  useEffect(() => {
    return store.subscribe(() => {
      const nextSelected = selectorRef.current(store.getState());
      setSelected((currentSelected) =>
        isEqualRef.current(currentSelected, nextSelected)
          ? currentSelected
          : nextSelected,
      );
    });
  }, [store]);

  return selected;
}

export function useSubtaskContext() {
  const store = useTaskStore();
  const state = useTaskStoreSelector((current) => current);

  return useMemo<SubtaskContextValue>(
    () => ({
      ...state,
      tasks: state.tasksById,
      hydrateTasks: store.hydrateTasks,
      upsertTask: store.upsertTask,
      removeTasksByRunId: store.removeTasksByRunId,
      resetTasksBySource: store.resetTasksBySource,
      clearAllTasks: store.clearAllTasks,
    }),
    [state, store],
  );
}

export function useSubtask(id: string) {
  return useTaskStoreSelector((state) => state.tasksById[id]);
}

export function useTaskActions() {
  const store = useTaskStore();

  return useMemo(
    () => ({
      hydrateTasks: store.hydrateTasks,
      upsertTask: store.upsertTask,
      removeTasksByRunId: store.removeTasksByRunId,
      resetTasksBySource: store.resetTasksBySource,
      clearAllTasks: store.clearAllTasks,
    }),
    [store],
  );
}

export function useUpdateSubtask() {
  return useTaskActions().upsertTask;
}
