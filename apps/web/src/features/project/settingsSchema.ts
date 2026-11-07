import { z } from "zod";

/** Convenience checks only. The API and the worker repeat every rule against the shared contract. */
export const generationFormSchema = z
  .object({
    presetId: z.string().min(1, "Choose a preset"),
    positivePrompt: z.string().max(2000, "Keep the prompt under 2000 characters"),
    negativePrompt: z.string().max(2000, "Keep the prompt under 2000 characters"),
    seed: z.coerce.number().int("Use a whole number").min(0, "Use 0 or higher").max(4294967295, "That seed is too large"),
    guidanceScale: z.coerce.number().min(1, "Use 1 or higher").max(20, "Use 20 or lower"),
    consent: z.boolean().refine((value) => value, "Confirm you have permission to use this person's likeness"),
  })
  .superRefine((values, context) => {
    if (values.negativePrompt.trim() && values.guidanceScale <= 1) {
      context.addIssue({
        code: "custom",
        path: ["guidanceScale"],
        message: "A negative prompt only has an effect with guidance above 1. Raise guidance or remove it.",
      });
    }
  });

export type GenerationFormInput = z.input<typeof generationFormSchema>;
export type GenerationFormValues = z.output<typeof generationFormSchema>;

export function toOverrides(values: GenerationFormValues) {
  return {
    seed: values.seed,
    guidance_scale: values.guidanceScale,
    ...(values.positivePrompt.trim() ? { positive_prompt: values.positivePrompt.trim() } : {}),
    ...(values.negativePrompt.trim() ? { negative_prompt: values.negativePrompt.trim() } : {}),
  };
}
