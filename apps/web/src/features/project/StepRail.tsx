"use client";

import { TapeX } from "@/components/ui";
import type { StepId, StepInfo } from "./workflow";

/** Six floor marks in order. The current one gets a pink spike mark. */
export function StepRail({ steps, current, onSelect }: { steps: StepInfo[]; current: StepId; onSelect: (id: StepId) => void }) {
  return (
    <nav aria-label="Steps" className="floor-strip">
      <ol className="flex gap-1 overflow-x-auto md:flex-col md:gap-3 md:overflow-visible">
        {steps.map((step, index) => {
          const active = step.id === current;
          return (
            <li key={step.id} className="shrink-0">
              <button
                type="button"
                disabled={!step.available}
                aria-current={active ? "step" : undefined}
                onClick={() => onSelect(step.id)}
                className="group flex items-center gap-2 pr-2 text-left disabled:cursor-not-allowed disabled:opacity-45"
              >
                <span className="relative grid size-10 shrink-0 place-items-center bg-mirror">
                  {active ? (
                    <TapeX size={40} className="absolute opacity-90" />
                  ) : (
                    <span className={`absolute size-7 rounded-sm ${step.done ? "bg-real" : "border-2 border-rule bg-sheen"}`} />
                  )}
                  <span className={`mark-heading relative text-lg ${step.done && !active ? "text-white" : "text-ink"}`}>
                    {index + 1}
                  </span>
                </span>
                <span className={`font-semibold group-enabled:group-hover:text-tape-deep ${active ? "text-tape-deep" : ""}`}>
                  {step.title}
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
