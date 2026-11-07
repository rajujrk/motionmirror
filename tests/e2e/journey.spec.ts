import { expect, test } from "@playwright/test";
import { compose, createProject, fixtures, generatePreview, prepareToGenerate, signIn } from "./support";

const files = fixtures();
const SHOTS = process.env.MM_E2E_SCREENSHOTS;

test("the whole mock journey works through real services and survives a restart", async ({ page, request }) => {
  await signIn(page);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/projects.png`, fullPage: true });
  const name = `Journey ${Date.now()}`;
  await createProject(page, name);
  await prepareToGenerate(page, files);
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/mask.png`, fullPage: true });

  // Mock output is never allowed to look real.
  await expect(page.getByText("MOCK / NO AI REPLACEMENT")).toBeVisible();

  await generatePreview(page);
  await expect(page.getByTestId("job-status").first()).toHaveAttribute("data-state", "SUCCEEDED");
  await expect(page.getByText("MOCK / NO AI REPLACEMENT").first()).toBeVisible();
  await expect(page.getByLabel("Generated result")).toBeVisible();
  await expect(page.getByLabel("Original clip")).toBeVisible();
  if (SHOTS) await page.screenshot({ path: `${SHOTS}/results.png`, fullPage: true });

  // The MP4 downloads through a short-lived signed URL.
  const href = await page.getByRole("link", { name: "Download MP4" }).getAttribute("href");
  const download = await request.get(href!);
  expect(download.ok()).toBeTruthy();
  expect(download.headers()["content-type"]).toContain("video/mp4");
  expect((await download.body()).length).toBeGreaterThan(1000);

  // The run says what it did and didn't use.
  await page.getByText("What this run used").click();
  await expect(page.getByText("None. No model ran")).toBeVisible();

  // Everything is durable across an API restart.
  const url = page.url();
  compose("restart", "api");
  await expect(async () => {
    const response = await request.get("/actuator/health/liveness").catch(() => null);
    const health = await page.request.get(`${new URL(url).origin}/api/v1/auth/me`);
    expect(response === null || health.status() === 200 || health.status() === 401).toBeTruthy();
    expect(health.status()).toBe(200);
  }).toPass({ timeout: 120_000 });
  await page.goto(url);
  await expect(page.getByTestId("project-name")).toHaveText(name);
  await expect(page.getByTestId("job-status").first()).toHaveAttribute("data-state", "SUCCEEDED");
});

test("a job waits while no worker runs, can be cancelled and retried, and then finishes", async ({ page }) => {
  await signIn(page);
  await createProject(page, `Recovery ${Date.now()}`);
  await prepareToGenerate(page, files);

  compose("stop", "worker-mock");
  try {
    await generatePreview(page);
    await expect(page.getByText("Waiting for a worker")).toBeVisible();

    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.getByTestId("job-status").first()).toHaveAttribute("data-state", "CANCELLED");

    await page.getByRole("button", { name: "Retry" }).click();
    await expect(page.getByText("Waiting for a worker")).toBeVisible();
  } finally {
    compose("start", "worker-mock");
  }
  await expect(page.getByTestId("job-status").first()).toHaveAttribute("data-state", "SUCCEEDED");
  await expect(page.getByRole("link", { name: "Download MP4" })).toBeVisible();
});

test("a file that isn't really a video is rejected with an explanation", async ({ page }) => {
  await signIn(page);
  await createProject(page, `Rejected ${Date.now()}`);
  await page.getByLabel("I have permission to use this video").check();
  await page.getByLabel("Choose a clip").setInputFiles(files.notVideo);
  await expect(page.getByText("isn't a readable video")).toBeVisible();
  await expect(page.getByRole("button", { name: "Dancer" })).toBeDisabled();
});

test("signed-out visitors are sent to sign in and security headers are present", async ({ page, context }) => {
  await context.clearCookies();
  const response = await page.goto("/");
  const headers = response!.headers();
  expect(headers["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(headers["cache-control"]).toContain("no-store");
  await page.goto("/projects");
  await expect(page.getByLabel("Username or email")).toBeVisible();
});

test("signing out ends the session", async ({ page, request }) => {
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("heading", { name: "Put yourself in the dance." })).toBeVisible();
  const me = await page.request.get("/api/v1/auth/me");
  expect(me.status()).toBe(401);
  expect(request).toBeTruthy();
});
