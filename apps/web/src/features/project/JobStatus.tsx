"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ErrorNote, Hint } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { isActive } from "@/lib/hooks";
import type { Job } from "@/lib/types";
import { failureMessage, stageLabel } from "./workflow";

/** Real stages and counts only. There are no invented percentages or time estimates. */
export function JobStatus({ job }: { job: Job }) {
  const client = useQueryClient();
  const refresh = () => client.invalidateQueries({ queryKey: ["jobs", job.projectId] });
  const cancel = useMutation({ mutationFn: () => api<Job>(`/api/v1/jobs/${job.id}/cancel`, { method: "POST" }), onSuccess: refresh });
  const retry = useMutation({ mutationFn: () => api<Job>(`/api/v1/jobs/${job.id}/retry`, { method: "POST" }), onSuccess: refresh });
  const progress = job.progress;
  const error = cancel.error ?? retry.error;

  return (
    <div className="space-y-2" data-testid="job-status" data-state={job.state}>
      {job.state === "QUEUED" ? <p role="status" className="font-semibold">Waiting for a worker</p> : null}
      {job.state === "RUNNING" ? (
        <p role="status" className="font-semibold">
          {stageLabel(job.stage)}
          {progress?.current && progress.total ? (
            <span className="timecode font-normal text-ink-soft">
              {" "}
              {progress.current} of {progress.total} {progress.unit ?? ""}
            </span>
          ) : null}
        </p>
      ) : null}
      {job.state === "RETRY_WAIT" ? (
        <p role="status" className="font-semibold">
          Attempt {job.attemptCount} of {job.maxAttempts} failed. Trying again soon.
        </p>
      ) : null}
      {job.state === "CANCEL_REQUESTED" ? (
        <p role="status" className="font-semibold">Stopping. Work on the GPU may take a moment to end.</p>
      ) : null}
      {job.state === "CANCELLED" ? <p role="status" className="font-semibold">Cancelled</p> : null}
      {job.state === "FAILED" ? <ErrorNote>{failureMessage(job)}</ErrorNote> : null}

      <div className="flex flex-wrap gap-2">
        {isActive(job) && job.state !== "CANCEL_REQUESTED" ? (
          <Button onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            Cancel
          </Button>
        ) : null}
        {job.state === "FAILED" || job.state === "CANCELLED" ? (
          <Button onClick={() => retry.mutate()} disabled={retry.isPending}>
            Retry
          </Button>
        ) : null}
      </div>
      {error instanceof ApiError ? <ErrorNote>{error.message}</ErrorNote> : null}
      {job.attemptCount > 1 && isActive(job) ? (
        <Hint>
          Attempt {job.attemptCount} of {job.maxAttempts}
        </Hint>
      ) : null}
    </div>
  );
}
