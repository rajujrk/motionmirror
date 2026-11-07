import { describe, expect, it } from "vitest";
import {
  containedRect,
  displayToSource,
  formatTimecode,
  frameIndexAt,
  modelMapping,
  modelToSource,
  parseFrameRate,
  secondsAtFrame,
  sourceToDisplay,
  sourceToModel,
} from "./coords";

const close = (actual: number, expected: number) => expect(actual).toBeCloseTo(expected, 6);

describe("letterboxed player", () => {
  const player = { width: 800, height: 450 };
  const portrait = { width: 1080, height: 1920 };

  it("centres a portrait video between side bars", () => {
    const rect = containedRect(player, portrait);
    close(rect.height, 450);
    close(rect.width, 253.125);
    close(rect.x, (800 - 253.125) / 2);
    close(rect.y, 0);
  });

  it("maps the centre of the player to the centre of the frame", () => {
    const point = displayToSource({ x: 400, y: 225 }, player, portrait)!;
    close(point.x, 0.5);
    close(point.y, 0.5);
  });

  it("ignores a click on the letterbox bars", () => {
    expect(displayToSource({ x: 10, y: 225 }, player, portrait)).toBeNull();
    expect(displayToSource({ x: 790, y: 225 }, player, portrait)).toBeNull();
  });

  it("round-trips between player and frame coordinates", () => {
    const original = { x: 0.2, y: 0.85 };
    const shown = sourceToDisplay(original, player, portrait);
    const back = displayToSource(shown, player, portrait)!;
    close(back.x, original.x);
    close(back.y, original.y);
  });

  it("handles a wide video with bars above and below", () => {
    const wide = { width: 1920, height: 800 };
    const tall = { width: 400, height: 600 };
    expect(displayToSource({ x: 200, y: 10 }, tall, wide)).toBeNull();
    const point = displayToSource({ x: 200, y: 300 }, tall, wide)!;
    close(point.x, 0.5);
    close(point.y, 0.5);
  });
});

describe("rotated phone video", () => {
  it("uses display dimensions, so a click lands where the user sees it", () => {
    // A phone clip stored as 1920x1080 with 90 degree rotation reports videoWidth 1080 and videoHeight 1920.
    const display = { width: 1080, height: 1920 };
    const point = displayToSource({ x: 100, y: 300 }, { width: 200, height: 400 }, display)!;
    close(point.x, (100 - (200 - 225) / 2) / 225);
  });
});

describe("model space", () => {
  const source = { width: 1080, height: 1920 };

  it("pads when the aspect ratios differ", () => {
    const mapping = modelMapping(source, { width: 720, height: 1040 });
    close(mapping.scale, 1040 / 1920);
    close(mapping.padY, 0);
    close(mapping.padX, (720 - 1080 * (1040 / 1920)) / 2);
  });

  it("round-trips through model coordinates", () => {
    const mapping = modelMapping(source, { width: 480, height: 832 });
    const original = { x: 0.31, y: 0.77 };
    const back = modelToSource(sourceToModel(original, source, mapping), source, mapping);
    close(back.x, original.x);
    close(back.y, original.y);
  });
});

describe("time", () => {
  it("keeps NTSC rates exact", () => {
    const rate = parseFrameRate("30000/1001");
    expect(frameIndexAt(secondsAtFrame(901, rate), rate)).toBe(901);
  });

  it("falls back when a rate is missing", () => {
    expect(parseFrameRate(undefined)).toEqual({ num: 30, den: 1 });
    expect(parseFrameRate("0/0")).toEqual({ num: 30, den: 1 });
  });

  it("formats timecode with two decimals", () => {
    expect(formatTimecode(0)).toBe("0:00.00");
    expect(formatTimecode(75.5)).toBe("1:15.50");
  });
});
