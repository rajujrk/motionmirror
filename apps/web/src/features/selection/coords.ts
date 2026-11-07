/**
 * Coordinates for the selection canvas. Annotations are stored normalized to the displayed video frame (0 to 1),
 * so they don't depend on the player's size. Browsers already apply rotation metadata, so `videoWidth` and
 * `videoHeight` are display dimensions.
 */

export type Size = { width: number; height: number };
export type Point = { x: number; y: number };
export type Rect = { x: number; y: number; width: number; height: number };

/** Where the video actually sits inside a player using `object-fit: contain`. */
export function containedRect(element: Size, video: Size): Rect {
  const scale = Math.min(element.width / video.width, element.height / video.height);
  const width = video.width * scale;
  const height = video.height * scale;
  return { x: (element.width - width) / 2, y: (element.height - height) / 2, width, height };
}

/** Player pixels to normalized frame coordinates. Returns null for clicks on the letterbox bars. */
export function displayToSource(point: Point, element: Size, video: Size): Point | null {
  const rect = containedRect(element, video);
  const x = (point.x - rect.x) / rect.width;
  const y = (point.y - rect.y) / rect.height;
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return { x, y };
}

/** Normalized frame coordinates back to player pixels, for drawing marks. */
export function sourceToDisplay(point: Point, element: Size, video: Size): Point {
  const rect = containedRect(element, video);
  return { x: rect.x + point.x * rect.width, y: rect.y + point.y * rect.height };
}

export type ModelMapping = { modelWidth: number; modelHeight: number; scale: number; padX: number; padY: number };

/** How a source frame fits a model canvas when its aspect ratio is kept and the rest is padded. */
export function modelMapping(source: Size, model: { width: number; height: number }): ModelMapping {
  const scale = Math.min(model.width / source.width, model.height / source.height);
  return {
    modelWidth: model.width,
    modelHeight: model.height,
    scale,
    padX: (model.width - source.width * scale) / 2,
    padY: (model.height - source.height * scale) / 2,
  };
}

export function sourceToModel(point: Point, source: Size, mapping: ModelMapping): Point {
  return {
    x: point.x * source.width * mapping.scale + mapping.padX,
    y: point.y * source.height * mapping.scale + mapping.padY,
  };
}

export function modelToSource(point: Point, source: Size, mapping: ModelMapping): Point {
  return {
    x: (point.x - mapping.padX) / (source.width * mapping.scale),
    y: (point.y - mapping.padY) / (source.height * mapping.scale),
  };
}

/** A rational frame rate like "30000/1001". Falls back to 30 if the probe had nothing usable. */
export function parseFrameRate(rate: string | undefined | null): { num: number; den: number } {
  const [num, den] = (rate ?? "").split("/").map(Number);
  return num && den ? { num, den } : { num: 30, den: 1 };
}

export function frameIndexAt(seconds: number, rate: { num: number; den: number }): number {
  return Math.round((seconds * rate.num) / rate.den);
}

export function secondsAtFrame(frame: number, rate: { num: number; den: number }): number {
  return (frame * rate.den) / rate.num;
}

export function formatTimecode(seconds: number): string {
  const whole = Math.max(0, seconds);
  const minutes = Math.floor(whole / 60);
  const rest = whole - minutes * 60;
  return `${minutes}:${rest.toFixed(2).padStart(5, "0")}`;
}
