"use client";

import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { Button, Hint, Stage, TapeX } from "@/components/ui";
import type { Annotations, SelectionBox, SelectionPoint } from "@/lib/types";
import {
  displayToSource,
  formatTimecode,
  frameIndexAt,
  parseFrameRate,
  secondsAtFrame,
  sourceToDisplay,
  type Point,
  type Size,
} from "./coords";

export type Tool = "mark" | "exclude" | "paint" | "box";

const TOOLS: { id: Tool; label: string; hint: string }[] = [
  { id: "mark", label: "Mark the dancer", hint: "Click on the dancer you want to replace." },
  { id: "exclude", label: "Not this", hint: "Click on anything that was picked up by mistake." },
  { id: "paint", label: "Paint", hint: "Drag over the dancer to add several marks at once." },
  { id: "box", label: "Box", hint: "Drag a box around the dancer." },
];

const PAINT_SPACING = 0.04;
const MAX_PAINT_POINTS = 24;
const KEYBOARD_STEP = 0.01;

type Props = {
  url: string;
  frameRate: string | undefined;
  annotations: Annotations;
  onChange: (next: Annotations) => void;
  onFrameChange: (frame: number, seconds: number) => void;
};

/** Marks are stored normalized to the displayed frame, tagged with the frame they were placed on. */
export function SelectionCanvas({ url, frameRate, annotations, onChange, onFrameChange }: Props) {
  const rate = parseFrameRate(frameRate);
  const video = useRef<HTMLVideoElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const [element, setElement] = useState<Size>({ width: 1, height: 1 });
  const [natural, setNatural] = useState<Size | null>(null);
  const [duration, setDuration] = useState(0);
  const [time, setTime] = useState(0);
  const [tool, setTool] = useState<Tool>("mark");
  const [cursor, setCursor] = useState<Point | null>(null);
  const [dragStart, setDragStart] = useState<Point | null>(null);
  const [dragNow, setDragNow] = useState<Point | null>(null);
  const painting = useRef<number>(0);

  const frame = frameIndexAt(time, rate);
  const points = annotations.points ?? [];
  const box = annotations.box;

  useEffect(() => {
    const node = surface.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setElement({ width: node.clientWidth, height: node.clientHeight }));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  function seek(seconds: number) {
    const bounded = Math.min(Math.max(seconds, 0), duration || seconds);
    if (video.current) video.current.currentTime = bounded;
    setTime(bounded);
    onFrameChange(frameIndexAt(bounded, rate), bounded);
  }

  function toSource(event: PointerEvent<HTMLDivElement>): Point | null {
    if (!natural || !surface.current) return null;
    const bounds = surface.current.getBoundingClientRect();
    return displayToSource({ x: event.clientX - bounds.left, y: event.clientY - bounds.top }, element, natural);
  }

  function addPoint(at: Point, positive: boolean) {
    const point: SelectionPoint = { x: at.x, y: at.y, positive, frame };
    onChange({ ...annotations, points: [...points, point] });
  }

  function onPointerDown(event: PointerEvent<HTMLDivElement>) {
    const at = toSource(event);
    if (!at) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    if (tool === "mark") addPoint(at, true);
    if (tool === "exclude") addPoint(at, false);
    if (tool === "box") {
      setDragStart(at);
      setDragNow(at);
    }
    if (tool === "paint") {
      painting.current = 1;
      addPoint(at, true);
    }
  }

  function onPointerMove(event: PointerEvent<HTMLDivElement>) {
    const at = toSource(event);
    if (!at) return;
    if (tool === "box" && dragStart) setDragNow(at);
    if (tool === "paint" && painting.current > 0 && painting.current < MAX_PAINT_POINTS) {
      const last = points[points.length - 1];
      if (!last || Math.hypot(last.x - at.x, last.y - at.y) >= PAINT_SPACING) {
        painting.current += 1;
        addPoint(at, true);
      }
    }
  }

  function onPointerUp() {
    painting.current = 0;
    if (tool === "box" && dragStart && dragNow) {
      const next: SelectionBox = {
        x: Math.min(dragStart.x, dragNow.x),
        y: Math.min(dragStart.y, dragNow.y),
        width: Math.abs(dragNow.x - dragStart.x),
        height: Math.abs(dragNow.y - dragStart.y),
        frame,
      };
      if (next.width > 0.01 && next.height > 0.01) onChange({ ...annotations, box: next });
    }
    setDragStart(null);
    setDragNow(null);
  }

  /** The canvas works without a pointer: arrows move a crosshair, Enter places a mark, Backspace removes one. */
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const moves: Record<string, [number, number]> = {
      ArrowLeft: [-KEYBOARD_STEP, 0],
      ArrowRight: [KEYBOARD_STEP, 0],
      ArrowUp: [0, -KEYBOARD_STEP],
      ArrowDown: [0, KEYBOARD_STEP],
    };
    const move = moves[event.key];
    if (move) {
      event.preventDefault();
      const from = cursor ?? { x: 0.5, y: 0.5 };
      setCursor({ x: clamp(from.x + move[0]), y: clamp(from.y + move[1]) });
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      addPoint(cursor ?? { x: 0.5, y: 0.5 }, tool !== "exclude");
    } else if (event.key === "Backspace" || event.key === "Delete") {
      event.preventDefault();
      onChange({ ...annotations, points: points.slice(0, -1) });
    }
  }

  const shown = natural ? points.filter((point) => point.frame === frame) : [];
  const dragBox = dragStart && dragNow ? normalize(dragStart, dragNow) : null;
  const liveBox = dragBox ?? (box && box.frame === frame ? box : null);

  return (
    <div className="space-y-3">
      <div role="group" aria-label="Selection tools" className="flex flex-wrap gap-2">
        {TOOLS.map((option) => (
          <button
            key={option.id}
            type="button"
            aria-pressed={tool === option.id}
            onClick={() => setTool(option.id)}
            className={`rounded-sm border px-3 py-1.5 text-sm font-semibold ${
              tool === option.id ? "border-tape bg-tape text-white" : "border-ink bg-sheen hover:bg-white"
            }`}
          >
            {option.label}
          </button>
        ))}
        <Button variant="quiet" className="ml-auto text-sm" disabled={!points.length} onClick={() => onChange({ ...annotations, points: points.slice(0, -1) })}>
          Undo last mark
        </Button>
        <Button variant="quiet" className="text-sm" disabled={!points.length && !box} onClick={() => onChange({})}>
          Clear all
        </Button>
      </div>
      <Hint>{TOOLS.find((option) => option.id === tool)?.hint}</Hint>

      <Stage>
        <div
          ref={surface}
          role="application"
          aria-label="Video frame. Use arrow keys to move the crosshair, Enter to place a mark, Backspace to remove the last mark."
          tabIndex={0}
          data-testid="selection-surface"
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onKeyDown={onKeyDown}
          className="relative mx-auto h-[56vh] w-full touch-none cursor-crosshair select-none"
        >
          <video
            ref={video}
            src={url}
            muted
            playsInline
            preload="auto"
            className="pointer-events-none size-full object-contain"
            onLoadedMetadata={(event) => {
              const node = event.currentTarget;
              setNatural({ width: node.videoWidth, height: node.videoHeight });
              setDuration(node.duration);
            }}
          />
          {shown.map((point, index) => {
            const at = sourceToDisplay(point, element, natural!);
            return (
              <TapeX
                key={`${index}-${point.x}`}
                size={28}
                color={point.positive ? "var(--color-tape)" : "#7ad6e0"}
                className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2"
                style={{ left: at.x, top: at.y }}
              />
            );
          })}
          {natural && liveBox ? <BoxOverlay box={liveBox} element={element} natural={natural} /> : null}
          {natural && cursor ? (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute size-4 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-hazard"
              style={cursorStyle(sourceToDisplay(cursor, element, natural))}
            />
          ) : null}
        </div>
      </Stage>

      <div className="flex flex-wrap items-center gap-3">
        <Button className="px-2! py-1! text-sm" aria-label="Previous frame" onClick={() => seek(secondsAtFrame(frame - 1, rate))}>
          Back one frame
        </Button>
        <input
          type="range"
          aria-label="Frame position"
          min={0}
          max={duration || 0}
          step={rate.den / rate.num}
          value={time}
          onChange={(event) => seek(Number(event.target.value))}
          className="min-w-40 flex-1"
        />
        <Button className="px-2! py-1! text-sm" aria-label="Next frame" onClick={() => seek(secondsAtFrame(frame + 1, rate))}>
          Forward one frame
        </Button>
        <span className="timecode text-sm" data-testid="frame-position">
          {formatTimecode(time)} of {formatTimecode(duration)}, frame {frame}
        </span>
      </div>
      <p className="text-sm text-ink-soft">
        {points.length} {points.length === 1 ? "mark" : "marks"}
        {box ? " and a box" : ""}. Marks show on the frame where you placed them.
      </p>
    </div>
  );
}

function cursorStyle(at: Point) {
  return { left: at.x, top: at.y };
}

function clamp(value: number): number {
  return Math.min(Math.max(value, 0), 1);
}

function normalize(start: Point, end: Point) {
  return {
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  };
}

function BoxOverlay({ box, element, natural }: { box: { x: number; y: number; width: number; height: number }; element: Size; natural: Size }) {
  const topLeft = sourceToDisplay({ x: box.x, y: box.y }, element, natural);
  const bottomRight = sourceToDisplay({ x: box.x + box.width, y: box.y + box.height }, element, natural);
  return (
    <span
      aria-hidden="true"
      className="pointer-events-none absolute border-3 border-tape"
      style={{ left: topLeft.x, top: topLeft.y, width: bottomRight.x - topLeft.x, height: bottomRight.y - topLeft.y }}
    />
  );
}
