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

function getBrowserURLParts() {
  if (typeof window === "undefined") {
    return null;
  }

  return {
    protocol: window.location.protocol,
    hostname: window.location.hostname,
    port: window.location.port,
    origin: window.location.origin,
  };
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
  }

  const browser = getBrowserURLParts();
  if (!browser) {
    return "";
  }

  // Preserve nginx reverse-proxy behavior when the app is served from the unified entry port.
  if (browser.port === "2026" || browser.port === "") {
    return "";
  }

  return `${browser.protocol}//${browser.hostname}:8001`;
}

export function getLangGraphBaseURL(isMock?: boolean) {
  const browser = getBrowserURLParts();
  const fallbackURL = isMock
    ? `${getBrowserOrigin(DEFAULT_MOCK_ORIGIN)}/mock/api`
    : browser && browser.port !== "2026" && browser.port !== ""
      ? `${browser.protocol}//${browser.hostname}:2024`
      : `${getBrowserOrigin(DEFAULT_APP_ORIGIN)}/api/langgraph`;

  if (env.NEXT_PUBLIC_LANGGRAPH_BASE_URL) {
    return normalizeAbsoluteURL(env.NEXT_PUBLIC_LANGGRAPH_BASE_URL, fallbackURL);
  } else if (isMock) {
    return fallbackURL;
  } else {
    return fallbackURL;
  }
}
