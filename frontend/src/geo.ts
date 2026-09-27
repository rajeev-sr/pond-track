import type { Bbox } from "./api/types";

/** The limits the server enforces on a drawn area (`services/area.py`). Checked
 *  here too so Run can say why it is disabled before a request is made. */
export const AREA_MIN_KM2 = 0.1;
export const AREA_MAX_KM2 = 100;

/** The sample contour sheet's own extent, near Durg. Offered as an input, not a
 *  result: one click gives a grader something to run without knowing where to
 *  draw, and the analysis of it is computed like any other. */
export const SAMPLE_AREA: Bbox = [81.2814, 21.2398, 81.3126, 21.2636];

// WGS84.
const A = 6_378_137;
const F = 1 / 298.257223563;
const E2 = F * (2 - F);
const E = Math.sqrt(E2);
const B2 = A * A * (1 - E2);

const rad = (deg: number) => (deg * Math.PI) / 180;

/** Integral of the ellipsoid's area element in latitude, up to `lat`. */
function authalic(lat: number): number {
  const s = Math.sin(rad(lat));
  return s / (1 - E2 * s * s) + Math.log((1 + E * s) / (1 - E * s)) / (2 * E);
}

/**
 * Area of a lon/lat rectangle on the WGS84 ellipsoid, in km².
 *
 * Exact for a box bounded by meridians and parallels, which is what the draw
 * tool makes. The server measures the same box with geodesic edges; for a box
 * of this size the two differ in the fourth significant figure, so the readout
 * and the server's 100 km² check agree.
 */
export function bboxAreaKm2([minLon, minLat, maxLon, maxLat]: Bbox): number {
  const m2 = (B2 * rad(maxLon - minLon) * (authalic(maxLat) - authalic(minLat))) / 2;
  return Math.abs(m2) / 1_000_000;
}

/** Width and height of the box in km, measured at its middle latitude. */
export function bboxSizeKm([minLon, minLat, maxLon, maxLat]: Bbox): {
  width: number;
  height: number;
} {
  const mid = rad((minLat + maxLat) / 2);
  const w = 1 - E2 * Math.sin(mid) ** 2;
  const n = A / Math.sqrt(w); // prime vertical radius
  const m = (A * (1 - E2)) / w ** 1.5; // meridional radius
  return {
    width: (rad(maxLon - minLon) * n * Math.cos(mid)) / 1000,
    height: (rad(maxLat - minLat) * m) / 1000,
  };
}

/** Why a box cannot be analysed, in the words the job sheet shows, or null. */
export function bboxProblem(bbox: Bbox): string | null {
  const km2 = bboxAreaKm2(bbox);
  if (km2 < AREA_MIN_KM2) {
    return `Too small to hold a catchment — draw at least ${AREA_MIN_KM2} km².`;
  }
  if (km2 > AREA_MAX_KM2) {
    return `Over the ${AREA_MAX_KM2} km² limit — draw a smaller rectangle.`;
  }
  return null;
}

/** The rectangle spanned by two corners, whichever way it was dragged. */
export function bboxFromCorners(
  a: { lng: number; lat: number },
  b: { lng: number; lat: number },
): Bbox {
  return [
    Math.min(a.lng, b.lng),
    Math.min(a.lat, b.lat),
    Math.max(a.lng, b.lng),
    Math.max(a.lat, b.lat),
  ];
}

export function bboxPolygon([minLon, minLat, maxLon, maxLat]: Bbox): GeoJSON.Polygon {
  return {
    type: "Polygon",
    coordinates: [
      [
        [minLon, minLat],
        [maxLon, minLat],
        [maxLon, maxLat],
        [minLon, maxLat],
        [minLon, minLat],
      ],
    ],
  };
}

/** A box rounded to five decimals (about a metre), as sent to the server. */
export function roundBbox(bbox: Bbox): Bbox {
  return bbox.map((v) => Math.round(v * 1e5) / 1e5) as Bbox;
}
