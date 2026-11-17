import { defineConfig } from "@playwright/test";
import { existsSync } from "node:fs";
import { resolve } from "node:path";
const python =
  process.env.ZK_PYTHON ||
  (existsSync("../.venv/bin/python")
    ? resolve("../.venv/bin/python")
    : "python3");
export default defineConfig({
  testDir: "./tests",
  expect: { timeout: 15000 },
  timeout: 120000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8000",
    headless: true,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `${python} -m uvicorn zookeeper.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log`,
    cwd: "..",
    env: {
      ZK_DATA: process.env.ZK_BROWSER_DATA || "data/browser-test",
      OMP_NUM_THREADS: "2",
      NUMBA_NUM_THREADS: "2",
    },
    url: "http://127.0.0.1:8000/api/health",
    reuseExistingServer: false,
  },
});
