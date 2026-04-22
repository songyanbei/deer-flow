"use client";

import { useParams, usePathname, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { createRuntimeThread } from "@/core/threads/runtime-api";

type ThreadCreationState = "idle" | "creating" | "ready" | "error";

export function useThreadChat() {
  const { thread_id: threadIdFromPath } = useParams<{ thread_id: string }>();
  const pathname = usePathname();

  const searchParams = useSearchParams();
  const [threadId, setThreadId] = useState(() => threadIdFromPath);

  const [isNewThread, setIsNewThread] = useState(
    () => threadIdFromPath === "new",
  );
  // `creating` covers the window between mount on `/new` and the Gateway
  // `POST /api/runtime/threads` resolving. UI uses this to disable submit
  // so we never try to stream into an un-registered thread id (Phase 1 D1.1).
  const [creationState, setCreationState] =
    useState<ThreadCreationState>("idle");
  const [creationError, setCreationError] = useState<Error | null>(null);
  const activeRequestRef = useRef<symbol | null>(null);

  useEffect(() => {
    if (pathname.endsWith("/new")) {
      setIsNewThread(true);
      setThreadId("new");
      setCreationState("creating");
      setCreationError(null);

      const requestToken = Symbol("runtime-thread-create");
      activeRequestRef.current = requestToken;

      void createRuntimeThread()
        .then((result) => {
          // Ignore stale responses if the user navigated away.
          if (activeRequestRef.current !== requestToken) return;
          setThreadId(result.thread_id);
          setCreationState("ready");
        })
        .catch((error: unknown) => {
          if (activeRequestRef.current !== requestToken) return;
          setCreationError(
            error instanceof Error ? error : new Error(String(error)),
          );
          setCreationState("error");
        });

      return () => {
        // Any in-flight response after unmount/navigation is discarded.
        if (activeRequestRef.current === requestToken) {
          activeRequestRef.current = null;
        }
      };
    }

    activeRequestRef.current = null;
    setIsNewThread(false);
    setCreationState("idle");
    setCreationError(null);
    if (threadIdFromPath !== "new") {
      setThreadId(threadIdFromPath);
    }
  }, [pathname, threadIdFromPath]);

  const retryThreadCreation = useCallback(() => {
    if (!pathname.endsWith("/new")) return;
    setCreationState("creating");
    setCreationError(null);
    const requestToken = Symbol("runtime-thread-create-retry");
    activeRequestRef.current = requestToken;
    void createRuntimeThread()
      .then((result) => {
        if (activeRequestRef.current !== requestToken) return;
        setThreadId(result.thread_id);
        setCreationState("ready");
      })
      .catch((error: unknown) => {
        if (activeRequestRef.current !== requestToken) return;
        setCreationError(
          error instanceof Error ? error : new Error(String(error)),
        );
        setCreationState("error");
      });
  }, [pathname]);

  const isMock = searchParams.get("mock") === "true";
  const threadReady = !isNewThread || creationState === "ready";

  return {
    threadId,
    isNewThread,
    setIsNewThread,
    isMock,
    threadReady,
    threadCreationState: creationState,
    threadCreationError: creationError,
    retryThreadCreation,
  };
}
