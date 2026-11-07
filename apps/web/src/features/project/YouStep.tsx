"use client";

import { useMutation } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Button, ErrorNote, Hint, StepHeading } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useAssetUrl } from "@/lib/hooks";
import type { AppearanceProfile, Asset } from "@/lib/types";
import { ConfirmDelete } from "./ConfirmDelete";
import { FilePicker } from "./FilePicker";

const VIEW_LABELS: { value: string; label: string }[] = [
  { value: "FULL_BODY", label: "Full body" },
  { value: "FRONT", label: "Front" },
  { value: "BACK", label: "Back" },
  { value: "SIDE", label: "Side" },
  { value: "THREE_QUARTER", label: "Three-quarter" },
  { value: "FACE", label: "Face close-up" },
];

function ReferenceThumbnail({ asset }: { asset: Asset }) {
  const url = useAssetUrl(asset.status === "READY" ? asset.id : undefined);
  return (
    <div className="grid aspect-[3/4] w-full place-items-center overflow-hidden rounded-sm bg-floor text-sm text-sheen">
      {/* Signed storage URLs change, so next/image optimization doesn't apply here. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      {url.data ? <img src={url.data} alt={asset.displayName} className="size-full object-contain" /> : asset.status === "REJECTED" ? "Rejected" : "Checking"}
    </div>
  );
}

export function YouStep({
  projectId,
  references,
  profile,
  onChanged,
  onSaved,
}: {
  projectId: string;
  references: Asset[];
  profile?: AppearanceProfile;
  onChanged: () => void;
  onSaved: () => void;
}) {
  const ready = useMemo(() => references.filter((asset) => asset.status === "READY"), [references]);
  const [chosenPrimary, setPrimary] = useState<string | undefined>(() => profile?.references.find((reference) => reference.primary)?.assetId);
  // Until someone chooses, the first usable photo is the primary. A pick that was since rejected doesn't count.
  const primary = ready.some((asset) => asset.id === chosenPrimary) ? chosenPrimary : ready[0]?.id;
  const [labels, setLabels] = useState<Record<string, string>>(() =>
    Object.fromEntries((profile?.references ?? []).map((reference) => [reference.assetId, reference.viewLabel])),
  );

  const labelOf = (id: string) => labels[id] ?? "FULL_BODY";
  const primaryIsFace = primary !== undefined && labelOf(primary) === "FACE";

  const save = useMutation({
    mutationFn: () =>
      api<AppearanceProfile>(`/api/v1/projects/${projectId}/appearance-profiles`, {
        method: "POST",
        body: {
          name: "My references",
          references: ready.map((asset) => ({ assetId: asset.id, viewLabel: labelOf(asset.id), primary: asset.id === primary })),
        },
      }),
    onSuccess: onSaved,
  });

  return (
    <section>
      <StepHeading title="Add photos of you">
        Choose one clear full-body photo in the outfit you want. That photo is what the model uses. Any other photos are
        stored with your project, but this version doesn&apos;t use them.
      </StepHeading>

      <FilePicker
        projectId={projectId}
        role="TARGET_REFERENCE"
        accept="image/jpeg,image/png,image/webp"
        label="Choose photos"
        consentText="These photos are of me, or I have permission to use this person's likeness."
        multiple
        onUploaded={onChanged}
      />

      {references.length ? (
        <ul className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4">
          {references.map((asset) => (
            <li key={asset.id} className="space-y-2">
              <ReferenceThumbnail asset={asset} />
              {asset.status === "REJECTED" ? (
                <p className="text-sm text-tape-deep">{asset.rejectionDetail ?? "This photo can't be used."}</p>
              ) : null}
              {asset.status === "READY" ? (
                <>
                  <label className="flex items-center gap-2 text-sm font-semibold">
                    <input
                      type="radio"
                      name="primary-reference"
                      checked={primary === asset.id}
                      onChange={() => setPrimary(asset.id)}
                    />
                    Use this photo
                  </label>
                  <label className="block text-sm">
                    <span className="mb-1 block text-ink-soft">View</span>
                    <select
                      value={labelOf(asset.id)}
                      onChange={(event) => setLabels({ ...labels, [asset.id]: event.target.value })}
                      className="w-full rounded-sm border border-ink bg-sheen px-2 py-1"
                    >
                      {VIEW_LABELS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {asset.media?.warnings?.includes("LOW_RESOLUTION") ? (
                    <p className="text-sm text-ink-soft">This photo is small, so detail may be lost.</p>
                  ) : null}
                </>
              ) : null}
              <ConfirmDelete
                path={`/api/v1/assets/${asset.id}`}
                label="Remove photo"
                warning="This removes the photo and any videos made from it. It can't be undone."
                confirmLabel="Remove photo and videos"
                onDeleted={onChanged}
              />
            </li>
          ))}
        </ul>
      ) : null}

      {primaryIsFace ? (
        <div className="mt-4">
          <ErrorNote>A face close-up can&apos;t show an outfit. Choose a full-body photo to use.</ErrorNote>
        </div>
      ) : null}

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button variant="primary" disabled={!primary || primaryIsFace || save.isPending} onClick={() => save.mutate()}>
          Save photos
        </Button>
        {profile ? <Hint>Saved. Saving again replaces what the next job uses.</Hint> : null}
      </div>
      {save.error instanceof ApiError ? <div className="mt-3"><ErrorNote>{save.error.message}</ErrorNote></div> : null}
    </section>
  );
}
