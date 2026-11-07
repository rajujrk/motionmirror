import { readFileSync } from "node:fs";
import path from "node:path";
import { defineConfig } from "@playwright/test";

function readEnv(): Record<string, string> {
  const entries = readFileSync(path.join(import.meta.dirname, "../../.env"), "utf8")
    .split("\n")
    .filter((line) => line && !line.startsWith("#") && line.includes("="))
    .map((line) => [line.slice(0, line.indexOf("=")), line.slice(line.indexOf("=") + 1)]);
  return Object.fromEntries(entries);
}

const env = readEnv();
process.env.MM_DEV_USER_PASSWORD = env.MM_DEV_USER_PASSWORD;

export default defineConfig({
  testDir: ".",
  timeout: 180_000,
  expect: { timeout: 60_000 },
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  outputDir: "../../.data/e2e-results",
  use: {
    baseURL: env.MM_PUBLIC_ORIGIN,
    // Google Chrome, because Playwright's bundled Chromium can't decode H.264.
    channel: "chrome",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
