"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { useI18n } from "@/core/i18n/hooks";

type CallbackState =
  | "pending"
  | "invalid-entry"
  | "expired"
  | "unavailable"
  | "network";

const SSO_CALLBACK_ENDPOINT = "/api/sso/callback";
const DEFAULT_REDIRECT = "/chat";

export default function SsoCallbackPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { t } = useI18n();

  const [state, setState] = useState<CallbackState>("pending");
  const sentRef = useRef(false);

  const ticket = searchParams?.get("ticket") ?? null;
  const targetSystem = searchParams?.get("targetSystem");

  useEffect(() => {
    if (sentRef.current) {
      return;
    }

    if (!ticket) {
      sentRef.current = true;
      setState("invalid-entry");
      return;
    }

    sentRef.current = true;

    const body: { ticket: string; targetSystem?: string } = { ticket };
    if (targetSystem) {
      body.targetSystem = targetSystem;
    }

    let cancelled = false;

    void (async () => {
      let res: Response;
      try {
        res = await fetch(SSO_CALLBACK_ENDPOINT, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } catch {
        if (!cancelled) setState("network");
        return;
      }

      if (cancelled) return;

      if (res.ok) {
        let redirect: string | undefined;
        try {
          const data = (await res.json()) as { redirect?: unknown } | null;
          if (data && typeof data.redirect === "string") {
            redirect = data.redirect;
          }
        } catch {
          redirect = undefined;
        }
        if (cancelled) return;
        router.replace(redirect ?? DEFAULT_REDIRECT);
        return;
      }

      if (res.status === 401) {
        setState("expired");
        return;
      }

      setState("unavailable");
    })();

    return () => {
      cancelled = true;
    };
  }, [ticket, targetSystem, router]);

  return (
    <main className="flex min-h-screen items-center justify-center bg-background px-6 py-16">
      <section
        aria-live="polite"
        className="w-full max-w-md rounded-lg border border-border bg-card p-8 shadow-sm"
      >
        <CallbackBody state={state} t={t} />
      </section>
    </main>
  );
}

function CallbackBody({
  state,
  t,
}: {
  state: CallbackState;
  t: ReturnType<typeof useI18n>["t"];
}) {
  const copy = t.sso.callback;

  if (state === "pending") {
    return (
      <div className="space-y-3 text-center">
        <h1 className="text-lg font-semibold text-foreground">
          {copy.pending}
        </h1>
        <p className="text-sm text-muted-foreground">{copy.pendingHint}</p>
      </div>
    );
  }

  const map: Record<
    Exclude<CallbackState, "pending">,
    { title: string; description: string }
  > = {
    "invalid-entry": {
      title: copy.invalidEntryTitle,
      description: copy.invalidEntryDescription,
    },
    expired: {
      title: copy.expiredTitle,
      description: copy.expiredDescription,
    },
    unavailable: {
      title: copy.unavailableTitle,
      description: copy.unavailableDescription,
    },
    network: {
      title: copy.networkTitle,
      description: copy.networkDescription,
    },
  };

  const entry = map[state];

  return (
    <div className="space-y-3 text-center">
      <h1 className="text-lg font-semibold text-foreground">{entry.title}</h1>
      <p className="text-sm text-muted-foreground">{entry.description}</p>
      <p className="text-xs text-muted-foreground">{copy.backToMossHubHint}</p>
    </div>
  );
}
