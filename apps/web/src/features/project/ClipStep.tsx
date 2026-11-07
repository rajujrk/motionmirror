"use client";

import { ErrorNote, Hint, Stage, StepHeading } from "@/components/ui";
import { formatTimecode } from "@/features/selection/coords";
import { useAssetUrl } from "@/lib/hooks";
import type { Asset } from "@/lib/types";
import { ConfirmDelete } from "./ConfirmDelete";
import { FilePicker } from "./FilePicker";

export function ClipStep({ projectId, source, onChanged }: { projectId: string; source?: Asset; onChanged: () => void }) {
  const url = useAssetUrl(source?.status === "READY" ? source.id : undefined);
  const media = source?.media;

  return (
    <section>
      <StepHeading title="Upload a dance clip">
        Up to 30 seconds, one continuous shot, MP4 or MOV. It works best when the dancer&apos;s whole body stays in frame.
      </StepHeading>

      {source?.status === "QUARANTINED" || source?.status === "PENDING_UPLOAD" ? (
        <p role="status" className="mb-4 font-semibold">
          Checking {source.displayName}. This stays private and can&apos;t be used until the check passes.
        </p>
      ) : null}
      {source?.status === "REJECTED" ? (
        <div className="mb-4">
          <ErrorNote>
            {source.displayName} can&apos;t be used. {source.rejectionDetail ?? "Upload a different file."}
          </ErrorNote>
        </div>
      ) : null}

      {source?.status === "READY" ? (
        <div className="mb-6 space-y-3">
          <Stage>
            {url.data ? (
              <video src={url.data} controls playsInline className="mx-auto max-h-[60vh] w-full object-contain" aria-label="Source clip" />
            ) : null}
          </Stage>
          <dl className="timecode flex flex-wrap gap-x-6 gap-y-1 text-sm">
            <div className="flex gap-1.5">
              <dt className="text-ink-soft">Length</dt>
              <dd>{formatTimecode(media?.duration_seconds ?? 0)}</dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-ink-soft">Size</dt>
              <dd>
                {media?.display_width}×{media?.display_height}
              </dd>
            </div>
            <div className="flex gap-1.5">
              <dt className="text-ink-soft">Sound</dt>
              <dd>{media?.audio_streams ? "Yes" : "None"}</dd>
            </div>
          </dl>
          {media?.needs_trim ? <ErrorNote>This clip is longer than 30 seconds. Choose a trim in the next step.</ErrorNote> : null}
          {media?.cuts?.length ? (
            <ErrorNote>
              This clip has {media.cuts.length === 1 ? "a cut" : `${media.cuts.length} cuts`} at{" "}
              {media.cuts.map((cut) => formatTimecode(cut)).join(", ")}. Tracking can&apos;t cross a cut, so trim to one
              continuous shot in the next step.
            </ErrorNote>
          ) : null}
          {media?.is_vfr ? <Hint>This clip has a variable frame rate. Its timing is kept from the original timestamps.</Hint> : null}
          <ConfirmDelete
            path={`/api/v1/assets/${source.id}`}
            label="Remove clip"
            warning="This removes the clip, its masks, your dancer selection and every video made from it. It can't be undone."
            confirmLabel="Remove clip and results"
            onDeleted={onChanged}
          />
        </div>
      ) : null}

      <FilePicker
        projectId={projectId}
        role="DRIVING_VIDEO"
        accept="video/mp4,video/quicktime"
        label={source ? "Replace the clip" : "Choose a clip"}
        consentText="I have permission to use this video and to edit the people in it."
        onUploaded={onChanged}
      />
    </section>
  );
}
