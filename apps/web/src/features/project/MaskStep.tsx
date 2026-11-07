"use client";

import { useMutation } from "@tanstack/react-query";
import { useId } from "react";
import { Button, ErrorNote, Hint, MockTape, RealBadge, Stage, StepHeading } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useOutputUrl } from "@/lib/hooks";
import type { Job, Selection } from "@/lib/types";
import { JobStatus } from "./JobStatus";

export function MaskStep({
  projectId,
  selection,
  job,
  onChanged,
  onCorrect,
  onContinue,
}: {
  projectId: string;
  selection: Selection;
  job?: Job;
  onChanged: () => void;
  onCorrect: () => void;
  onContinue: () => void;
}) {
  const requestKey = useId().replaceAll(":", "");
  const preview = useOutputUrl(job?.state === "SUCCEEDED" ? job.id : undefined, "preview.mp4");
  const start = useMutation({
    mutationFn: () =>
      api<Job>(`/api/v1/projects/${projectId}/preprocessing-jobs`, {
        method: "POST",
        body: { selectionId: selection.id },
        idempotencyKey: `mask-${selection.id}-${requestKey}`,
      }),
    onSuccess: onChanged,
  });

  return (
    <section>
      <StepHeading title="Check the mask">
        Before spending time on generation, check that the highlight stays on your dancer the whole way through.
      </StepHeading>

      {!job ? (
        <Button variant="primary" disabled={start.isPending} onClick={() => start.mutate()}>
          Track the dancer
        </Button>
      ) : null}
      {start.error instanceof ApiError ? <ErrorNote>{start.error.message}</ErrorNote> : null}

      {job ? <JobStatus job={job} /> : null}

      {job?.state === "SUCCEEDED" ? (
        <div className="mt-4 space-y-3">
          {job.outputLabel === "mock" ? (
            <MockTape detail="This box is a stand-in. No tracking ran." />
          ) : (
            <RealBadge />
          )}
          <Stage>
            {preview.data ? (
              <video src={preview.data} controls playsInline className="mx-auto max-h-[56vh] w-full object-contain" aria-label="Mask preview" />
            ) : null}
          </Stage>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" onClick={onContinue}>
              Continue
            </Button>
            <Button onClick={onCorrect}>Correct the marks</Button>
            <Hint>Corrections make a new selection, then you track the dancer again.</Hint>
          </div>
        </div>
      ) : null}
    </section>
  );
}
