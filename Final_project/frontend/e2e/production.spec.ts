import { expect, test } from "@playwright/test";
import { expectAccessible, PROD, report } from "./helpers";

test.skip(!PROD, "production only: BASE_URL=https://<vercel-domain> npm run test:e2e");

// Production smoke (FRONTEND_PLAN §3.7, §6): health and one sample replay at 0 calls. A real
// run is opt-in (E2E_REAL_RUN=1) and run by the user only: it spends Gemini quota.
test("health is reachable and the service banner renders", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByLabel("Research question")).toBeVisible();
  await expectAccessible(page);
});

test("one real run, end to end (opt-in)", async ({ page }) => {
  test.skip(!process.env.E2E_REAL_RUN, "set E2E_REAL_RUN=1 to spend real quota");
  test.setTimeout(300_000);
  await page.goto("/");
  await page.getByLabel("Research question").fill("When should I use RAG instead of fine-tuning?");
  await page.getByRole("button", { name: "Research", exact: true }).click();
  await expect(report(page)).toBeVisible({ timeout: 280_000 });
});
