import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";

test("offline scientist flow: import, projections, source evidence, formatting and saved decisions", async ({
  page,
  request,
}) => {
  const outbound = [],
    errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (["127.0.0.1", "localhost"].includes(url.hostname))
      return route.continue();
    outbound.push(url.href);
    return route.abort("internetdisconnected");
  });
  const temp = mkdtempSync(join(tmpdir(), "zk-browser-"));
  const python =
    process.env.ZK_PYTHON ||
    (existsSync("../.venv/bin/python")
      ? resolve("../.venv/bin/python")
      : "python3");
  const parquet = join(temp, "demo.parquet");
  execFileSync(python, ["-m", "zookeeper.demo", parquet], { cwd: ".." });
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(parquet);
  await expect(page.getByText("RECORDS", { exact: true })).toBeVisible({
    timeout: 60000,
  });
  const sid = new URL(page.url()).searchParams.get("snapshot");
  const base = `/api/snapshots/${sid}`;
  await page.getByRole("button", { name: "03 Leakage" }).click();
  await page.getByRole("button", { name: "Run audit", exact: true }).click();
  await expect(
    page.getByText("candidate source rows in the filtered queue.", {
      exact: false,
    }),
  ).toBeVisible();
  await page
    .getByRole("row")
    .filter({ hasText: "sum_copy.py" })
    .first()
    .click();
  await expect(page.locator("#viewer")).toBeVisible();
  await expect(page.locator(".monaco-diff-editor")).toBeVisible();
  await page
    .locator("#evidence")
    .getByRole("button", { name: "Format", exact: true })
    .first()
    .click();
  await expect(page.getByText("Formatted copy", { exact: true })).toBeVisible();
  await expect(page.locator(".view-lines").first()).toContainText(
    "def add(a, b):",
  );
  await page
    .locator("#viewer-bottom select")
    .nth(1)
    .selectOption("confirmed leakage");
  await page
    .getByRole("textbox", { name: "Review notes" })
    .fill("Browser offline review");
  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(page.getByText("Saved to this snapshot")).toBeVisible();
  await page.getByRole("button", { name: "Close viewer" }).click();
  await page.getByRole("button", { name: "04 Coverage" }).click();
  await expect(
    page.getByRole("cell", { name: "rust", exact: true }),
  ).toBeVisible();
  await page.getByRole("cell", { name: "rust", exact: true }).click();
  await page.getByRole("row").filter({ hasText: "rare.rs" }).first().click();
  await expect(page.locator("#evidence")).toContainText("rare.rs");
  await page.getByRole("button", { name: "Close viewer" }).click();
  await page.getByRole("button", { name: "05 Label review" }).click();
  await page.locator("#content select").first().selectOption("train");
  await page.getByRole("button", { name: "Run audit", exact: true }).click();
  await page
    .getByRole("row")
    .filter({ hasText: "guard_conflict.py" })
    .first()
    .click();
  await expect(page.locator("#evidence")).toContainText("LABEL: safe");
  await expect(page.locator("#evidence")).toContainText("LABEL: unsafe");
  await page
    .locator("#viewer-bottom select")
    .nth(1)
    .selectOption("suspected mislabel");
  await page.getByRole("button", { name: "Save decision" }).click();
  await expect(page.getByText("Saved to this snapshot")).toBeVisible();
  await page.getByRole("button", { name: "Close viewer" }).click();
  await page.getByRole("button", { name: "02 Embedding atlas" }).click();
  await expect(page.locator(".js-plotly-plot")).toBeVisible();
  await page
    .locator("#content select")
    .nth(1)
    .selectOption("training_similarity");
  await expect(
    page.getByText("Grey points have no saved match", { exact: false }),
  ).toBeVisible();
  await expect
    .poll(() =>
      page
        .locator(".atlas")
        .evaluate(
          (n) => n.data?.find((t) => t.name === "No saved match")?.marker.color,
        ),
    )
    .toBe("#b8c4bf");
  for (const method of ["umap", "tsne", "pca"]) {
    await page.locator("#content select").first().selectOption(method);
    await page.getByRole("button", { name: "Fit / open projection" }).click();
    await expect(
      page.getByText("points shown /", { exact: false }),
    ).toBeVisible({ timeout: 90000 });
    await expect(page.locator("#job")).toBeHidden({ timeout: 90000 });
  }
  // Plotly's event uses exactly the point row ID shipped in customdata.
  await page
    .locator(".atlas")
    .evaluate((n) => n.emit("plotly_click", { points: [{ customdata: 3 }] }));
  await expect(page.locator("#evidence")).toContainText("shell.js");
  await page
    .locator("#evidence")
    .getByRole("button", { name: "Format", exact: true })
    .click();
  await expect(page.getByText("Formatted copy", { exact: true })).toBeVisible();
  await expect(page.locator(".view-lines")).toContainText("function run(x)");
  await page.getByRole("button", { name: "Close viewer" }).click();
  await page.getByRole("button", { name: "07 Source explorer" }).click();
  await page
    .getByRole("row")
    .filter({ hasText: "missing.weird" })
    .first()
    .click();
  await expect(page.locator("#evidence select").first()).toHaveValue(
    "plaintext",
  );
  await expect(page.locator(".view-lines")).toContainText("<script>");
  expect(await page.evaluate(() => window.pwned)).toBeUndefined();
  await page.locator("#evidence select").first().selectOption("html");
  await page.getByRole("button", { name: "Close viewer" }).click();
  await page.getByRole("row").filter({ hasText: "broken.py" }).first().click();
  await page
    .locator("#evidence")
    .getByRole("button", { name: "Format", exact: true })
    .click();
  await expect(
    page.getByText("Format failed:", { exact: false }),
  ).toBeVisible();
  await expect(page.locator(".view-lines")).toContainText("broken");
  await page.getByRole("button", { name: "Close viewer" }).click();
  await page.reload();
  expect(
    (
      await (
        await request.get(base + "/reviews?a=0&b=1&workflow=leakage")
      ).json()
    ).notes,
  ).toBe("Browser offline review");
  expect(outbound).toEqual([]);
  expect(errors).toEqual([]);
});
