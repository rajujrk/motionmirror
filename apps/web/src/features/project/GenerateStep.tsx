"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { Button, ErrorNote, Hint, MockTape, RealBadge, StepHeading } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useCapabilities, usePresets } from "@/lib/hooks";
import type { AppearanceProfile, Job, Selection } from "@/lib/types";
import {
  generationFormSchema,
  toOverrides,
  type GenerationFormInput,
  type GenerationFormValues,
} from "./settingsSchema";

export function GenerateStep({
  projectId,
  selection,
  profile,
  maskJob,
  onSubmitted,
}: {
  projectId: string;
  selection: Selection;
  profile: AppearanceProfile;
  maskJob: Job;
  onSubmitted: () => void;
}) {
  const presets = usePresets();
  const capabilities = useCapabilities();
  const [attemptKey] = useState(() => crypto.randomUUID());
  const form = useForm<GenerationFormInput, unknown, GenerationFormValues>({
    resolver: zodResolver(generationFormSchema),
    defaultValues: { presetId: "preview", positivePrompt: "", negativePrompt: "", seed: 42, guidanceScale: 1, consent: false },
  });
  const errors = form.formState.errors;

  const submit = useMutation({
    mutationFn: ({ kind, values }: { kind: "PREVIEW" | "FULL"; values: GenerationFormValues }) =>
      api<Job>(`/api/v1/projects/${projectId}/generation-jobs`, {
        method: "POST",
        idempotencyKey: `${attemptKey}-${kind}-${form.formState.submitCount}`,
        body: {
          kind,
          selectionId: selection.id,
          appearanceProfileId: profile.id,
          preprocessJobId: maskJob.id,
          presetId: kind === "PREVIEW" ? "preview" : values.presetId,
          overrides: toOverrides(values),
          consentConfirmed: values.consent,
        },
      }),
    onSuccess: onSubmitted,
  });

  const primary = profile.references.find((reference) => reference.primary);
  const others = profile.references.length - (primary ? 1 : 0);
  const label = capabilities.data?.generationLabel;

  return (
    <section>
      <StepHeading title="Generate">
        Start with a short preview to check likeness, outfit and motion, then generate the full clip.
      </StepHeading>

      {label === "mock" ? (
        <div className="mb-5">
          <MockTape detail="This setup has no GPU, so no one is actually replaced." />
        </div>
      ) : label ? (
        <div className="mb-5"><RealBadge experimental={label === "experimental"} /></div>
      ) : null}

      <form className="max-w-2xl space-y-4" noValidate onSubmit={(event) => event.preventDefault()}>
        <label className="block">
          <span className="mb-1 block font-semibold">Preset for the full clip</span>
          <select className="w-full rounded-sm border border-ink bg-sheen px-3 py-2" {...form.register("presetId")}>
            {(presets.data ?? []).map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.displayName}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block font-semibold">Describe the result (optional)</span>
          <textarea rows={2} className="w-full rounded-sm border border-ink bg-sheen px-3 py-2" {...form.register("positivePrompt")} />
          {errors.positivePrompt ? <p className="mt-1 text-sm text-tape-deep">{errors.positivePrompt.message}</p> : null}
        </label>

        <details className="rounded-sm border border-rule bg-sheen px-3 py-2">
          <summary className="cursor-pointer font-semibold">Advanced settings</summary>
          <div className="mt-3 space-y-4">
            <label className="block">
              <span className="mb-1 block">Things to avoid</span>
              <textarea rows={2} className="w-full rounded-sm border border-ink bg-mirror px-3 py-2" {...form.register("negativePrompt")} />
            </label>
            <div className="flex flex-wrap gap-4">
              <label className="block">
                <span className="mb-1 block">Guidance</span>
                <input type="number" step="0.5" className="timecode w-28 rounded-sm border border-ink bg-mirror px-2 py-1" {...form.register("guidanceScale")} />
              </label>
              <label className="block">
                <span className="mb-1 block">Seed</span>
                <input type="number" className="timecode w-40 rounded-sm border border-ink bg-mirror px-2 py-1" {...form.register("seed")} />
              </label>
            </div>
            <Hint>Guidance of 1 turns off &ldquo;things to avoid&rdquo; and is faster. The same seed and settings repeat a result.</Hint>
          </div>
        </details>
        {errors.guidanceScale ? <ErrorNote>{errors.guidanceScale.message}</ErrorNote> : null}
        {errors.seed ? <ErrorNote>{errors.seed.message}</ErrorNote> : null}

        <p className="text-sm text-ink-soft">
          Uses your primary photo{others > 0 ? `. ${others} other ${others === 1 ? "photo is" : "photos are"} stored but won't be used` : ""}.
        </p>

        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" className="mt-1" {...form.register("consent")} />
          <span>I have permission to use this person&apos;s likeness, and I understand the result is AI-edited.</span>
        </label>
        {errors.consent ? <p className="text-sm text-tape-deep">{errors.consent.message}</p> : null}

        <div className="flex flex-wrap gap-3">
          <Button
            variant="primary"
            disabled={submit.isPending}
            onClick={form.handleSubmit((values) => submit.mutate({ kind: "PREVIEW", values }))}
          >
            Generate preview
          </Button>
          <Button
            disabled={submit.isPending}
            onClick={form.handleSubmit((values) => submit.mutate({ kind: "FULL", values }))}
          >
            Generate full clip
          </Button>
        </div>
        {submit.error instanceof ApiError ? <ErrorNote>{submit.error.message}</ErrorNote> : null}
      </form>
    </section>
  );
}
