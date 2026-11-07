"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { ErrorNote } from "@/components/ui";
import { AppHeader } from "@/features/project/AppHeader";
import { ClipStep } from "@/features/project/ClipStep";
import { ConfirmDelete } from "@/features/project/ConfirmDelete";
import { DancerStep } from "@/features/project/DancerStep";
import { GenerateStep } from "@/features/project/GenerateStep";
import { MaskStep } from "@/features/project/MaskStep";
import { ResultsStep } from "@/features/project/ResultsStep";
import { StepRail } from "@/features/project/StepRail";
import { useAuthGuard } from "@/features/project/useAuthGuard";
import { YouStep } from "@/features/project/YouStep";
import { deriveSteps, deriveWorkflow, firstOpenStep, type StepId } from "@/features/project/workflow";
import { useAssets, useCapabilities, useJobs, useProfiles, useProject, useSelections } from "@/lib/hooks";

export default function ProjectPage() {
  const { id } = useParams<{ id: string }>();
  const { ready } = useAuthGuard();
  const project = useProject(id);
  const assets = useAssets(id);
  const selections = useSelections(id);
  const profiles = useProfiles(id);
  const jobs = useJobs(id);
  const client = useQueryClient();
  const router = useRouter();
  const [chosen, setChosen] = useState<StepId>();
  const capabilities = useCapabilities();
  const retentionDays = capabilities.data?.retentionDays ?? 0;

  const projectDeleted = async () => {
    await client.invalidateQueries({ queryKey: ["projects"] });
    router.push("/projects");
  };

  const state = useMemo(
    () => deriveWorkflow(assets.data ?? [], selections.data ?? [], profiles.data ?? [], jobs.data ?? []),
    [assets.data, selections.data, profiles.data, jobs.data],
  );
  const steps = useMemo(() => deriveSteps(state), [state]);
  const loaded = assets.isSuccess && selections.isSuccess && profiles.isSuccess && jobs.isSuccess;
  const current = chosen ?? (loaded ? firstOpenStep(steps) : undefined);

  const refresh = () =>
    Promise.all(
      [["assets", id], ["selections", id], ["profiles", id], ["jobs", id]].map((queryKey) => client.invalidateQueries({ queryKey })),
    );

  /** After saving a step, go back to following the first step still open once the new data has arrived. */
  const saved = async () => {
    await refresh();
    setChosen(undefined);
  };

  useEffect(() => {
    // An upload's check finishing or a job starting elsewhere shouldn't leave a stale list behind.
    if (jobs.isSuccess) void client.invalidateQueries({ queryKey: ["assets", id] });
  }, [jobs.dataUpdatedAt, jobs.isSuccess, client, id]);

  if (!ready) return null;

  return (
    <>
      <AppHeader />
      <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 sm:px-6 md:grid-cols-[11rem_minmax(0,1fr)] md:gap-10">
        <aside className="min-w-0">
          <h1 className="mark-heading mb-4 text-3xl break-words" data-testid="project-name">
            {project.data?.name}
          </h1>
          {project.error ? <ErrorNote>This project doesn&apos;t exist, or it isn&apos;t yours.</ErrorNote> : null}
          {current ? <StepRail steps={steps} current={current} onSelect={setChosen} /> : null}
          <div className="mt-8 border-t border-rule pt-3 text-sm">
            <ConfirmDelete
              path={`/api/v1/projects/${id}`}
              label="Delete project"
              warning="This removes the clip, photos, masks and every result. It can't be undone."
              confirmLabel="Delete everything"
              onDeleted={() => void projectDeleted()}
            />
            <p className="mt-2 text-ink-soft">
              {retentionDays > 0
                ? `Projects are deleted ${retentionDays} days after you last use them.`
                : "Projects stay until you delete them."}
            </p>
          </div>
        </aside>
        <main className="min-w-0">
          {current === "clip" ? <ClipStep projectId={id} source={state.source} onChanged={() => void refresh()} /> : null}
          {current === "dancer" && state.source ? (
            <DancerStep key={state.source.id} projectId={id} source={state.source} selection={state.selection} onSaved={() => void saved()} />
          ) : null}
          {current === "you" ? <YouStep projectId={id} references={state.references} profile={state.profile} onChanged={() => void refresh()} onSaved={() => void saved()} /> : null}
          {current === "mask" && state.selection ? (
            <MaskStep
              projectId={id}
              selection={state.selection}
              job={state.maskJob}
              onChanged={() => {
                // Stay here when tracking finishes, so the mask can be checked before moving on.
                setChosen("mask");
                void refresh();
              }}
              onCorrect={() => setChosen("dancer")}
              onContinue={() => setChosen(undefined)}
            />
          ) : null}
          {current === "generate" && state.selection && state.profile && state.maskJob ? (
            <GenerateStep
              projectId={id}
              selection={state.selection}
              profile={state.profile}
              maskJob={state.maskJob}
              onSubmitted={() => {
                void refresh();
                setChosen("results");
              }}
            />
          ) : null}
          {current === "results" ? <ResultsStep jobs={state.generations} onChanged={() => void refresh()} /> : null}
        </main>
      </div>
    </>
  );
}
