"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote, Hint, StepHeading } from "@/components/ui";
import { SelectionCanvas } from "@/features/selection/SelectionCanvas";
import { api, ApiError } from "@/lib/api";
import { useAssetUrl } from "@/lib/hooks";
import type { Annotations, Asset, Selection } from "@/lib/types";

const MAX_SECONDS = 30;

/** Every save is a new revision, so correcting a mask never rewrites what an earlier result used. */
export function DancerStep({
  projectId,
  source,
  selection,
  onSaved,
}: {
  projectId: string;
  source: Asset;
  selection?: Selection;
  onSaved: () => void;
}) {
  const url = useAssetUrl(source.id);
  const [annotations, setAnnotations] = useState<Annotations>(selection?.annotations ?? {});
  const [position, setPosition] = useState({ frame: selection?.frameIndex ?? 0, seconds: 0 });
  const [trimStart, setTrimStart] = useState<string>(selection?.trimStartSeconds?.toString() ?? "");
  const [trimEnd, setTrimEnd] = useState<string>(selection?.trimEndSeconds?.toString() ?? "");
  const duration = source.media?.duration_seconds ?? 0;

  const start = trimStart === "" ? null : Number(trimStart);
  const end = trimEnd === "" ? null : Number(trimEnd);
  const selected = (end ?? duration) - (start ?? 0);
  const trimProblem =
    selected <= 0
      ? "The trim end must be after its start."
      : selected > MAX_SECONDS
        ? `Choose ${MAX_SECONDS} seconds or less. This selection is ${selected.toFixed(1)} seconds.`
        : undefined;

  const hasMark = (annotations.points ?? []).some((point) => point.positive) || Boolean(annotations.box);

  const save = useMutation({
    mutationFn: () =>
      api<Selection>(`/api/v1/projects/${projectId}/subject-selections`, {
        method: "POST",
        body: {
          sourceAssetId: source.id,
          frameIndex: position.frame,
          timestampNum: Math.round(position.seconds * 1_000_000),
          timestampDen: 1_000_000,
          coordinateMapping: {
            space: "display-normalized",
            displayWidth: source.media?.display_width,
            displayHeight: source.media?.display_height,
          },
          annotations,
          trimStartSeconds: start,
          trimEndSeconds: end,
        },
      }),
    onSuccess: onSaved,
  });

  return (
    <section>
      <StepHeading title="Mark the dancer">
        Pick the one person to replace. Other people can stay in the shot. Add more marks on any frame where tracking
        goes wrong.
      </StepHeading>

      {url.data ? (
        <SelectionCanvas
          url={url.data}
          frameRate={source.media?.frame_rate}
          annotations={annotations}
          onChange={setAnnotations}
          onFrameChange={(frame, seconds) => setPosition({ frame, seconds })}
        />
      ) : null}

      <fieldset className="mt-5 flex flex-wrap items-end gap-4">
        <legend className="mb-1 font-semibold">Trim (optional)</legend>
        <label className="text-sm">
          <span className="mb-1 block">Start, in seconds</span>
          <input
            type="number"
            min={0}
            step="0.1"
            value={trimStart}
            onChange={(event) => setTrimStart(event.target.value)}
            className="timecode w-28 rounded-sm border border-ink bg-sheen px-2 py-1"
          />
        </label>
        <label className="text-sm">
          <span className="mb-1 block">End, in seconds</span>
          <input
            type="number"
            min={0}
            step="0.1"
            value={trimEnd}
            onChange={(event) => setTrimEnd(event.target.value)}
            className="timecode w-28 rounded-sm border border-ink bg-sheen px-2 py-1"
          />
        </label>
        <Hint>Up to {MAX_SECONDS} seconds.</Hint>
      </fieldset>
      {trimProblem ? <div className="mt-3"><ErrorNote>{trimProblem}</ErrorNote></div> : null}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button variant="primary" disabled={!hasMark || Boolean(trimProblem) || save.isPending} onClick={() => save.mutate()}>
          Save selection
        </Button>
        {selection ? <span className="text-sm text-ink-soft">Saved selection, revision {selection.revision}</span> : null}
        {!hasMark ? <Hint>Place at least one mark or a box on the dancer.</Hint> : null}
      </div>
      {save.error instanceof ApiError ? <div className="mt-3"><ErrorNote>{save.error.message}</ErrorNote></div> : null}
    </section>
  );
}
