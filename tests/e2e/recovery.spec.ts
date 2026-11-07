import { expect, type APIRequestContext, type Page, test } from "@playwright/test";
import { compose, createProject, fixtures, generatePreview, prepareToGenerate, signIn } from "./support";

/**
 * Real failures through the running stack, with short deadlines and a job that holds its lease for a while.
 * Run through make test-failure-recovery, which starts the stack with infra/compose/recovery-test.yaml.
 */
const files = fixtures();
const OVERRIDE = "infra/compose/recovery-test.yaml";

test.skip(!process.env.MM_E2E_RECOVERY, "run through make test-failure-recovery");
test.describe.configure({ mode: "serial", timeout: 420_000 });

function stack(...args: string[]): void {
  compose("-f", OVERRIDE, ...args);
}

type Job = { id: string; kind: string; state: string; attemptCount: number; outputs: { name: string }[] };

async function latestGeneration(request: APIRequestContext, page: Page): Promise<Job> {
  const projectId = page.url().split("/projects/")[1];
  const jobs = await (await request.get(`/api/v1/projects/${projectId}/jobs`)).json();
  const items: Job[] = jobs.items ?? jobs.content ?? jobs;
  return items.filter((job) => job.kind === "PREVIEW")[0]!;
}

/** Real progress from a real attempt, so the failure lands while work is actually in flight. */
async function startHeldJob(page: Page): Promise<void> {
  await signIn(page);
  await createProject(page, `Recovery ${Date.now()}`);
  await prepareToGenerate(page, files, 180_000);
  await generatePreview(page);
  await expect(page.getByTestId("job-status").first()).toHaveAttribute("data-state", "RUNNING", { timeout: 120_000 });
}

async function finishes(page: Page): Promise<void> {
  await expect(page.getByTestId("job-status").first()).toHaveAttribute("data-state", "SUCCEEDED", { timeout: 300_000 });
  await expect(page.getByRole("link", { name: "Download MP4" })).toBeVisible();
}

test("a worker killed mid-job is recovered by a new attempt", async ({ page, request }) => {
  await startHeldJob(page);
  stack("kill", "worker-mock");
  try {
    // With nobody heartbeating the lease runs out, and the reconciler schedules another attempt.
    await page.waitForTimeout(30_000);
  } finally {
    stack("start", "worker-mock");
  }
  await finishes(page);
  const job = await latestGeneration(page.request ?? request, page);
  expect(job.attemptCount).toBeGreaterThanOrEqual(2);
  expect(job.outputs.map((output) => output.name).sort()).toEqual(["manifest.json", "result.mp4"]);
});

test("an API restart mid-job loses neither the job nor the session", async ({ page, request }) => {
  await startHeldJob(page);
  stack("restart", "api");
  await finishes(page);
  await page.reload();
  await expect(page.getByRole("link", { name: "Download MP4" })).toBeVisible();
  const job = await latestGeneration(page.request ?? request, page);
  expect(job.state).toBe("SUCCEEDED");
});

test("a worker frozen past its lease can't overwrite the attempt that replaced it", async ({ page, request }) => {
  await startHeldJob(page);
  const before = await latestGeneration(page.request ?? request, page);
  stack("pause", "worker-mock");
  try {
    await page.waitForTimeout(30_000);
  } finally {
    stack("unpause", "worker-mock");
  }
  await finishes(page);
  const job = await latestGeneration(page.request ?? request, page);
  expect(job.id).toBe(before.id);
  expect(job.attemptCount).toBeGreaterThanOrEqual(2);
  // One attempt's results, not two, even though the frozen worker woke up still believing in its lease.
  expect(job.outputs.map((output) => output.name).sort()).toEqual(["manifest.json", "result.mp4"]);
});

test("a storage outage during a job is retried once storage is back", async ({ page }) => {
  await startHeldJob(page);
  stack("stop", "seaweedfs");
  try {
    await page.waitForTimeout(20_000);
  } finally {
    stack("start", "seaweedfs");
  }
  await finishes(page);
});
