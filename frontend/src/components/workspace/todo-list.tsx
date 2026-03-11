import { ChevronUpIcon, ListTodoIcon } from "lucide-react";
import { useState } from "react";

import type { Todo } from "@/core/todos";
import { cn } from "@/lib/utils";

import {
  QueueItem,
  QueueItemContent,
  QueueItemIndicator,
  QueueList,
} from "../ai-elements/queue";

export function TodoList({
  className,
  todos,
  collapsed: controlledCollapsed,
  hidden = false,
  onToggle,
}: {
  className?: string;
  todos: Todo[];
  collapsed?: boolean;
  hidden?: boolean;
  onToggle?: () => void;
}) {
  const [internalCollapsed, setInternalCollapsed] = useState(true);
  const isControlled = controlledCollapsed !== undefined;
  const collapsed = isControlled ? controlledCollapsed : internalCollapsed;

  if (hidden) {
    return null;
  }

  const handleToggle = () => {
    if (isControlled) {
      onToggle?.();
    } else {
      setInternalCollapsed((prev) => !prev);
    }
  };

  return (
    <div
      className={cn(
        "flex h-fit w-full origin-bottom flex-col overflow-hidden rounded-t-2xl border border-b-0 bg-white shadow-sm backdrop-blur-sm transition-all duration-200 ease-out",
        className,
      )}
    >
      <header
        className={cn(
          "bg-accent flex min-h-7 shrink-0 cursor-pointer items-center justify-between px-3 py-1.5 text-sm transition-all duration-300 ease-out",
        )}
        onClick={handleToggle}
      >
        <div className="text-muted-foreground">
          <div className="flex items-center justify-center gap-2">
            <ListTodoIcon className="size-4" />
            <div>To-dos</div>
          </div>
        </div>
        <div>
          <ChevronUpIcon
            className={cn(
              "text-muted-foreground size-4 transition-transform duration-300 ease-out",
              collapsed ? "" : "rotate-180",
            )}
          />
        </div>
      </header>
      <main
        className={cn(
          "bg-accent flex grow px-2 transition-all duration-300 ease-out",
          collapsed ? "h-0 pb-2" : "h-24 pb-2.5",
        )}
      >
        <QueueList className="bg-background mt-0 w-full rounded-t-2xl">
          {todos.map((todo, i) => (
            <QueueItem key={i + (todo.content ?? "")}>
              <div className="flex items-center gap-2">
                <QueueItemIndicator
                  className={
                    todo.status === "in_progress" ? "bg-primary/70" : ""
                  }
                  completed={todo.status === "completed"}
                />
                <QueueItemContent
                  className={
                    todo.status === "in_progress" ? "text-primary/70" : ""
                  }
                  completed={todo.status === "completed"}
                >
                  {todo.content}
                </QueueItemContent>
              </div>
            </QueueItem>
          ))}
        </QueueList>
      </main>
    </div>
  );
}
