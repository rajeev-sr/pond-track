import { BASEMAPS, type BasemapId, type LayerVisibility } from "./MapView";
import type { StreamScope } from "../api/types";
import { t } from "../i18n";

interface Props {
  visibility: LayerVisibility;
  onChange: (visibility: LayerVisibility) => void;
  basemap: BasemapId;
  onBasemapChange: (basemap: BasemapId) => void;
  hasContours: boolean;
  hasVillage: boolean;
  hasTerrain: boolean;
  hasStreams: boolean;
  hasExplored: boolean;
  hasParcels: boolean;
  hasPond: boolean;
  streamScope: StreamScope;
  onStreamScopeChange: (scope: StreamScope) => void;
  loadingStreams: boolean;
}

const SWATCH: Record<keyof LayerVisibility, string> = {
  hillshade: "#9ca3af",
  slope: "#e879a2",
  streams: "#38bdf8",
  explored: "#a3e635",
  parcels: "#4ade80",
  pond: "#f59e0b",
  contours: "#a78bfa",
  catchment: "#22d3ee",
  sites: "#f59e0b",
  aoi: "#7dd3fc",
  village: "#34d399",
};

const LABELS: Record<keyof LayerVisibility, string> = {
  hillshade: "layers.hillshade",
  slope: "layers.slope",
  streams: "layers.streams",
  explored: "layers.explored",
  parcels: "layers.parcels",
  pond: "layers.pond",
  contours: "layers.contours",
  catchment: "layers.catchment",
  sites: "layers.sites",
  aoi: "layers.aoi",
  village: "layers.village",
};

/** Why a layer is unavailable — a disabled toggle with no reason is a dead end. */
const UNAVAILABLE_HINT: Partial<Record<keyof LayerVisibility, string>> = {
  contours: " — enable “include contours”",
  village: " — search for a village",
  hillshade: " — needs the tiles service",
  slope: " — needs the tiles service",
  streams: " — analyse a contour map first",
  explored: " — click the map to delineate",
  parcels: " — load available land",
  pond: " — no pond was sized",
};

export function LayerPanel({
  visibility,
  onChange,
  basemap,
  onBasemapChange,
  hasContours,
  hasVillage,
  hasTerrain,
  hasStreams,
  hasExplored,
  hasParcels,
  hasPond,
  streamScope,
  onStreamScopeChange,
  loadingStreams,
}: Props) {
  const keys = Object.keys(LABELS) as (keyof LayerVisibility)[];
  return (
    <section className="panel" aria-labelledby="layers-heading">
      <h2 id="layers-heading">{t("layers.heading")}</h2>
      <ul className="layers">
        {keys.map((key) => {
          const unavailable =
            (key === "contours" && !hasContours) ||
            (key === "village" && !hasVillage) ||
            ((key === "hillshade" || key === "slope") && !hasTerrain) ||
            (key === "streams" && !hasStreams) ||
            (key === "explored" && !hasExplored) ||
            (key === "parcels" && !hasParcels) ||
            (key === "pond" && !hasPond);
          return (
            <li key={key}>
              <label className={unavailable ? "muted" : undefined}>
                <input
                  type="checkbox"
                  checked={visibility[key] && !unavailable}
                  disabled={unavailable}
                  onChange={(e) => onChange({ ...visibility, [key]: e.target.checked })}
                />
                {/* The swatch carries a text label too: colour alone must never
                    be the only channel (WCAG 1.4.1). */}
                <span className="swatch" style={{ background: SWATCH[key] }} aria-hidden="true" />
                {t(LABELS[key] as never)}
              </label>
              {unavailable && (
                <span className="small muted">
                  {UNAVAILABLE_HINT[key] ?? ""}
                </span>
              )}
              {/* Scope, not visibility, so it sits under the layer it changes
                  rather than in the options drawer. Both networks are genuinely
                  useful: the site catchment is what the drainage *density*
                  describes, and the whole sheet is what you read the terrain
                  with -- see `StreamScope`. */}
              {key === "streams" && !unavailable && (
                <div className="scope" role="group" aria-label={t("layers.streamsScope")}>
                  {(["site", "sheet"] as StreamScope[]).map((scope) => (
                    <button
                      key={scope}
                      type="button"
                      className={scope === streamScope ? "scope-chip active" : "scope-chip"}
                      aria-pressed={scope === streamScope}
                      disabled={loadingStreams}
                      onClick={() => onStreamScopeChange(scope)}
                    >
                      {t(
                        scope === "site"
                          ? "layers.streamsScope.site"
                          : "layers.streamsScope.sheet",
                      )}
                    </button>
                  ))}
                  {loadingStreams && (
                    <span className="small muted">{t("layers.streamsScope.loading")}</span>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {/* A radio group, not a checkbox list: the basemaps are alternatives, and
          the roles have to say so for anyone navigating by keyboard. */}
      <fieldset className="basemaps">
        <legend>{t("layers.basemap")}</legend>
        {(Object.keys(BASEMAPS) as BasemapId[]).map((id) => (
          <label key={id}>
            <input
              type="radio"
              name="basemap"
              value={id}
              checked={basemap === id}
              onChange={() => onBasemapChange(id)}
            />
            {BASEMAPS[id].label}
          </label>
        ))}
      </fieldset>
    </section>
  );
}
