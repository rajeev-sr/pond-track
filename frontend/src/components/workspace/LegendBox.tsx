import { BASEMAPS, type BasemapId, type LayerVisibility } from "../MapView";
import type { StreamScope } from "../../api/types";
import { t } from "../../i18n";
import { useCollapsed } from "../../state/collapsed";
import { MinButton } from "./MinButton";

interface Props {
  visibility: LayerVisibility;
  onChange: (v: LayerVisibility) => void;
  available: Partial<Record<keyof LayerVisibility, boolean>>;
  streamScope: StreamScope;
  onStreamScopeChange: (s: StreamScope) => void;
  loadingStreams: boolean;
  basemap: BasemapId;
  onBasemapChange: (b: BasemapId) => void;
}

/** How each layer is drawn, so the key matches the sheet rather than describing
 *  it in words. A line for line work, a filled swatch for areas. */
const KEY: Record<keyof LayerVisibility, { kind: "line" | "fill" | "dash"; colour: string }> = {
  contours: { kind: "line", colour: "var(--contour)" },
  streams: { kind: "line", colour: "var(--water)" },
  catchment: { kind: "dash", colour: "var(--water)" },
  explored: { kind: "dash", colour: "var(--veg)" },
  pond: { kind: "fill", colour: "var(--earth)" },
  sites: { kind: "fill", colour: "var(--earth)" },
  parcels: { kind: "fill", colour: "var(--veg)" },
  village: { kind: "dash", colour: "var(--ink-2)" },
  aoi: { kind: "dash", colour: "var(--rule-2)" },
  hillshade: { kind: "fill", colour: "var(--ink-3)" },
  slope: { kind: "fill", colour: "var(--earth)" },
};

const ORDER: (keyof LayerVisibility)[] = [
  "contours",
  "streams",
  "catchment",
  "sites",
  "pond",
  "explored",
  "hillshade",
  "slope",
  "parcels",
  "village",
  "aoi",
];

/** Why a layer cannot be turned on. A disabled control with no reason is a dead
 *  end, and these are the four the reader can actually act on. */
const WHY: Partial<Record<keyof LayerVisibility, string>> = {
  contours: "include contours on the run",
  hillshade: "needs the tile service",
  slope: "needs the tile service",
  parcels: "load available land",
  village: "search for a village",
  explored: "click the sheet",
  pond: "no design was sized",
  streams: "run an analysis",
};

export function LegendBox({
  visibility,
  onChange,
  available,
  streamScope,
  onStreamScopeChange,
  loadingStreams,
  basemap,
  onBasemapChange,
}: Props) {
  const [collapsed, toggle] = useCollapsed("legend");

  return (
    <div className={collapsed ? "legendbox is-collapsed" : "legendbox"}>
      <div className="panel-head">
        <span className="stamp">Legend</span>
        <MinButton collapsed={collapsed} onToggle={toggle} label="the legend" />
      </div>
      <div className="items" hidden={collapsed}>
        {ORDER.map((key) => {
          const on = available[key] !== false;
          const k = KEY[key];
          return (
            <div key={key}>
              <label className={on ? "lg" : "lg dim"}>
                <input
                  type="checkbox"
                  checked={visibility[key] && on}
                  disabled={!on}
                  onChange={(e) => onChange({ ...visibility, [key]: e.target.checked })}
                />
                {k.kind === "fill" ? (
                  <span
                    className="keyf"
                    style={{ borderColor: k.colour, background: k.colour, opacity: 0.32 }}
                    aria-hidden="true"
                  />
                ) : (
                  <span
                    className="key"
                    style={{
                      borderColor: k.colour,
                      borderTopStyle: k.kind === "dash" ? "dashed" : "solid",
                    }}
                    aria-hidden="true"
                  />
                )}
                <span className="name">{t(`layers.${key}` as never)}</span>
              </label>
              {!on && WHY[key] && (
                <div
                  className="stamp"
                  style={{ fontSize: 9.5, letterSpacing: ".08em", margin: "0 0 4px 29px" }}
                >
                  {WHY[key]}
                </div>
              )}
              {/* Scope belongs under the layer it changes, not in a settings
                  drawer: the site catchment is what drainage density describes,
                  and the whole sheet is what you read the terrain with. */}
              {key === "streams" && on && (
                <div className="extent" role="group" aria-label="Drainage network extent">
                  {(["site", "sheet"] as StreamScope[]).map((scope) => (
                    <button
                      key={scope}
                      type="button"
                      className={scope === streamScope ? "on" : undefined}
                      aria-pressed={scope === streamScope}
                      disabled={loadingStreams}
                      onClick={() => onStreamScopeChange(scope)}
                    >
                      {scope === "site" ? "Site catchment" : "Whole sheet"}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {/* Basemap. Map chrome, so it belongs on the drawing with the legend
            rather than in the job sheet — and it has to be here somewhere:
            imagery shows what is already on the ground, the street map names the
            villages and roads that imagery cannot. */}
        <div
          className="stamp"
          style={{ display: "block", marginTop: 10, paddingTop: 9, borderTop: "1px solid var(--rule)" }}
        >
          Base
        </div>
        <div className="extent" style={{ marginLeft: 0, marginTop: 6 }} role="group" aria-label="Basemap">
          {(Object.keys(BASEMAPS) as BasemapId[]).map((id) => (
            <button
              key={id}
              type="button"
              className={id === basemap ? "on" : undefined}
              aria-pressed={id === basemap}
              onClick={() => onBasemapChange(id)}
            >
              {BASEMAPS[id].label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
