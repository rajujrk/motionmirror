"use client";

import { useRef } from "react";
import { Button, Hint, MockTape, RealBadge, Stage, StepHeading } from "@/components/ui";
import { useAssetUrl, useOutputUrl } from "@/lib/hooks";
import type { Job } from "@/lib/types";
import { JobStatus } from "./JobStatus";

/** The output drives; the source follows it, so they stay in step while comparing. */
function Comparison({ job }: { job: Job }) {
  const source = useAssetUrl(job.inputs.source_asset_id);
  const output = useOutputUrl(job.id, "result.mp4");
  const follower = useRef<HTMLVideoElement>(null);
  const trimStart = Number((job.inputs as { trim_start_seconds?: number }).trim_start_seconds ?? 0);

  const follow = (time: number) => {
    if (follower.current) follower.current.currentTime = time + trimStart;
  };

  return (
    <div className="grid gap-3 md:grid-cols-2">
      <figure>
        <figcaption className="mb-1 text-sm font-semibold">Original</figcaption>
        <Stage>
          {source.data ? (
            <video ref={follower} src={source.data} muted playsInline className="max-h-[52vh] w-full object-contain" aria-label="Original clip" />
          ) : null}
        </Stage>
      </figure>
      <figure>
        <figcaption className="mb-1 text-sm font-semibold">Result</figcaption>
        <Stage>
          {output.data ? (
            <video
              src={output.data}
              controls
              playsInline
              className="max-h-[52vh] w-full object-contain"
              aria-label="Generated result"
              onPlay={(event) => {
                follow(event.currentTarget.currentTime);
                void follower.current?.play();
              }}
              onPause={() => follower.current?.pause()}
              onSeeked={(event) => follow(event.currentTarget.currentTime)}
            />
          ) : null}
        </Stage>
      </figure>
    </div>
  );
}

function SettingsDetails({ job }: { job: Job }) {
  const manifest = useOutputUrl(job.outputs.some((output) => output.name === "manifest.json") ? job.id : undefined, "manifest.json");
  const stored = job.inputs.supplementary_reference_asset_ids ?? [];
  return (
    <details className="rounded-sm border border-rule bg-sheen px-3 py-2 text-sm">
      <summary className="cursor-pointer font-semibold">What this run used</summary>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
        <dt className="text-ink-soft">Preset</dt>
        <dd>{job.configRevision}</dd>
        <dt className="text-ink-soft">Photo used by the model</dt>
        <dd>{job.outputLabel === "mock" ? "None. No model ran" : "Your primary photo"}</dd>
        <dt className="text-ink-soft">Photos stored but not used</dt>
        <dd className="timecode">{stored.length}</dd>
      </dl>
      <pre className="mt-3 overflow-x-auto bg-mirror p-2 text-xs">{JSON.stringify(job.effectiveSettings, null, 2)}</pre>
      {manifest.data ? (
        <a href={manifest.data} className="mt-2 inline-block underline underline-offset-3">
          Download the settings manifest
        </a>
      ) : null}
    </details>
  );
}

function JobResult({ job }: { job: Job }) {
  const output = useOutputUrl(job.state === "SUCCEEDED" ? job.id : undefined, "result.mp4");
  const when = new Date(job.createdAt).toLocaleString();
  return (
    <li className="space-y-3 py-5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="mark-heading text-3xl">{job.kind === "PREVIEW" ? "Preview" : "Full clip"}</h3>
        <span className="timecode text-sm text-ink-soft">{when}</span>
      </div>
      <JobStatus job={job} />
      {job.state === "SUCCEEDED" ? (
        <>
          {job.outputLabel === "mock" ? <MockTape /> : <RealBadge />}
          <Comparison job={job} />
          <div className="flex flex-wrap items-center gap-3">
            {output.data ? (
              <a href={output.data} className="rounded-sm bg-tape px-4 py-2 font-semibold text-white hover:bg-tape-deep">
                Download MP4
              </a>
            ) : (
              <Button disabled>Download MP4</Button>
            )}
            <Hint>{job.outputLabel === "mock" ? "This file carries the mock label in every frame." : "This video is AI-edited."}</Hint>
          </div>
          <SettingsDetails job={job} />
        </>
      ) : null}
    </li>
  );
}

export function ResultsStep({ jobs }: { jobs: Job[] }) {
  return (
    <section>
      <StepHeading title="Results">Newest first. Check hands, feet, spins and the edges around the dancer.</StepHeading>
      <ul className="divide-y divide-rule">
        {jobs.map((job) => (
          <JobResult key={job.id} job={job} />
        ))}
      </ul>
    </section>
  );
}
