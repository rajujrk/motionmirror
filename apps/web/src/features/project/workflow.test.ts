import { describe, expect, it } from "vitest";
import type { Asset, Job, Selection } from "@/lib/types";
import { deriveSteps, deriveWorkflow, failureMessage, firstOpenStep, stageLabel } from "./workflow";

const asset = (over: Partial<Asset>): Asset =>
  ({ id: "a1", role: "DRIVING_VIDEO", status: "READY", media: null, ...over }) as Asset;
const selection = (over: Partial<Selection>): Selection =>
  ({ id: "s1", sourceAssetId: "a1", revision: 1, annotations: {}, ...over }) as Selection;
const job = (over: Partial<Job>): Job =>
  ({ id: "j1", kind: "PREPROCESS", state: "SUCCEEDED", inputs: { selection_id: "s1" }, outputs: [], ...over }) as Job;

describe("deriveWorkflow", () => {
  it("uses the newest selection revision", () => {
    const state = deriveWorkflow([asset({})], [selection({ id: "s1", revision: 1 }), selection({ id: "s2", revision: 2 })], [], []);
    expect(state.selection?.id).toBe("s2");
  });

  it("doesn't reuse a mask made for an earlier selection", () => {
    const state = deriveWorkflow(
      [asset({})],
      [selection({ id: "s2", revision: 2 })],
      [],
      [job({ inputs: { selection_id: "s1" } })],
    );
    expect(state.maskJob).toBeUndefined();
  });
});

describe("deriveSteps", () => {
  it("opens with the clip step", () => {
    const steps = deriveSteps(deriveWorkflow([], [], [], []));
    expect(firstOpenStep(steps)).toBe("clip");
    expect(steps.find((step) => step.id === "dancer")?.available).toBe(false);
  });

  it("keeps later steps locked while the clip is still being checked", () => {
    const steps = deriveSteps(deriveWorkflow([asset({ status: "QUARANTINED" })], [], [], []));
    expect(steps.find((step) => step.id === "dancer")?.available).toBe(false);
  });

  it("needs a finished mask and a profile before generating", () => {
    const ready = deriveWorkflow(
      [asset({})],
      [selection({})],
      [{ id: "p1" } as never],
      [job({ state: "RUNNING" })],
    );
    expect(deriveSteps(ready).find((step) => step.id === "generate")?.available).toBe(false);
    const finished = deriveWorkflow([asset({})], [selection({})], [{ id: "p1" } as never], [job({})]);
    expect(deriveSteps(finished).find((step) => step.id === "generate")?.available).toBe(true);
  });
});

describe("messages", () => {
  it("names real stages and never invents progress", () => {
    expect(stageLabel("generate")).toBe("Generating");
    expect(stageLabel(null)).toBe("Starting");
  });

  it("explains out-of-memory with a next step", () => {
    expect(failureMessage({ failureCode: "CUDA_OOM", failureDetail: null })).toContain("Preview preset");
  });

  it("falls back to the detail for unknown codes", () => {
    expect(failureMessage({ failureCode: "OTHER", failureDetail: "Worker note" })).toBe("Worker note");
  });
});
