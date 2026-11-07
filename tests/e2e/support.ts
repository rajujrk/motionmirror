import { execFileSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { expect, type Page } from "@playwright/test";

const ROOT = path.join(import.meta.dirname, "../..");
const FIXTURES = path.join(ROOT, ".data/e2e-fixtures");

/** Synthetic fixtures, generated on demand, so no real person's likeness is involved. */
export function fixtures(): { clip: string; reference: string; notVideo: string } {
  mkdirSync(FIXTURES, { recursive: true });
  const clip = path.join(FIXTURES, "dance.mp4");
  const reference = path.join(FIXTURES, "reference.png");
  const notVideo = path.join(FIXTURES, "fake.mp4");
  const ffmpeg = (...args: string[]) => execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args]);
  ffmpeg(
    "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=30:duration=3",
    "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", clip,
  ); // prettier-ignore
  ffmpeg("-f", "lavfi", "-i", "color=c=teal:size=720x1280", "-frames:v", "1", "-update", "1", reference);
  execFileSync("/bin/sh", ["-c", `printf '<html><script>alert(1)</script></html>' > "${notVideo}"`]);
  return { clip, reference, notVideo };
}

export function compose(...args: string[]): void {
  execFileSync("docker", ["compose", "--env-file", ".env", "-f", "infra/compose/compose.yaml", "--profile", "lite", ...args], {
    cwd: ROOT,
    stdio: "pipe",
  });
}

export async function signIn(page: Page): Promise<void> {
  await page.goto("/");
  await page.getByRole("link", { name: "Sign in" }).click();
  await page.getByLabel("Username or email").fill("dev.user");
  await page.getByLabel("Password", { exact: true }).fill(process.env.MM_DEV_USER_PASSWORD!);
  await page.getByRole("button", { name: "Sign In" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
}

export async function createProject(page: Page, name: string): Promise<void> {
  await page.getByLabel("New project name").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  await page.getByRole("link", { name }).click();
  await expect(page.getByTestId("project-name")).toHaveText(name);
}

/** Upload a clip, mark the dancer, add a photo and track, leaving the page on a checked mask. */
export async function prepareToGenerate(
  page: Page,
  files: { clip: string; reference: string },
  trackTimeout = 60_000,
): Promise<void> {
  await page.getByLabel("I have permission to use this video").check();
  await page.getByLabel("Choose a clip").setInputFiles(files.clip);

  await expect(page.getByRole("heading", { name: "Mark the dancer" })).toBeVisible();
  const surface = page.getByTestId("selection-surface");
  await expect(surface.locator("video")).toHaveJSProperty("readyState", 4);
  await surface.click();
  await expect(page.getByText("1 mark.")).toBeVisible();
  await page.getByRole("button", { name: "Save selection" }).click();

  await expect(page.getByRole("heading", { name: "Add photos of you" })).toBeVisible();
  await page.getByLabel("These photos are of me").check();
  await page.getByLabel("Choose photos").setInputFiles(files.reference);
  await expect(page.getByLabel("Use this photo")).toBeChecked();
  await page.getByRole("button", { name: "Save photos" }).click();

  await expect(page.getByRole("heading", { name: "Check the mask" })).toBeVisible();
  await page.getByRole("button", { name: "Track the dancer" }).click();
  await expect(page.getByTestId("job-status")).toHaveAttribute("data-state", "SUCCEEDED", { timeout: trackTimeout });
  await expect(page.getByLabel("Mask preview")).toBeVisible();
}

export async function generatePreview(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { name: "Generate" })).toBeVisible();
  await page.getByLabel("I have permission to use this person's likeness").check();
  await page.getByRole("button", { name: "Generate preview" }).click();
  await expect(page.getByRole("heading", { name: "Results" })).toBeVisible();
}
