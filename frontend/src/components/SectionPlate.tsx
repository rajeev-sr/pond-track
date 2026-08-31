import { num } from "../format";

interface Props {
  /** Full supply level, m. Omitted on the brief, where the plate is a schematic. */
  fslM?: number | null;
  /** Design depth, m — the dimension between full supply and the excavated floor. */
  depthM?: number | null;
  /** Freeboard from full supply to the bund crest, m. */
  freeboardM?: number;
  caption?: string;
  height?: number;
}

/**
 * Long section through the proposed pond.
 *
 * The geometry is the point: a valley falling left to right, a bund thrown across
 * it, water impounded *behind* the bund, and the floor excavated below natural
 * ground. An earlier draft had the ground rising between the two water edges,
 * which drew a pond cut into a hillside — wrong, and wrong in a way that would
 * mislead anyone who reads sections for a living.
 *
 * Vertical exaggeration is unavoidable at this aspect ratio and is stated in the
 * caption rather than left for the reader to infer.
 */
export function SectionPlate({
  fslM = null,
  depthM = null,
  freeboardM = 1.2,
  caption = "Long section, recommended site",
  height,
}: Props) {
  const known = fslM != null && depthM != null;
  const rl = (v: number) => (known ? num(v, 2) : null);

  return (
    <figure className="plate" style={{ margin: 0 }}>
      <span className="tick-l" />
      <span className="tick-r" />
      <svg
        viewBox="0 0 620 380"
        style={height ? { height } : undefined}
        role="img"
        aria-label="Long section: a valley falling to the right, a bund across it, water impounded behind the bund, and the floor excavated below natural ground."
      >
        <defs>
          <pattern
            id="sp-water"
            width="7"
            height="7"
            patternTransform="rotate(45)"
            patternUnits="userSpaceOnUse"
          >
            <line x1="0" y1="0" x2="0" y2="7" stroke="var(--water-2)" strokeWidth="1.1" opacity=".6" />
          </pattern>
          <pattern
            id="sp-earth"
            width="6"
            height="6"
            patternTransform="rotate(-45)"
            patternUnits="userSpaceOnUse"
          >
            <line x1="0" y1="0" x2="0" y2="6" stroke="var(--earth)" strokeWidth="1" opacity=".45" />
          </pattern>
        </defs>

        {/* levelling grid, 1 m per 25 px */}
        <g stroke="var(--rule)" strokeWidth=".7">
          {[60, 110, 160, 210, 260, 310].map((y) => (
            <line key={y} x1="64" y1={y} x2="600" y2={y} />
          ))}
        </g>
        {known && (
          <g fontSize="9" fill="var(--ink-3)" textAnchor="end" fontFamily="var(--mono)">
            {[4, 2, 0, -2, -4, -6].map((d, i) => (
              <text key={d} x="56" y={63 + i * 50}>
                {num(fslM + d, 0)}
              </text>
            ))}
          </g>
        )}
        <text
          x="22"
          y="200"
          fontSize="9"
          fill="var(--ink-3)"
          letterSpacing="1.4"
          transform="rotate(-90 22 200)"
          fontFamily="var(--mono)"
        >
          {known ? "REDUCED LEVEL, m" : "LEVEL"}
        </text>

        {/* excavated deepening, then the impounded wedge */}
        <path d="M268 175 L438 244 L430 272 L300 272 Z" fill="url(#sp-earth)" />
        <path d="M232 160 L438 160 L438 244 Z" fill="url(#sp-water)" />
        <path d="M268 175 L438 244 L430 272 L300 272 Z" fill="url(#sp-water)" fillOpacity=".5" />

        {/* full supply level */}
        <line x1="224" y1="160" x2="452" y2="160" stroke="var(--water)" strokeWidth="1.7" />
        {/* The conventional water-surface symbol: two short strokes stepping
            away from the line. Kept to the left of the label — at x=246 they sat
            directly under it. */}
        <g stroke="var(--water)" strokeWidth="1.1">
          <line x1="196" y1="153" x2="208" y2="153" />
          <line x1="214" y1="149" x2="222" y2="149" />
        </g>
        <text x="240" y="150" fontSize="10" fill="var(--water)" fontFamily="var(--mono)">
          {known ? `FSL ${rl(fslM)}` : "FULL SUPPLY LEVEL"}
        </text>

        {/* original ground: a valley falling left to right */}
        <path
          d="M64 88 L232 160 L438 244 L520 272 L600 302"
          fill="none"
          stroke="var(--ink)"
          strokeWidth="2"
        />
        {/* excavated profile, dashed as on a drawing */}
        <path
          d="M268 175 L300 272 L430 272 L438 244"
          fill="none"
          stroke="var(--earth)"
          strokeWidth="1.3"
          strokeDasharray="5 3"
        />

        {/* bund across the valley */}
        <path
          d="M438 244 L462 130 L496 130 L520 272 Z"
          fill="var(--paper-3)"
          stroke="var(--ink)"
          strokeWidth="1.6"
        />
        <line x1="462" y1="130" x2="496" y2="130" stroke="var(--ink)" strokeWidth="2.6" />

        {/* depth dimension, full supply to excavated floor */}
        <g stroke="var(--ink-2)" strokeWidth="1">
          <line x1="352" y1="160" x2="352" y2="272" />
          <path d="M348 165 L352 160 L356 165" fill="none" />
          <path d="M348 267 L352 272 L356 267" fill="none" />
        </g>
        <rect x="358" y="205" width="58" height="15" fill="var(--paper)" />
        <text x="360" y="216" fontSize="11" fill="var(--ink)" fontFamily="var(--mono)">
          {depthM != null ? `${num(depthM, 2)} m` : "depth d"}
        </text>

        {/* annotations */}
        <g stroke="var(--ink-3)" strokeWidth=".9" fill="none">
          <path d="M479 130 L479 100 L520 100" />
          <path d="M348 272 L372 320 L404 320" />
        </g>
        <g fontSize="10" fill="var(--ink-2)">
          <text x="600" y="97" textAnchor="end">
            {known ? `bund crest RL ${rl(fslM + freeboardM)}` : "bund crest"}
          </text>
          <text x="408" y="323">
            {known ? `excavated to RL ${rl(fslM - depthM)}` : "excavated floor"}
          </text>
        </g>
      </svg>
      <div className="plate-cap">
        <span>{caption}</span>
        <span>V exagg.</span>
      </div>
    </figure>
  );
}
