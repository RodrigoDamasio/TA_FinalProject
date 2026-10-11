import { existsSync } from "node:fs";
import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

// BASE_URL=https://<vercel-domain> runs the production subset instead of local servers.
const deployedUrl = process.env.BASE_URL;

// Browser: the preinstalled Chromium in cloud sessions, the installed Chrome locally
// (Playwright's bundled Chromium is not supported on the user's Ubuntu 20.04).
const cloudChromium = "/opt/pw-browsers/chromium";
const executablePath = process.env.PW_CHROMIUM ?? (existsSync(cloudChromium) ? cloudChromium : undefined);
const browser = executablePath ? { launchOptions: { executablePath } } : { channel: "chrome" };

// Backend interpreter: backend/.venv (cloud) or the shared .venv above the repo (local).
const backendDir = path.join(__dirname, "../backend");
const uvicorn = [path.join(backendDir, ".venv/bin/uvicorn"), path.join(backendDir, "../../../.venv/bin/uvicorn")].find(
  (p) => existsSync(p),
) ?? "uvicorn";

export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  reporter: [["list"]],
  workers: 1, // one backend: runs execute one at a time
  use: {
    baseURL: deployedUrl ?? "http://localhost:3000",
    trace: "retain-on-failure",
  },
  projects: [{ name: "desktop", use: { ...devices["Desktop Chrome"], ...browser } }],
  webServer: deployedUrl
    ? undefined
    : [
        {
          // Real backend in fake mode (demo agents, fake search, Wikipedia and embedder):
          // the whole loop with 0 real calls. FAKE_LLM_DELAY_S makes the live board visible.
          command: `rm -rf .e2e && mkdir -p .e2e && ${uvicorn} app.main:app --port 8000`,
          cwd: backendDir,
          url: "http://localhost:8000/health",
          env: {
            LLM_MODE: "fake",
            EMBED_MODE: "fake",
            SEARCH_MODE: "fake",
            WIKI_MODE: "fake",
            TELEGRAM_MODE: "off",
            FAKE_LLM_DELAY_S: "0.3",
            DATABASE_PATH: ".e2e/research.db",
            CHROMA_PATH: ".e2e/chroma",
            FRONTEND_ORIGIN: "http://localhost:3000",
            RATE_LIMIT_RUNS_PER_MINUTE: "1000",
            RATE_LIMIT_RUNS_PER_DAY: "10000",
            RATE_LIMIT_UPLOADS_PER_HOUR: "1000",
          },
          timeout: 120_000,
          reuseExistingServer: false,
        },
        {
          // Production build: no file watchers (the dev server hits the OS watch limit locally).
          command: "npm run build && npm run start",
          url: "http://localhost:3000",
          env: { NEXT_PUBLIC_API_URL: "http://localhost:8000" },
          timeout: 240_000,
          reuseExistingServer: false,
        },
      ],
});
