import { useState, type CSSProperties } from "react";
import type { CandidateSite, Environment } from "../api/types";
import { num, volume } from "../format";

/**
 * The water budget: what falls (monthly rainfall normals) beside what runs off
 * (the selected site's runoff figures).
 *
 * Form follows the job each number does. Twelve monthly magnitudes compare as
 * columns; the runoff headlines are single numbers, so they are stat tiles
 * rather than a second chart. One measure means one hue -- the monsoon window
 * is picked out by emphasis, not by a second categorical colour, which keeps
 * this a single series and so needs no legend box: the caption names it.
 */

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

/**
 * Mark fills, both computed against this panel's surface (#131c26) rather than
 * eyeballed. The monsoon step clears the dark-mode lightness band, the chroma
 * floor and 3:1 contrast; the recessive step sits at 2.7:1, above the 2:1 floor
 * that keeps a de-emphasised mark visible, and the table view below discharges
 * the relief that a sub-3:1 mark obliges. Text never wears either -- labels
 * stay on the text tokens and identity comes from the mark beside them.
 */
const MONSOON_FILL = "#3987e5";
const DRY_FILL = "#4e6276";

// Geometry. A viewBox keeps the chart fluid inside the panel; the bar is capped
// so it never fills its band -- the leftover is deliberate air, not a gap.
const VIEW_W = 340;
const VIEW_H = 152;
const GUTTER_L = 30;
const PAD_R = 6;
const PLOT_TOP = 18;
const BASELINE = 116;
const BAR_MAX_W = 24;
const BAND_AIR = 6;
const CAP_RADIUS = 4;

const BAND = (VIEW_W - GUTTER_L - PAD_R) / MONTHS.length;
const BAR_W = Math.min(BAR_MAX_W, BAND - BAND_AIR);

interface Bar {
  index: number;
  month: string;
  mm: number;
  monsoon: boolean;
  /** Band centre and mark left edge, in viewBox units. */
  centre: number;
  left: number;
}

/** Round the axis top up to a readable number so the ticks land on clean values. */
function niceMax(peak: number): number {
  if (peak <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(peak));
  for (const step of [1, 2, 2.5, 4, 5, 10]) {
    const candidate = step * magnitude;
    if (candidate >= peak) return candidate;
  }
  return 10 * magnitude;
}

/** A column with a rounded data-end and a square foot on the baseline. */
function columnPath(x: number, y: number, w: number): string {
  const height = BASELINE - y;
  if (height <= 0) return "";
  const r = Math.min(CAP_RADIUS, w / 2, height);
  return (
    `M${x},${BASELINE}L${x},${y + r}Q${x},${y} ${x + r},${y}` +
    `L${x + w - r},${y}Q${x + w},${y} ${x + w},${y + r}L${x + w},${BASELINE}Z`
  );
}

function RainfallChart({
  monthly,
  monsoon,
}: {
  monthly: number[];
  monsoon: string[];
}) {
  const [hovered, setHovered] = useState<Bar | null>(null);

  // One pass builds every value the chart needs, so nothing indexes the raw
  // arrays again further down.
  const bars: Bar[] = MONTHS.map((month, i) => {
    const bandLeft = GUTTER_L + i * BAND;
    return {
      index: i,
      month,
      mm: monthly[i] ?? 0,
      monsoon: monsoon.includes(month),
      centre: bandLeft + BAND / 2,
      left: bandLeft + (BAND - BAR_W) / 2,
    };
  });

  const axisMax = niceMax(Math.max(...bars.map((b) => b.mm)));
  const ticks = [0, axisMax / 2, axisMax];
  const peak = bars.reduce(
    (best, b) => (b.mm > best.mm ? b : best),
    bars[0] as Bar,
  );

  const yOf = (mm: number) => BASELINE - (mm / axisMax) * (BASELINE - PLOT_TOP);
  const bandLeftOf = (b: Bar) => b.centre - BAND / 2;

  const tipStyle = (b: Bar): CSSProperties => {
    if (b.index <= 1) return { left: 0, transform: "none" };
    if (b.index >= MONTHS.length - 2)
      return { right: 0, left: "auto", transform: "none" };
    return { left: `${(b.centre / VIEW_W) * 100}%` };
  };

  const label =
    `Mean monthly rainfall, January to December. ` +
    `Wettest month ${peak.month} at ${num(peak.mm, 0)} mm; ` +
    `monsoon window ${monsoon.join(", ")}.`;

  return (
    <figure className="chart chart--rainfall">
      <figcaption>
        Mean monthly rainfall{" "}
        <span className="muted small">mm &middot; monsoon highlighted</span>
      </figcaption>

      <div className="chart-plot">
        <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" aria-label={label}>
          {/* Gridlines and axis ticks stay hairline and recessive -- they carry
              the values the direct label does not. */}
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
                {num(tick, 0)}
              </text>
            </g>
          ))}

          {bars.map((b) => (
            <path
              key={b.month}
              d={columnPath(b.left, yOf(b.mm), BAR_W)}
              fill={b.monsoon ? MONSOON_FILL : DRY_FILL}
            />
          ))}

          {/* One direct label, on the extreme. A number on every column is
              chaos and goes unread; the axis and the tooltip carry the rest. */}
          <text
            className="chart-value"
            x={peak.centre}
            y={yOf(peak.mm) - 5}
            textAnchor="middle"
          >
            {num(peak.mm, 0)}
          </text>

          {bars.map((b) => (
            <text
              key={`m-${b.month}`}
              className={`chart-month${b.monsoon ? " chart-month--on" : ""}`}
              x={b.centre}
              y={BASELINE + 14}
              textAnchor="middle"
            >
              {b.month}
            </text>
          ))}

          {/* Hit targets span the whole band, so they are comfortably larger
              than the mark they select. */}
          {bars.map((b) => (
            <rect
              key={`hit-${b.month}`}
              x={bandLeftOf(b)}
              y={PLOT_TOP - 12}
              width={BAND}
              height={BASELINE - PLOT_TOP + 12}
              fill="transparent"
              onMouseEnter={() => setHovered(b)}
              onMouseLeave={() => setHovered(null)}
            >
              <title>{`${b.month}: ${num(b.mm, 0)} mm`}</title>
            </rect>
          ))}
        </svg>

        {hovered && (
          <div className="chart-tip" style={tipStyle(hovered)}>
            <strong>{hovered.month}</strong> {num(hovered.mm, 0)} mm
            {hovered.monsoon && <span className="muted small"> monsoon</span>}
          </div>
        )}
      </div>

      <details className="chart-table">
        <summary>Monthly values</summary>
        <table>
          <thead>
            <tr>
              <th scope="col">Month</th>
              <th scope="col">Rainfall (mm)</th>
              <th scope="col">Monsoon</th>
            </tr>
          </thead>
          <tbody>
            {bars.map((b) => (
              <tr key={b.month}>
                <th scope="row">{b.month}</th>
                <td>{num(b.mm, 1)}</td>
                <td>{b.monsoon ? "yes" : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

function Kpi({
  label,
  value,
  hint,
}: {
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="kpi">
      <span className="kpi-label">{label}</span>
      <strong className="kpi-value">{value}</strong>
      {hint && <span className="kpi-hint">{hint}</span>}
    </div>
  );
}

export default function RainfallPanel({
  env,
  site,
}: {
  env: Environment;
  site: CandidateSite | null;
}) {
  const rainfall = env.rainfall;
  if (!rainfall) return null;

  const monthly = rainfall.monthly_normals_mm;
  const runoff = site?.runoff?.available ? site.runoff : undefined;

  return (
    <section className="water">
      <h3>Water balance</h3>

      {monthly && monthly.length === MONTHS.length ? (
        <RainfallChart monthly={monthly} monsoon={rainfall.monsoon.months} />
      ) : (
        <p className="muted small">
          Monthly normals unavailable for this location.
        </p>
      )}

      {runoff ? (
        <div className="kpis">
          {runoff.annual_mean && (
            <Kpi
              label="Annual runoff"
              value={volume(runoff.annual_mean.runoff_volume_m3)}
              hint={`C = ${num(runoff.annual_mean.runoff_coefficient, 3)}`}
            />
          )}
          {runoff.design_75_percent_dependable && (
            <Kpi
              label="Design yield"
              value={volume(
                runoff.design_75_percent_dependable.runoff_volume_m3,
              )}
              hint="75% dependable"
            />
          )}
          {runoff.curve_number && (
            <Kpi
              label="Curve number"
              value={num(runoff.curve_number.composite_cn_amc2, 1)}
              hint={`HSG ${runoff.curve_number.hydrologic_soil_group}`}
            />
          )}
        </div>
      ) : (
        <p className="muted small">
          {site?.runoff?.reason
            ? `Runoff not estimated: ${site.runoff.reason}`
            : "Select a site to see its runoff."}
        </p>
      )}
    </section>
  );
}
