import { betterAuth } from "better-auth";

import { env } from "@/env";

const authSecret =
  env.BETTER_AUTH_SECRET ??
  (env.NODE_ENV === "production"
    ? undefined
    : "deer-flow-dev-secret-change-me");

export const auth = betterAuth({
  secret: authSecret,
  emailAndPassword: {
    enabled: true,
  },
});

export type Session = typeof auth.$Infer.Session;
