import { useState, type CSSProperties } from "react";
import type { StagePoint } from "../api/types";
import { num, volume } from "../format";

/**
 * How much water the terrain holds at each depth, from the flood-fill curve.
 *
 * Storage against depth is a continuous domain, so it is a line rather than
 * columns. Only storage volume is plotted: flooded area is the other measure the
 * curve carries, and it is on a different scale, so it belongs in the tooltip
 * and the table rather than on a second axis.
 *
 * The curve has two states, and the distinction is the point of the chart. Past
 * the depth where the flood fill stopped being contained by terrain, the volume
 * is no longer an impoundment -- it is what the water would occupy if something
 * held it in, which nothing does. Drawing that as the same solid line would
 * overstate the site.
 */

const BOUNDED_FILL = "#3987e5";
const UNBOUNDED_FILL = "#4e6276";

const VIEW_W = 340;
const VIEW_H = 152;
const GUTTER_L = 34;
const PAD_R = 8;
const PLOT_TOP = 16;
const BASELINE = 116;
const DOT_R = 4;

function niceMax(peak: number): number {
  if (peak <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(peak));
  for (const step of [1, 2, 2.5, 4, 5, 10]) {
    const candidate = step * magnitude;
    if (candidate >= peak) return candidate;
  }
  return 10 * magnitude;
}

/** Axis ticks stay short so the gutter does not eat the plot. */
function compact(value: number): string {
  if (value === 0) return "0";
  if (value >= 1000) return `${num(value / 1000, value % 1000 === 0 ? 0 : 1)}k`;
  return num(value);
}

interface Plotted extends StagePoint {
  x: number;
  y: number;
}

export default function StageStorageChart({
  curve,
  designDepthM,
}: {
  curve: StagePoint[];
  designDepthM?: number;
}) {
  const [hovered, setHovered] = useState<Plotted | null>(null);

  if (curve.length < 2) return null;

  const maxDepth = Math.max(...curve.map((p) => p.depth_m));
  const axisMax = niceMax(Math.max(...curve.map((p) => p.storage_volume_m3)));
  const ticks = [0, axisMax / 2, axisMax];

  const xOf = (depth: number) =>
    GUTTER_L +
    (maxDepth === 0 ? 0 : depth / maxDepth) * (VIEW_W - GUTTER_L - PAD_R);
  const yOf = (v: number) => BASELINE - (v / axisMax) * (BASELINE - PLOT_TOP);

  const points: Plotted[] = curve.map((p) => ({
    ...p,
    x: xOf(p.depth_m),
    y: yOf(p.storage_volume_m3),
  }));

  // The backend ends the curve at the level where terrain stops containing the
  // water, so there is at most one unbounded point and it is the last. That
  // final step is drawn dashed to mark where containment failed, and it needs
  // its predecessor to be a segment at all.
  const firstUnbounded = points.findIndex((p) => p.unbounded);
  const bounded =
    firstUnbounded === -1 ? points : points.slice(0, firstUnbounded + 1);
  const unbounded = firstUnbounded <= 0 ? [] : points.slice(firstUnbounded - 1);

  const line = (pts: Plotted[]) => pts.map((p) => `${p.x},${p.y}`).join(" ");
  const areaPath = (pts: Plotted[]) =>
    pts.length < 2
      ? ""
      : `M${pts[0]!.x},${BASELINE} L${line(pts).replace(/ /g, " L")} L${pts[pts.length - 1]!.x},${BASELINE} Z`;

  const last = points[points.length - 1]!;
  const lastBounded = bounded[bounded.length - 1]!;
  const endsUncontained = unbounded.length > 1;

  const label =
    `Storage against depth, 0 to ${num(maxDepth, 2)} m. ` +
    `Terrain holds ${num(lastBounded.storage_volume_m3)} cubic metres at ` +
    `${num(lastBounded.depth_m, 2)} m` +
    (endsUncontained
      ? ", beyond which the water is not contained by terrain."
      : ".");

  const tipStyle = (p: Plotted): CSSProperties => {
    const fraction = (p.x - GUTTER_L) / (VIEW_W - GUTTER_L - PAD_R);
    if (fraction < 0.2) return { left: 0, transform: "none" };
    if (fraction > 0.8) return { right: 0, left: "auto", transform: "none" };
    return { left: `${(p.x / VIEW_W) * 100}%` };
  };

  return (
    <figure className="chart chart--stage">
      <figcaption>
        Stage–storage <span className="muted small">m³ held at each depth</span>
      </figcaption>

      {/* Two states of one series, so identity is never colour alone. */}
      {endsUncontained && (
        <ul className="chart-legend">
          <li>
            <span className="swatch" style={{ background: BOUNDED_FILL }} />{" "}
            held by terrain
          </li>
          <li>
            <span
              className="swatch swatch--dashed"
              style={{ background: UNBOUNDED_FILL }}
            />{" "}
            not contained
          </li>
        </ul>
      )}

      <div className="chart-plot">
        <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" aria-label={label}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line
                className="chart-grid"
                x1={GUTTER_L}
                x2={VIEW_W - PAD_R}
                y1={yOf(tick)}
                y2={yOf(tick)}
              />
              <text
                className="chart-tick"
                x={GUTTER_L - 6}
                y={yOf(tick) + 3}
                textAnchor="end"
              >
                {compact(tick)}
              </text>
            </g>
          ))}

          {/* A wash under the impounded part only: shading the unbounded tail
              would imply storage that terrain does not provide. */}
          <path d={areaPath(bounded)} fill={BOUNDED_FILL} fillOpacity={0.1} />
          <polyline
            className="chart-line"
            points={line(bounded)}
            stroke={BOUNDED_FILL}
          />
          {endsUncontained && (
            <polyline
              className="chart-line chart-line--dashed"
              points={line(unbounded)}
              stroke={UNBOUNDED_FILL}
            />
          )}

          {/* Where the design actually sits on the curve. */}
          {designDepthM != null && designDepthM <= maxDepth && (
            <g>
              <line
                className="chart-rule"
                x1={xOf(designDepthM)}
                x2={xOf(designDepthM)}
                y1={PLOT_TOP - 6}
                y2={BASELINE}
              />
              <text
                className="chart-annotation"
                x={xOf(designDepthM)}
                y={PLOT_TOP - 9}
                textAnchor="end"
              >
                design {num(designDepthM, 1)} m
              </text>
            </g>
          )}

          <circle
            cx={lastBounded.x}
            cy={lastBounded.y}
            r={DOT_R}
            fill={BOUNDED_FILL}
            className="chart-dot"
          />
          <text
            className="chart-value"
            x={lastBounded.x - 6}
            y={lastBounded.y - 7}
            textAnchor="end"
          >
            {compact(lastBounded.storage_volume_m3)}
          </text>

          <text className="chart-tick" x={GUTTER_L} y={BASELINE + 13}>
            0 m
          </text>
          <text
            className="chart-tick"
            x={VIEW_W - PAD_R}
            y={BASELINE + 13}
            textAnchor="end"
          >
            {num(maxDepth, 1)} m
          </text>

          {/* A crosshair needs a hit band per point, wider than the dot. */}
          {points.map((p, i) => {
            const half =
              (VIEW_W - GUTTER_L - PAD_R) / Math.max(1, points.length - 1) / 2;
            return (
              <rect
                key={`${p.depth_m}-${i}`}
                x={p.x - half}
                y={PLOT_TOP - 10}
                width={half * 2}
                height={BASELINE - PLOT_TOP + 10}
                fill="transparent"
                onMouseEnter={() => setHovered(p)}
                onMouseLeave={() => setHovered(null)}
              >
                <title>{`${num(p.depth_m, 2)} m: ${num(p.storage_volume_m3)} m³`}</title>
              </rect>
            );
          })}

          {hovered && (
            <g className="chart-crosshair">
              <line
                x1={hovered.x}
                x2={hovered.x}
                y1={PLOT_TOP - 6}
                y2={BASELINE}
              />
              <circle
                cx={hovered.x}
                cy={hovered.y}
                r={DOT_R}
                className="chart-dot"
              />
            </g>
          )}
        </svg>

        {hovered && (
          <div className="chart-tip" style={tipStyle(hovered)}>
            <strong>{num(hovered.depth_m, 2)} m</strong>{" "}
            {volume(hovered.storage_volume_m3)}
            <span className="muted small">
              {" "}
              · {num(hovered.flooded_area_m2)} m² wet
            </span>
            {hovered.unbounded && (
              <span className="muted small"> · not contained</span>
            )}
          </div>
        )}
      </div>

      <details className="chart-table">
        <summary>Curve values</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Depth (m)</th>
              <th scope="col">Storage (m³)</th>
              <th scope="col">Wet area (m²)</th>
              <th scope="col">Held</th>
            </tr>
          </thead>
          <tbody>
            {curve.map((p) => (
              <tr key={p.depth_m}>
                <th scope="row">{num(p.depth_m, 2)}</th>
                <td>{num(p.storage_volume_m3)}</td>
                <td>{num(p.flooded_area_m2)}</td>
                <td>{p.unbounded ? "no" : "yes"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>

      {endsUncontained && (
        <p className="muted small">
          The curve stops at {num(last.depth_m, 2)} m because terrain stops
          holding the water there — the fill spread out rather than reaching a
          rim. Storing more than {volume(last.storage_volume_m3)} here means
          excavating or bunding for it, which is what the design figures
          describe.
        </p>
      )}
    </figure>
  );
}
