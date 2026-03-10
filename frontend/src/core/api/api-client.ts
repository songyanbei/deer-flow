"use client";

import { Client as LangGraphClient } from "@langchain/langgraph-sdk/client";

import { getLangGraphBaseURL } from "../config";

const clients = new Map<string, LangGraphClient>();

export function getAPIClient(isMock?: boolean): LangGraphClient {
  const apiUrl = getLangGraphBaseURL(isMock);
  const existingClient = clients.get(apiUrl);
  if (existingClient) {
    return existingClient;
  }

  const client = new LangGraphClient({
    apiUrl,
  });
  clients.set(apiUrl, client);
  return client;
}
