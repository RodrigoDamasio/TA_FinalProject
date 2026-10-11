import { expect, test } from "@playwright/test";
import { ask, chooseSources, expectAccessible, expectNoHorizontalScroll, PROD, report, toolCards } from "./helpers";

test.skip(PROD, "local fake-backend flows (production uses production.spec.ts)");

test("a full run: live board and tool-call card, then the cited report and run details", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText(/Demo mode:/)).toBeVisible();
  await expectAccessible(page);
  await ask(page, "What is retrieval augmented generation and when should I use it?");
  // live: an agent is working and the Researcher's tool call appears before the report
  await expect(page.getByRole("listitem", { name: /: working$/ }).first()).toBeVisible();
  const card = toolCards(page).first();
  await expect(card).toBeVisible();
  await expect(card).toHaveAttribute("data-tool", "search_web");
  await expect(card).toContainText("via searxng");
  await expect(card).toContainText("evidence E1–E3");
  await expectAccessible(page);

  const r = report(page);
  await expect(r).toBeVisible();
  await expect(page.getByTestId("evidence-counter").last()).toHaveText("Evidence: 3");
  await expect(r.getByRole("heading", { level: 2 })).toHaveText("What is retrieval augmented generation and when should I use it?");
  const chip = r.getByRole("link", { name: /^E1: / }).first();
  await chip.focus();
  await expect(page.getByRole("tooltip")).toContainText("example.org");
  await expect(r.getByRole("region", { name: "References" }).getByRole("listitem")).toHaveCount(3);
  // "How the agents worked" collapses when the run completes, leaving its summary
  const activity = page.getByText(/^How the agents worked/);
  await expect(activity).toContainText(/1 tool call · 3 evidence · \d+ decisions/);
  await activity.click();
  for (const agent of ["Supervisor", "Researcher", "Writer", "Reviewer"]) {
    await expect(page.getByRole("listitem", { name: `${agent}: done` })).toBeVisible();
  }
  await page.getByText("Run details").click();
  await expect(page.getByLabel("Run metadata")).toContainText("demo mode (no LLM)");
  await expect(page.getByRole("region", { name: "Per-agent usage" })).toContainText("Researcher");
  await expect(page.getByRole("list", { name: "Transcript" }).getByText(/^Supervisor · decision \d of 5$/)).toHaveCount(4);
  await expect(page).toHaveURL(/\?chat=conv_[a-f0-9]{16}$/);
  await expectAccessible(page);

  // reload: the chat and the run come back from ?chat= and storage
  await page.reload();
  await expect(report(page)).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Chats" }).getByText("Chats (1)")).toBeVisible();
});

test("documents only: the Researcher calls search_documents and cites the demo library", async ({ page }) => {
  await page.goto("/");
  await chooseSources(page, ["Documents"]);
  await page.getByRole("radio", { name: "Brief" }).check({ force: true });
  await ask(page, "How should webhooks be signed and retried?");
  const r = report(page);
  await expect(r).toBeVisible();
  await expect(toolCards(page)).toHaveCount(1);
  await expect(toolCards(page).first()).toHaveAttribute("data-tool", "search_documents");
  await expect(r.getByRole("region", { name: "References" })).toContainText("standard-webhooks-spec.md");
  await expect(page.getByText("Brief · Documents")).toBeVisible();
});

test("upload a document, see it indexed, then searched in a run", async ({ page }) => {
  await page.goto("/library");
  await expect(page.getByRole("region", { name: "Demo library" }).getByRole("listitem").first()).toBeVisible();
  await expectAccessible(page);
  await page.getByLabel("Choose files").setInputFiles("e2e/fixtures/quokka-notes.md");
  const row = page.getByRole("listitem", { name: "quokka-notes.md" });
  await expect(row).toHaveAttribute("data-status", "ready");
  await expectAccessible(page);

  await page.getByRole("link", { name: "Research", exact: true }).click();
  await chooseSources(page, ["Documents"]);
  await ask(page, "What are quokka protocol zebra signatures?");
  const r = report(page);
  await expect(r).toBeVisible();
  await expect(toolCards(page).first()).toHaveAttribute("data-tool", "search_documents");
  await expect(r.getByRole("region", { name: "References" })).toContainText("quokka-notes.md");

  // delete it again (with confirmation)
  await page.goto("/library");
  await page.getByRole("button", { name: "Delete quokka-notes.md" }).click();
  await page.getByRole("group", { name: "Confirm deleting quokka-notes.md" }).getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("No uploads yet.")).toBeVisible();
});

test("approval: reject with feedback → revision → approve", async ({ page }) => {
  await page.goto("/");
  await page.getByText(/^Advanced:/).click();
  await page.getByLabel("Require my approval before the final report").check();
  await ask(page, "Compare SQLite and PostgreSQL for a small web app.");
  await expect(page.getByRole("heading", { name: "Your approval is needed" })).toBeVisible();
  await expectAccessible(page);
  await page.getByLabel("Feedback for a revision (optional)").fill("Add one practical example.");
  await page.getByRole("button", { name: "Reject" }).click();
  await expect(page.getByText("No revisions left: rejecting cancels the run.")).toBeVisible();
  // the activity is collapsed while the run waits (it is not live): open it
  await page.getByText(/^How the agents worked/).last().click();
  await expect(page.getByRole("list", { name: "Activity feed" }).last()).toContainText("Add one practical example.");
  await page.getByRole("button", { name: "Approve" }).click();
  await expect(report(page)).toBeVisible();
});

test("errors: backend unreachable, then rate limited with a Retry-After countdown", async ({ page }) => {
  await page.goto("/");
  await page.route("**/research", (route) => (route.request().method() === "POST" ? route.abort() : route.continue()));
  await ask(page, "Summarize the history of Python.");
  await expect(page.getByRole("main").getByRole("alert")).toContainText("Can't reach the research service");
  await page.unroute("**/research");
  await page.route("**/research", (route) =>
    route.request().method() === "POST"
      ? route.fulfill({
          status: 429,
          headers: {
            "Content-Type": "application/problem+json",
            "Retry-After": "30",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Expose-Headers": "Retry-After",
          },
          body: JSON.stringify({ type: "http://localhost:8000/problems/rate-limited", title: "Too many requests.", status: 429 }),
        })
      : route.continue(),
  );
  await page.getByRole("button", { name: "Research", exact: true }).click();
  await expect(page.getByRole("main").getByRole("alert")).toContainText("You started too many runs");
  await expect(page.getByText(/You can send again in \d+ s/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Research", exact: true })).toBeDisabled();
  const first = await page.getByText(/You can send again in \d+ s/).textContent();
  await page.waitForTimeout(2100);
  const later = await page.getByText(/You can send again in \d+ s/).textContent();
  expect(Number(later?.match(/(\d+) s/)?.[1])).toBeLessThan(Number(first?.match(/(\d+) s/)?.[1]));
});

test("phone width (375 px): no horizontal scroll on home, run and library", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 740 });
  await page.goto("/");
  await expectNoHorizontalScroll(page);
  await ask(page, "What is retrieval augmented generation and when should I use it?");
  await expect(toolCards(page).first()).toBeVisible();
  await expectNoHorizontalScroll(page);
  await expect(report(page)).toBeVisible();
  await report(page).getByRole("link", { name: /^E1: / }).first().focus();
  await expect(page.getByRole("tooltip")).toBeVisible();
  await expectNoHorizontalScroll(page);
  await page.getByText("Run details").click();
  await expectNoHorizontalScroll(page);
  await expectAccessible(page);
  await page.goto("/library");
  await expect(page.getByRole("region", { name: "Demo library" }).getByRole("listitem").first()).toBeVisible();
  await expectNoHorizontalScroll(page);
});
