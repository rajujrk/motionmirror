import type { components } from "@motionmirror/contracts";

type Schemas = components["schemas"];

export type Me = Schemas["Me"];
export type Project = Schemas["ProjectView"];
export type ProjectPage = Schemas["ProjectPage"];
export type Preset = Schemas["PresetView"];

export type JsonObject = Record<string, unknown>;

export type AssetMedia = {
  duration_seconds?: number;
  display_width?: number;
  display_height?: number;
  width?: number;
  height?: number;
  frame_rate?: string;
  audio_streams?: number;
  needs_trim?: boolean;
  warnings?: string[];
};

export type Asset = Omit<Schemas["AssetView"], "media"> & { media?: AssetMedia | null };

export type Annotations = {
  points?: { x: number; y: number; positive: boolean }[];
  box?: { x: number; y: number; width: number; height: number };
};

export type Selection = Omit<Schemas["Selection"], "annotations" | "coordinateMapping"> & {
  annotations: Annotations;
  coordinateMapping: JsonObject;
};

export type AppearanceProfile = Schemas["Profile"];

export type JobOutput = Omit<Schemas["OutputView"], "media"> & { media?: JsonObject };

export type Job = Omit<Schemas["JobView"], "effectiveSettings" | "requestedSettings" | "inputs" | "outputs"> & {
  effectiveSettings: JsonObject;
  requestedSettings: JsonObject;
  inputs: JsonObject;
  outputs: JobOutput[];
};

export type Capabilities = Schemas["Capabilities"];
