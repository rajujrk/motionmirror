"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type {
  AppearanceProfile,
  Asset,
  Capabilities,
  Job,
  Me,
  Preset,
  Project,
  ProjectPage,
  Selection,
} from "./types";

const ACTIVE = new Set(["QUEUED", "RUNNING", "RETRY_WAIT", "CANCEL_REQUESTED"]);

export const isActive = (job: Pick<Job, "state">): boolean => ACTIVE.has(job.state);

/** Poll while something is running, slowing down the longer it runs. */
function backoff(updates: number): number {
  return Math.min(1_500 * 1.25 ** updates, 10_000);
}

export const useMe = () => useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/api/v1/auth/me"), staleTime: 60_000 });

export const useProjects = () =>
  useQuery({ queryKey: ["projects"], queryFn: () => api<ProjectPage>("/api/v1/projects?size=50") });

export const useProject = (id: string) =>
  useQuery({ queryKey: ["project", id], queryFn: () => api<Project>(`/api/v1/projects/${id}`) });

export const useAssets = (projectId: string) =>
  useQuery({
    queryKey: ["assets", projectId],
    queryFn: () => api<{ items: Asset[] }>(`/api/v1/projects/${projectId}/assets`).then((page) => page.items),
    refetchInterval: (query) =>
      query.state.data?.some((asset) => asset.status === "QUARANTINED") ? backoff(query.state.dataUpdateCount) : false,
  });

export const useSelections = (projectId: string) =>
  useQuery({
    queryKey: ["selections", projectId],
    queryFn: () =>
      api<{ items: Selection[] }>(`/api/v1/projects/${projectId}/subject-selections`).then((page) => page.items),
  });

export const useProfiles = (projectId: string) =>
  useQuery({
    queryKey: ["profiles", projectId],
    queryFn: () =>
      api<{ items: AppearanceProfile[] }>(`/api/v1/projects/${projectId}/appearance-profiles`).then((page) => page.items),
  });

export const useJobs = (projectId: string) =>
  useQuery({
    queryKey: ["jobs", projectId],
    queryFn: () => api<{ items: Job[] }>(`/api/v1/projects/${projectId}/jobs?size=50`).then((page) => page.items),
    refetchInterval: (query) => (query.state.data?.some(isActive) ? backoff(query.state.dataUpdateCount) : false),
  });

export const usePresets = () =>
  useQuery({
    queryKey: ["presets"],
    queryFn: () => api<{ items: Preset[] }>("/api/v1/presets").then((page) => page.items),
    staleTime: 60_000,
  });

export const useCapabilities = () =>
  useQuery({
    queryKey: ["capabilities"],
    queryFn: () => api<Capabilities>("/api/v1/models/capabilities"),
    staleTime: 15_000,
  });

/** Signed URLs are short-lived bearer capabilities, so they stay in memory and refresh before expiry. */
export const useAssetUrl = (assetId: string | undefined) =>
  useQuery({
    queryKey: ["asset-url", assetId],
    enabled: Boolean(assetId),
    queryFn: () => api<{ url: string }>(`/api/v1/assets/${assetId}/download-url`).then((result) => result.url),
    staleTime: 3 * 60_000,
    gcTime: 4 * 60_000,
  });

export const useOutputUrl = (jobId: string | undefined, name: string) =>
  useQuery({
    queryKey: ["output-url", jobId, name],
    enabled: Boolean(jobId),
    queryFn: () => api<{ url: string }>(`/api/v1/jobs/${jobId}/outputs/${name}/download-url`).then((result) => result.url),
    staleTime: 3 * 60_000,
    gcTime: 4 * 60_000,
  });
