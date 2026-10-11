import { expect, test } from "@playwright/test";
import { ask, expectAccessible, expectNoHorizontalScroll, PROD, report } from "./helpers";

test.skip(PROD, "local fake-backend flows (production uses production.spec.ts)");

// v1.2 conversations (plan Improving_Chat_UI_UX §10, E2E row) against the fake-mode backend.
test("a follow-up in the same chat, a new chat, reload, and a ?chat= link in a fresh browser", async ({ page, browser }) => {
  const chats = page.getByRole("navigation", { name: "Chats" });
  const turn1 = page.getByRole("button", { name: /^Turn 1/ });
  const turn2 = page.getByRole("button", { name: /^Follow-up · turn 2/ });

  await page.goto("/");
  await ask(page, "When should I use RAG instead of fine-tuning?");
  await expect(report(page)).toBeVisible();
  await expect(page).toHaveURL(/\?chat=conv_[a-f0-9]{16}$/);
  const chatUrl = page.url();

  // follow-up: 2 characters are enough; turn 1 collapses, turn 2 is expanded
  await expect(page.getByLabel("Research question")).toHaveAttribute("placeholder", "Ask a follow-up…");
  await ask(page, "and cost?");
  await expect(turn2).toHaveAttribute("aria-expanded", "true");
  await expect(turn2).toBeFocused();
  await expect(turn1).toHaveAttribute("aria-expanded", "false");
  await expect(report(page)).toBeVisible();
  await expect(page.getByRole("article", { name: "Report" })).toHaveCount(1);
  await expect(page.getByText(/^How the agents worked/).last()).toContainText(/1 tool call · \d+ evidence · \d+ decisions/);
  await expect(chats.getByText("Chats (1)")).toBeVisible();
  await expect(chats.getByText(/^2 turns/)).toBeVisible();
  expect(page.url()).toBe(chatUrl);

  // a turn opens and closes on its own; Collapse all / Expand all
  await turn1.click();
  await expect(page.getByRole("article", { name: "Report" })).toHaveCount(2);
  await page.getByRole("button", { name: "Collapse all" }).click();
  await expect(page.getByRole("article", { name: "Report" })).toHaveCount(0);
  await page.getByRole("button", { name: "Expand all" }).click();
  await expect(page.getByRole("article", { name: "Report" })).toHaveCount(2);

  // New chat: an empty thread; the next question is a chat of its own
  await chats.getByRole("button", { name: "New chat" }).click();
  await expect(page.getByText("Ask a research question.")).toBeVisible();
  await expect(page.getByRole("article", { name: "Report" })).toHaveCount(0);
  await ask(page, "What is retrieval augmented generation?");
  await expect(report(page)).toBeVisible();
  await expect(chats.getByText("Chats (2)")).toBeVisible();
  await expect(chats.getByRole("link", { name: /What is retrieval augmented generation/ })).toHaveAttribute("aria-current", "page");

  // reload keeps both chats; the first one opens from the sidebar
  await page.reload();
  await expect(chats.getByText("Chats (2)")).toBeVisible();
  await expect(report(page)).toBeVisible();
  await chats.getByRole("link", { name: /When should I use RAG/ }).click();
  await expect(turn2).toBeVisible();
  expect(page.url()).toBe(chatUrl);

  // a ?chat= link in a fresh browser context: the chat is rebuilt from GET /conversations
  const context = await browser.newContext();
  const fresh = await context.newPage();
  await fresh.goto(chatUrl);
  await expect(fresh.getByRole("button", { name: /^Follow-up · turn 2/ })).toHaveAttribute("aria-expanded", "true");
  await expect(fresh.getByRole("button", { name: /^Turn 1/ })).toHaveAttribute("aria-expanded", "false");
  await expect(report(fresh)).toBeVisible();
  await expect(fresh.getByRole("navigation", { name: "Chats" }).getByText("Chats (1)")).toBeVisible();

  // a collapsed chat: axe and phone width
  await fresh.getByRole("button", { name: "Collapse all" }).click();
  await expect(fresh.getByRole("article", { name: "Report" })).toHaveCount(0);
  await expectAccessible(fresh);
  await fresh.setViewportSize({ width: 375, height: 740 });
  await expectNoHorizontalScroll(fresh);
  await expectAccessible(fresh);
  await context.close();
});

test("a ?run= link (Telegram, callbacks) opens the chat of that run at its turn", async ({ page, browser }) => {
  await page.goto("/");
  await ask(page, "When should I use RAG instead of fine-tuning?");
  await expect(report(page)).toBeVisible();
  const firstTurn = await page.getByRole("list", { name: "Turns" }).locator("li[id^=turn-run_]").first().getAttribute("id");
  await ask(page, "and cost?");
  await expect(page.getByRole("button", { name: /^Follow-up · turn 2/ })).toHaveAttribute("aria-expanded", "true");
  await expect(report(page)).toBeVisible();

  const context = await browser.newContext();
  const fresh = await context.newPage();
  await fresh.goto(new URL(`/?run=${firstTurn!.replace("turn-", "")}`, page.url()).toString());
  await expect(fresh.getByRole("button", { name: /^Turn 1/ })).toHaveAttribute("aria-expanded", "true");
  await expect(fresh.getByRole("button", { name: /^Follow-up · turn 2/ })).toBeVisible();
  await context.close();
});
