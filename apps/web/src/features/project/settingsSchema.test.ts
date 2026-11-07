import { describe, expect, it } from "vitest";
import { generationFormSchema, toOverrides } from "./settingsSchema";

const valid = { presetId: "preview", positivePrompt: "", negativePrompt: "", seed: 42, guidanceScale: 1, consent: true };

describe("generation form", () => {
  it("accepts the defaults", () => {
    expect(generationFormSchema.safeParse(valid).success).toBe(true);
  });

  it("refuses a negative prompt that guidance would ignore", () => {
    const result = generationFormSchema.safeParse({ ...valid, negativePrompt: "blurry" });
    expect(result.success).toBe(false);
    expect(result.error?.issues[0]?.message).toContain("guidance above 1");
  });

  it("accepts a negative prompt with guidance enabled", () => {
    expect(generationFormSchema.safeParse({ ...valid, negativePrompt: "blurry", guidanceScale: 5 }).success).toBe(true);
  });

  it("needs consent", () => {
    expect(generationFormSchema.safeParse({ ...valid, consent: false }).success).toBe(false);
  });

  it("refuses a seed the contract would reject", () => {
    expect(generationFormSchema.safeParse({ ...valid, seed: 4294967296 }).success).toBe(false);
  });

  it("leaves empty prompts out of the request", () => {
    const parsed = generationFormSchema.parse(valid);
    expect(toOverrides(parsed)).toEqual({ seed: 42, guidance_scale: 1 });
  });
});
