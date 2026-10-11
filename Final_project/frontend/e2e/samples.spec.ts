import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { expectAccessible, expectNoHorizontalScroll, report, toolCards } from "./helpers";

// The backend serves samples/runs/ (exported by `python -m eval.run --export-samples`); the
// repo has none yet, so locally /samples is answered with a run recorded from the fake-mode
// backend (tests/fixtures/run-completed.json). Against production the real samples are used.
const recorded = JSON.parse(readFileSync(path.join(__dirname, "../tests/fixtures/run-completed.json"), "utf8"));
const SAMPLE = { id: "rag-basics", title: "RAG basics", question: recorded.question };

async function serveSamples(page: Page) {
  if (process.env.BASE_URL) return;
  const cors = { "Access-Control-Allow-Origin": "*", "Content-Type": "application/json" };
  await page.route("**/samples", (route) => route.fulfill({ headers: cors, body: JSON.stringify([SAMPLE]) }));
  await page.route(`**/samples/${SAMPLE.id}`, (route) =>
    route.fulfill({ headers: cors, body: JSON.stringify({ ...recorded, sample: true }) }),
  );
}

test("a sample replays through the board and report without starting a run", async ({ page }) => {
  const posts: string[] = [];
  page.on("request", (r) => r.method() === "POST" && posts.push(r.url()));
  await serveSamples(page);
  await page.goto("/");
  const cards = page.getByRole("region", { name: "Samples" }).getByRole("button");
  await expect(cards.first()).toBeVisible();
  await cards.first().click();
  await expect(page.getByText("Sample run — replay (0 AI calls)")).toBeVisible();
  await expect(toolCards(page).first()).toBeVisible();
  await expect(report(page)).toBeVisible({ timeout: 40_000 });
  await expect(page.getByRole("listitem", { name: "Writer: done" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Run it for real" })).toBeVisible();
  await expectAccessible(page);
  expect(posts).toEqual([]);
});

test("a sample at phone width: no horizontal scroll", async ({ page }) => {
  await serveSamples(page);
  await page.setViewportSize({ width: 375, height: 740 });
  await page.goto("/");
  await page.getByRole("region", { name: "Samples" }).getByRole("button").first().click();
  await expect(report(page)).toBeVisible({ timeout: 40_000 });
  await expectNoHorizontalScroll(page);
});
