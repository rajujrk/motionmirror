"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { api, ApiError } from "@/lib/api";

/** Deleting can't be undone, so the first press only says exactly what will go. */
export function ConfirmDelete({
  path,
  label,
  warning,
  confirmLabel,
  onDeleted,
}: {
  path: string;
  label: string;
  warning: string;
  confirmLabel: string;
  onDeleted: () => void;
}) {
  const [confirming, setConfirming] = useState(false);
  const remove = useMutation({ mutationFn: () => api<void>(path, { method: "DELETE" }), onSuccess: onDeleted });

  if (!confirming) {
    return (
      <Button variant="quiet" onClick={() => setConfirming(true)}>
        {label}
      </Button>
    );
  }
  return (
    <div className="space-y-2 text-sm">
      <p>{warning}</p>
      <div className="flex flex-wrap gap-2">
        <Button variant="primary" disabled={remove.isPending} onClick={() => remove.mutate()}>
          {confirmLabel}
        </Button>
        <Button onClick={() => setConfirming(false)}>Keep it</Button>
      </div>
      {remove.error ? (
        <ErrorNote>{remove.error instanceof ApiError ? remove.error.message : "That couldn't be deleted. Try again."}</ErrorNote>
      ) : null}
    </div>
  );
}
