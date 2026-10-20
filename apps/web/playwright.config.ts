import { defineConfig } from "@playwright/test";
import path from "node:path";

const root = path.resolve(__dirname, "../..");
const python = process.env.MODSAGE_PYTHON ?? path.join(root, ".venv/bin/python");
const database = path.join(root, "data/modsage-e2e.db");

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  timeout: 30_000,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:3100",
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    launchOptions: process.env.MODSAGE_CHROMIUM_EXECUTABLE
      ? { executablePath: process.env.MODSAGE_CHROMIUM_EXECUTABLE }
      : {},
  },
  webServer: [
    {
      command: `"${python}" scripts/seed_e2e.py && "${python}" -m uvicorn modsage.main:app --host 127.0.0.1 --port 8100`,
      cwd: root,
      env: {
        MODSAGE_DATABASE_URL: `sqlite:///${database}`,
        MODSAGE_E2E: "1",
        MODSAGE_LLM_API_KEY: "",
        MODSAGE_STEAM_API_KEY: "",
      },
      url: "http://127.0.0.1:8100/api/v1/health",
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: "npm run start -- --port 3100",
      env: { API_BASE_URL: "http://127.0.0.1:8100" },
      url: "http://127.0.0.1:3100",
      timeout: 60_000,
      reuseExistingServer: false,
    },
  ],
});
