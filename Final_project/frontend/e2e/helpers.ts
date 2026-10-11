import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

export const PROD = Boolean(process.env.BASE_URL);

/** No serious or critical axe violations (WCAG 2.x A/AA). */
export async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  const bad = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
}

export async function expectNoHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

export async function ask(page: Page, question: string) {
  await page.getByLabel("Research question").fill(question);
  await page.getByRole("button", { name: "Research", exact: true }).click();
}

/** Leaves only the given sources checked. */
export async function chooseSources(page: Page, sources: ("Web" | "Wikipedia" | "Documents")[]) {
  for (const s of ["Web", "Wikipedia", "Documents"] as const) {
    const box = page.getByRole("checkbox", { name: s });
    if (sources.includes(s)) await box.check();
    else await box.uncheck();
  }
}

export const report = (page: Page) => page.getByRole("article", { name: "Report" }).last();
export const toolCards = (page: Page) => page.getByTestId("tool-call");
