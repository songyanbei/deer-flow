const WRITE_FILE_SCHEME = "write-file://artifact";

export function buildWriteFileUrl({
  path,
  messageId,
  toolCallId,
}: {
  path: string;
  messageId?: string;
  toolCallId?: string;
}) {
  const url = new URL(WRITE_FILE_SCHEME);
  url.pathname = path;
  if (messageId) {
    url.searchParams.set("message_id", messageId);
  }
  if (toolCallId) {
    url.searchParams.set("tool_call_id", toolCallId);
  }
  return url.toString();
}

export function parseWriteFileUrl(urlString: string) {
  const url = new URL(urlString);
  if (url.protocol !== "write-file:") {
    throw new Error(`Unsupported write-file URL: ${urlString}`);
  }

  let path = decodeURIComponent(url.pathname);
  if (/^\/[A-Za-z]:/.test(path)) {
    path = path.slice(1);
  }

  return {
    path,
    messageId: url.searchParams.get("message_id"),
    toolCallId: url.searchParams.get("tool_call_id"),
  };
}
