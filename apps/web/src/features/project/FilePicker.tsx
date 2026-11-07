"use client";

import { useRef, useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Asset } from "@/lib/types";
import { uploadFile, type UploadForm } from "@/lib/upload";

type Props = {
  projectId: string;
  role: "DRIVING_VIDEO" | "TARGET_REFERENCE";
  accept: string;
  label: string;
  consentText: string;
  multiple?: boolean;
  onUploaded: () => void;
};

/** Upload intent, direct upload to storage, then completion. The API checks everything again after this. */
export function FilePicker({ projectId, role, accept, label, consentText, multiple, onUploaded }: Props) {
  const [consent, setConsent] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string>();
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);

  async function upload(files: FileList | null) {
    if (!files?.length) return;
    setError(undefined);
    abort.current = new AbortController();
    try {
      for (const file of Array.from(files)) {
        setProgress(0);
        const intent = await api<UploadForm & { assetId: string }>(`/api/v1/projects/${projectId}/assets/upload-intents`, {
          method: "POST",
          body: { role, filename: file.name, sizeBytes: file.size, contentType: file.type, consentConfirmed: consent },
        });
        await uploadFile(intent, file, setProgress, abort.current.signal);
        await api<Asset>(`/api/v1/assets/${intent.assetId}/complete`, { method: "POST" });
      }
      onUploaded();
    } catch (failure) {
      if (!(failure instanceof DOMException && failure.name === "AbortError")) {
        setError(failure instanceof ApiError || failure instanceof Error ? failure.message : "The upload failed.");
      }
    } finally {
      setProgress(null);
      if (input.current) input.current.value = "";
    }
  }

  const uploading = progress !== null;

  return (
    <div className="space-y-3">
      <label className="flex max-w-[65ch] items-start gap-2 text-sm">
        <input type="checkbox" className="mt-1" checked={consent} onChange={(event) => setConsent(event.target.checked)} />
        <span>{consentText}</span>
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <label className="block">
          <span className="mb-1 block font-semibold">{label}</span>
          <input
            ref={input}
            type="file"
            accept={accept}
            multiple={multiple}
            disabled={!consent || uploading}
            onChange={(event) => void upload(event.target.files)}
            className="block text-sm file:mr-3 file:rounded-sm file:border file:border-ink file:bg-sheen file:px-3 file:py-2 file:font-semibold disabled:opacity-50"
          />
        </label>
        {uploading ? (
          <>
            <progress value={progress} max={1} className="w-40" aria-label="Upload progress" />
            <span className="timecode text-sm">{Math.round((progress ?? 0) * 100)}%</span>
            <Button variant="quiet" onClick={() => abort.current?.abort()}>
              Cancel upload
            </Button>
          </>
        ) : null}
      </div>
      {error ? <ErrorNote>{error}</ErrorNote> : null}
    </div>
  );
}
