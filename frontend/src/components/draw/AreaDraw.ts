import type { Map as MapLibreMap, MapMouseEvent, MapTouchEvent } from "maplibre-gl";

import type { Bbox } from "../../api/types";
import { bboxFromCorners } from "../../geo";

export interface AreaDrawCallbacks {
  /** Every pointer move while dragging, for the live outline and readout. */
  onChange: (bbox: Bbox, at: { x: number; y: number }) => void;
  /** Released: the rectangle is drawn. */
  onComplete: (bbox: Bbox) => void;
  /** Escape. The caller decides what cancelling restores. */
  onCancel: () => void;
}

/** Below this many pixels a press is a click, not a rectangle. */
const MIN_DRAG_PX = 5;

/**
 * Press-and-drag rectangle drawing on MapLibre's own events.
 *
 * No drawing library: a rectangle is two corners, and `maplibre-gl-draw` would
 * add more code than the rest of the map for the one shape the tool accepts.
 * While active, dragging draws instead of panning, so pan, box-zoom and
 * double-click zoom are switched off and restored exactly as they were found.
 * The wheel still zooms, so the view can be adjusted mid-task.
 *
 * Returns the function that stops drawing.
 */
export function startAreaDraw(map: MapLibreMap, cb: AreaDrawCallbacks): () => void {
  const canvas = map.getCanvasContainer();
  const had = {
    dragPan: map.dragPan.isEnabled(),
    boxZoom: map.boxZoom.isEnabled(),
    doubleClickZoom: map.doubleClickZoom.isEnabled(),
  };
  map.dragPan.disable();
  map.boxZoom.disable();
  map.doubleClickZoom.disable();
  canvas.classList.add("is-drawing");

  let start: { lng: number; lat: number } | null = null;
  let startPx: { x: number; y: number } | null = null;
  let last: Bbox | null = null;

  const begin = (lngLat: { lng: number; lat: number }, point: { x: number; y: number }) => {
    start = lngLat;
    startPx = point;
    last = null;
  };

  const move = (lngLat: { lng: number; lat: number }, point: { x: number; y: number }) => {
    if (!start) return;
    last = bboxFromCorners(start, lngLat);
    cb.onChange(last, point);
  };

  const end = (point: { x: number; y: number } | null) => {
    if (!start || !startPx) return;
    const moved = point ? Math.hypot(point.x - startPx.x, point.y - startPx.y) : Infinity;
    const drawn = last;
    start = null;
    startPx = null;
    last = null;
    // A press without a drag is a stray click; stay in draw mode for the real one.
    if (drawn && moved >= MIN_DRAG_PX) cb.onComplete(drawn);
  };

  const onMouseDown = (e: MapMouseEvent) => {
    if (e.originalEvent.button !== 0) return;
    e.preventDefault();
    begin(e.lngLat.wrap(), e.point);
  };
  const onMouseMove = (e: MapMouseEvent) => move(e.lngLat.wrap(), e.point);
  const onMouseUp = (e: MapMouseEvent) => end(e.point);
  // Released outside the map: finish with the rectangle as last drawn, rather
  // than leaving a drag stuck open until the next press.
  const onWindowUp = () => {
    if (start) end(null);
  };

  const onTouchStart = (e: MapTouchEvent) => {
    if (e.points.length !== 1) return;
    e.preventDefault();
    begin(e.lngLat.wrap(), e.point);
  };
  const onTouchMove = (e: MapTouchEvent) => {
    if (e.points.length !== 1) return;
    e.preventDefault();
    move(e.lngLat.wrap(), e.point);
  };
  const onTouchEnd = (e: MapTouchEvent) => end(e.point);

  const onKey = (e: KeyboardEvent) => {
    if (e.key !== "Escape") return;
    start = null;
    startPx = null;
    last = null;
    cb.onCancel();
  };

  map.on("mousedown", onMouseDown);
  map.on("mousemove", onMouseMove);
  map.on("mouseup", onMouseUp);
  map.on("touchstart", onTouchStart);
  map.on("touchmove", onTouchMove);
  map.on("touchend", onTouchEnd);
  window.addEventListener("mouseup", onWindowUp);
  window.addEventListener("keydown", onKey);

  return () => {
    map.off("mousedown", onMouseDown);
    map.off("mousemove", onMouseMove);
    map.off("mouseup", onMouseUp);
    map.off("touchstart", onTouchStart);
    map.off("touchmove", onTouchMove);
    map.off("touchend", onTouchEnd);
    window.removeEventListener("mouseup", onWindowUp);
    window.removeEventListener("keydown", onKey);
    canvas.classList.remove("is-drawing");
    if (had.dragPan) map.dragPan.enable();
    if (had.boxZoom) map.boxZoom.enable();
    if (had.doubleClickZoom) map.doubleClickZoom.enable();
  };
}
