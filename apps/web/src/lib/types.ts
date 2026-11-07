import type { components } from "@motionmirror/contracts";

/** springdoc marks every record field optional, but the API always sends them. Nullable fields are handled at use. */
type Sent<T> = { [K in keyof T]-?: Exclude<T[K], undefined> };
type Schemas = components["schemas"];

export type Me = Sent<Schemas["Me"]>;
export type Project = Sent<Schemas["ProjectView"]>;
export type ProjectPage = Sent<Omit<Schemas["ProjectPage"], "items">> & { items: Project[] };
export type Preset = Sent<Schemas["PresetView"]>;

export type JsonObject = Record<string, unknown>;

export type AssetMedia = {
  duration_seconds?: number;
  display_width?: number;
  display_height?: number;
  width?: number;
  height?: number;
  frame_rate?: string;
  audio_streams?: number;
  is_vfr?: boolean;
  needs_trim?: boolean;
  warnings?: string[];
};

export type Asset = Omit<Sent<Schemas["AssetView"]>, "media" | "rejectionCode" | "rejectionDetail" | "sizeBytes"> & {
  media: AssetMedia | null;
  rejectionCode: string | null;
  rejectionDetail: string | null;
  sizeBytes: number | null;
};

export type SelectionPoint = { x: number; y: number; positive: boolean; frame: number };
export type SelectionBox = { x: number; y: number; width: number; height: number; frame: number };
export type Annotations = { points?: SelectionPoint[]; box?: SelectionBox };

export type Selection = Omit<Sent<Schemas["Selection"]>, "annotations" | "coordinateMapping"> & {
  annotations: Annotations;
  coordinateMapping: JsonObject;
};

export type Reference = Sent<Schemas["Reference"]>;
export type AppearanceProfile = Omit<Sent<Schemas["Profile"]>, "references"> & { references: Reference[] };

export type JobOutput = Omit<Sent<Schemas["OutputView"]>, "media"> & { media: JsonObject };
export type JobProgress = { stage: string; current: number | null; total: number | null; unit: string | null };

export type JobInputs = {
  selection_id?: string;
  source_asset_id?: string;
  primary_reference_asset_id?: string;
  supplementary_reference_asset_ids?: string[];
  preview_seconds?: number | null;
};

export type Job = {
  id: string;
  projectId: string;
  kind: "VALIDATE" | "PREPROCESS" | "PREVIEW" | "FULL";
  state: "QUEUED" | "RUNNING" | "RETRY_WAIT" | "CANCEL_REQUESTED" | "SUCCEEDED" | "FAILED" | "CANCELLED";
  stage: string | null;
  backendId: string;
  outputLabel: "mock" | "real";
  configRevision: string;
  requestedSettings: JsonObject;
  effectiveSettings: JsonObject;
  inputs: JobInputs;
  attemptCount: number;
  maxAttempts: number;
  nextAttemptAt: string | null;
  failureCode: string | null;
  failureDetail: string | null;
  createdAt: string;
  updatedAt: string;
  finishedAt: string | null;
  progress: JobProgress | null;
  outputs: JobOutput[];
};

export type Capabilities = {
  generateBackend: string;
  preprocessBackend: string;
  generationLabel: "mock" | "real" | "experimental";
};
