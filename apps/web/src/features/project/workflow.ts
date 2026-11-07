import type { AppearanceProfile, Asset, Job, Selection } from "@/lib/types";

export type StepId = "clip" | "dancer" | "you" | "mask" | "generate" | "results";

export type StepInfo = { id: StepId; title: string; available: boolean; done: boolean };

export type WorkflowState = {
  source?: Asset;
  references: Asset[];
  selection?: Selection;
  profile?: AppearanceProfile;
  maskJob?: Job;
  generations: Job[];
};

/** The newest wins everywhere, so a correction or a re-upload moves the workflow forward. */
export function deriveWorkflow(
  assets: Asset[],
  selections: Selection[],
  profiles: AppearanceProfile[],
  jobs: Job[],
): WorkflowState {
  const source = assets.find((asset) => asset.role === "DRIVING_VIDEO");
  const selection = source
    ? selections
        .filter((candidate) => candidate.sourceAssetId === source.id)
        .sort((left, right) => right.revision - left.revision)[0]
    : undefined;
  return {
    source,
    references: assets.filter((asset) => asset.role === "TARGET_REFERENCE"),
    selection,
    profile: profiles[0],
    maskJob: selection
      ? jobs.find((job) => job.kind === "PREPROCESS" && job.inputs.selection_id === selection.id)
      : undefined,
    generations: jobs.filter((job) => job.kind === "PREVIEW" || job.kind === "FULL"),
  };
}

export function deriveSteps(state: WorkflowState): StepInfo[] {
  const clipReady = state.source?.status === "READY";
  const maskReady = state.maskJob?.state === "SUCCEEDED";
  return [
    { id: "clip", title: "Clip", available: true, done: clipReady },
    { id: "dancer", title: "Dancer", available: clipReady, done: Boolean(state.selection) },
    { id: "you", title: "You", available: clipReady, done: Boolean(state.profile) },
    { id: "mask", title: "Mask", available: Boolean(state.selection), done: maskReady },
    { id: "generate", title: "Generate", available: maskReady && Boolean(state.profile), done: state.generations.length > 0 },
    { id: "results", title: "Results", available: state.generations.length > 0, done: false },
  ];
}

export function firstOpenStep(steps: StepInfo[]): StepId {
  return steps.find((step) => step.available && !step.done)?.id ?? "results";
}

const STAGE_LABELS: Record<string, string> = {
  validate: "Checking the file",
  decode: "Reading the frames",
  segment: "Finding the dancer",
  extract_motion: "Tracking body and face",
  prepare_reference: "Preparing your photo",
  generate: "Generating",
  composite: "Blending into the scene",
  encode: "Encoding the video",
  upload: "Saving",
  verify: "Checking the result",
};

export const stageLabel = (stage: string | null | undefined): string =>
  (stage && STAGE_LABELS[stage]) || "Starting";

const FAILURES: Record<string, string> = {
  CUDA_OOM: "The GPU ran out of memory at these settings. Try the Preview preset or a shorter clip.",
  TARGET_LOST: "The dancer was lost during tracking. Add more marks on the frames where they go wrong.",
  INVALID_MEDIA: "This file can't be used. Upload a different one.",
  ATTEMPT_TIMEOUT: "This ran past the time limit. Try a shorter clip or the Preview preset.",
  MODEL_MISSING: "The model isn't installed on the GPU worker. An operator needs to run make models-download.",
  UNSUPPORTED_CAPABILITY: "This backend can't do what these settings ask for. Change the settings and try again.",
  ATTEMPTS_EXHAUSTED: "This failed on every attempt. Retry, or check the job details.",
  LEASE_EXPIRED: "The worker stopped responding. Retry when a worker is available.",
};

export const failureMessage = (job: Pick<Job, "failureCode" | "failureDetail">): string =>
  (job.failureCode && FAILURES[job.failureCode]) || job.failureDetail || "This job failed. Retry to run it again.";
