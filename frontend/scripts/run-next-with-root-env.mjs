import { spawn } from "node:child_process";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import dotenv from "dotenv";

const __dirname = dirname(fileURLToPath(import.meta.url));
const rootEnvPath = resolve(__dirname, "../../.env");

dotenv.config({ path: rootEnvPath });

const [command = "dev", ...restArgs] = process.argv.slice(2);
const nextArgs = [command, ...restArgs];
const isWindows = process.platform === "win32";
const childCommand = isWindows ? "cmd.exe" : "pnpm";
const childArgs = isWindows
  ? ["/c", "pnpm", "exec", "next", ...nextArgs]
  : ["exec", "next", ...nextArgs];

const child = spawn(childCommand, childArgs, {
  stdio: "inherit",
  env: process.env,
  cwd: resolve(__dirname, ".."),
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});

child.on("error", (error) => {
  console.error("Failed to start Next.js:", error);
  process.exit(1);
});
