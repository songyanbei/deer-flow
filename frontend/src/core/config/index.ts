import { env } from "@/env";

function normalizeBaseURL(
  value: string | undefined,
  fallbackPath: string,
  isMock?: boolean,
) {
  const rawValue = value?.trim();

  if (rawValue) {
    try {
      return new URL(rawValue).toString().replace(/\/$/, "");
    } catch {
      if (typeof window !== "undefined") {
        return new URL(rawValue, window.location.origin)
          .toString()
          .replace(/\/$/, "");
      }
    }
  }

  if (typeof window !== "undefined") {
    return new URL(fallbackPath, window.location.origin)
      .toString()
      .replace(/\/$/, "");
  }

  return isMock
    ? "http://localhost:3000/mock/api"
    : `http://localhost:2026${fallbackPath}`.replace(/\/$/, "");
}

export function getBackendBaseURL() {
  return normalizeBaseURL(env.NEXT_PUBLIC_BACKEND_BASE_URL, "");
}

export function getLangGraphBaseURL(isMock?: boolean) {
  return normalizeBaseURL(
    env.NEXT_PUBLIC_LANGGRAPH_BASE_URL,
    isMock ? "/mock/api" : "/api/langgraph",
    isMock,
  );
}
