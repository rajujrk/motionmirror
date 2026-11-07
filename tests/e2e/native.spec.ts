import { expect, test } from "@playwright/test";
import { createProject, fixtures, generatePreview, prepareToGenerate, signIn } from "./support";

/**
 * Real tracking and pose through the whole system: browser, API, broker and a worker on this machine.
 * Needs make up-mac-native and make worker-native-mac, downloaded weights, and a real clip of one person,
 * because synthetic video has nobody to track: MM_E2E_REAL_CLIP=$PWD/clip.mp4 pnpm exec playwright test native
 */
const realClip = process.env.MM_E2E_REAL_CLIP;
const SHOTS = process.env.MM_E2E_SCREENSHOTS;

test.skip(!realClip, "set MM_E2E_REAL_CLIP to run real tracking");

test("a real clip is tracked for real and every result says how it was made", async ({ page }) => {
  test.setTimeout(1_200_000);
  await signIn(page);
  await createProject(page, `Real tracking ${Date.now()}`);
  await prepareToGenerate(page, { clip: realClip!, reference: fixtures().reference }, 900_000);

  // Real tracking, but not on acceptance-grade hardware, and the page must say so.
  await expect(page.getByText("Experimental model output")).toBeVisible();
  await expect(page.getByText("No tracking ran")).toHaveCount(0);
  await expect(page.getByLabel("Mask preview")).toHaveJSProperty("readyState", 4);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/real-mask.png`, fullPage: true });

  await generatePreview(page);
  const job = page.getByTestId("job-status").first();
  await expect(job).toHaveAttribute("data-state", "SUCCEEDED", { timeout: 120_000 });
  // Generation stays mock on this machine, and that label must not be lost next to real masks.
  await expect(page.getByText("MOCK / NO AI REPLACEMENT").first()).toBeVisible();
});
