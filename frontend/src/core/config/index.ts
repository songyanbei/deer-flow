import { env } from "@/env";

const DEFAULT_APP_ORIGIN = "http://localhost:2026";
const DEFAULT_MOCK_ORIGIN = "http://localhost:3000";

function trimTrailingSlash(url: string) {
  return url.replace(/\/$/, "");
}

function getBrowserOrigin(fallbackOrigin = DEFAULT_APP_ORIGIN) {
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return fallbackOrigin;
}

function normalizeAbsoluteURL(value: string, fallbackURL: string) {
  const trimmedValue = value.trim();
  const fallback = trimTrailingSlash(fallbackURL);

  if (!trimmedValue) {
    return fallback;
  }

  if (/^https?:\/\//i.test(trimmedValue)) {
    return trimTrailingSlash(trimmedValue);
  }

  if (trimmedValue.startsWith("//")) {
    const protocol =
      typeof window !== "undefined" ? window.location.protocol : "http:";
    return trimTrailingSlash(`${protocol}${trimmedValue}`);
  }

  if (/^(localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:\/.*)?$/i.test(trimmedValue)) {
    return trimTrailingSlash(`http://${trimmedValue}`);
  }

  return trimTrailingSlash(
    new URL(trimmedValue, getBrowserOrigin(new URL(fallback).origin)).toString(),
  );
}

export function getBackendBaseURL() {
  if (env.NEXT_PUBLIC_BACKEND_BASE_URL) {
    return env.NEXT_PUBLIC_BACKEND_BASE_URL;
  } else {
    return "";
  }
}

export function getLangGraphBaseURL(isMock?: boolean) {
  const fallbackURL = isMock
    ? `${getBrowserOrigin(DEFAULT_MOCK_ORIGIN)}/mock/api`
    : `${getBrowserOrigin(DEFAULT_APP_ORIGIN)}/api/langgraph`;

  if (env.NEXT_PUBLIC_LANGGRAPH_BASE_URL) {
    return normalizeAbsoluteURL(env.NEXT_PUBLIC_LANGGRAPH_BASE_URL, fallbackURL);
  } else if (isMock) {
    return fallbackURL;
  } else {
    return fallbackURL;
  }
}
