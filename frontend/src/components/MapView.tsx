import maplibregl, { type Map as MapLibreMap } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import type { Bbox, CandidateSite, ContourAnalysis, TerrainOverlay } from "../api/types";
import { num } from "../format";
import { AREA_MAX_KM2, AREA_MIN_KM2, bboxAreaKm2, bboxPolygon } from "../geo";
import { startAreaDraw } from "./draw/AreaDraw";

export interface LayerVisibility {
  hillshade: boolean;
  slope: boolean;
  parcels: boolean;
  pond: boolean;
  streams: boolean;
  explored: boolean;
  contours: boolean;
  catchment: boolean;
  sites: boolean;
  aoi: boolean;
  village: boolean;
  /** The rectangle drawn on the map, before and after it is run. */
  drawn: boolean;
}

/** A selected village's outline, and what that outline actually is.
 *
 *  `represents` is carried through to the map because the styling depends on it:
 *  a real village boundary is drawn solid, a containing sub-district dashed. A
 *  662 km² tehsil rendered like a village boundary would read as a claim the API
 *  explicitly refuses to make.
 */
export interface VillageOutline {
  id: string;
  name: string;
  geometry: GeoJSON.Geometry;
  represents: string | null;
  isVillageBoundary: boolean;
  focus: { lon: number; lat: number } | null;
}

interface Props {
  analysis: ContourAnalysis | null;
  visibility: LayerVisibility;
  selectedRank: number | null;
  basemap: BasemapId;
  village: VillageOutline | null;
  /** Slope and shaded relief as images, from GET /terrain/{dem_id}/overlays. */
  terrain: TerrainOverlay[];
  /** Drainage network from POST /hydrology/streams, if it was fetched. */
  streams: GeoJSON.FeatureCollection | null;
  /** A catchment the user delineated by clicking, distinct from the analysis'. */
  explored: { geometry: GeoJSON.Geometry; outlet: GeoJSON.Geometry } | null;
  /** Buildable parcels from POST /land/available, if they were fetched. */
  parcels: GeoJSON.FeatureCollection | null;
  /** Called with lon/lat when the user clicks bare map. Null disables it. */
  onDelineate: ((lon: number, lat: number) => void) | null;
  onSelectSite: (rank: number) => void;
  /** While true, dragging draws a rectangle instead of panning the map. */
  drawing: boolean;
  /** The rectangle to show as the selected area, or null. */
  drawnArea: Bbox | null;
  onAreaDrawn: (bbox: Bbox) => void;
  onDrawCancel: () => void;
  /** Changes when the map should frame `drawnArea` (the sample-area button). */
  frameAreaKey: number;
}

/**
 * Basemaps.
 *
 * Imagery is the default: siting a pond is largely a question of what is
 * already on the ground -- an existing tank, a field boundary, a settlement
 * edge -- and a road map shows none of it. The street map is kept as the
 * alternative because it names the villages and roads that imagery cannot.
 *
 * Both need the network. If neither loads the map still works: the analysis
 * overlays draw over the background colour. That is not a nicety --
 * `terrain_only` is explicitly an offline mode, and the answer must remain
 * legible when nothing external is reachable.
 */
export type BasemapId = "imagery" | "streets";

interface Basemap {
  id: BasemapId;
  label: string;
  tiles: string[];
  attribution: string;
  maxzoom: number;
  /** Contour lines need to stay readable over a busy photo. */
  overlayOpacity: number;
}

export const BASEMAPS: Record<BasemapId, Basemap> = {
  imagery: {
    id: "imagery",
    label: "Satellite",
    tiles: [
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    ],
    attribution: "Imagery © Esri, Maxar, Earthstar Geographics",
    maxzoom: 19,
    overlayOpacity: 1,
  },
  streets: {
    id: "streets",
    label: "Street map",
    tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
    attribution: "© OpenStreetMap contributors",
    maxzoom: 19,
    overlayOpacity: 0.85,
  },
};

function styleFor(basemap: Basemap): maplibregl.StyleSpecification {
  return {
    version: 8,
    sources: {
      basemap: {
        type: "raster",
        tiles: basemap.tiles,
        tileSize: 256,
        maxzoom: basemap.maxzoom,
        attribution: basemap.attribution,
      },
    },
    layers: [
      {
        id: "background",
        type: "background",
        paint: { "background-color": "#0f1720" },
      },
      {
        id: "basemap",
        type: "raster",
        source: "basemap",
        paint: { "raster-opacity": basemap.overlayOpacity },
      },
    ],
  };
}

const EMPTY: GeoJSON.FeatureCollection = {
  type: "FeatureCollection",
  features: [],
};

/** The raster products the map can show, in draw order. */
const TERRAIN_PRODUCTS = ["hillshade", "slope"] as const;

/** The lon/lat extent of a geometry, or null if it holds no coordinates.
 *
 *  Walks the nested coordinate arrays rather than depending on a geometry
 *  library: this is the only place the app needs an extent, and pulling in Turf
 *  for one bounding box would add more than the whole app bundle.
 */
function boundsOf(
  geometry: GeoJSON.Geometry,
): [[number, number], [number, number]] | null {
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;

  const visit = (node: unknown): void => {
    if (!Array.isArray(node)) return;
    if (typeof node[0] === "number" && typeof node[1] === "number") {
      const [lon, lat] = node as [number, number];
      if (lon < west) west = lon;
      if (lon > east) east = lon;
      if (lat < south) south = lat;
      if (lat > north) north = lat;
      return;
    }
    for (const child of node) visit(child);
  };

  if (!("coordinates" in geometry)) return null;
  visit(geometry.coordinates);
  if (!Number.isFinite(west) || !Number.isFinite(south)) return null;
  return [
    [west, south],
    [east, north],
  ];
}

type LngLatBox = [[number, number], [number, number]];

function unionBounds(a: LngLatBox | null, b: LngLatBox | null): LngLatBox | null {
  if (!a || !b) return a ?? b;
  return [
    [Math.min(a[0][0], b[0][0]), Math.min(a[0][1], b[0][1])],
    [Math.max(a[1][0], b[1][0]), Math.max(a[1][1], b[1][1])],
  ];
}

function sitesToGeoJSON(
  analysis: ContourAnalysis | null,
): GeoJSON.FeatureCollection {
  if (!analysis) return EMPTY;
  return {
    type: "FeatureCollection",
    features: analysis.candidate_sites.map((site) => ({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [site.location.lon, site.location.lat],
      },
      properties: {
        rank: site.rank,
        score: site.suitability_score,
        kind: site.site_kind,
        label: `#${site.rank}`,
      },
    })),
  };
}

function catchmentToGeoJSON(
  analysis: ContourAnalysis | null,
  rank: number | null,
): GeoJSON.FeatureCollection {
  const site = analysis?.candidate_sites.find((s) => s.rank === rank);
  const geometry = site?.catchment.geometry;
  if (!geometry) return EMPTY;
  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        geometry,
        properties: { area_ha: site.catchment.metrics.area_ha },
      },
    ],
  };
}

/**
 * The recommended pond as an indicative rectangle on the ground.
 *
 * The design fixes plan dimensions and depth; it does not fix an orientation,
 * because nothing in the model chooses one. So this draws a north-aligned
 * rectangle of the right size centred on the site -- honest about scale and
 * position, and drawn dashed on the map so it does not read as a surveyed
 * outline. Degrees per metre are taken at the site's own latitude, which
 * matters: using a global constant stretches the box measurably at 21 N.
 */
function pondFootprint(
  analysis: ContourAnalysis | null,
  selectedRank: number | null,
): GeoJSON.FeatureCollection {
  const site =
    analysis?.candidate_sites.find((s) => s.rank === selectedRank) ??
    analysis?.recommended_site ??
    null;
  const design = site?.pond?.available ? site.pond.recommended : undefined;
  if (!site || !design) return EMPTY;

  const { lon, lat } = site.location;
  const halfLen = design.top_length_m / 2;
  const halfWid = design.top_width_m / 2;
  const dLat = halfWid / 111_320;
  const dLon = halfLen / (111_320 * Math.cos((lat * Math.PI) / 180));

  return {
    type: "FeatureCollection",
    features: [
      {
        type: "Feature",
        properties: { rank: site.rank, depth_m: design.depth_m },
        geometry: {
          type: "Polygon",
          coordinates: [
            [
              [lon - dLon, lat - dLat],
              [lon + dLon, lat - dLat],
              [lon + dLon, lat + dLat],
              [lon - dLon, lat + dLat],
              [lon - dLon, lat - dLat],
            ],
          ],
        },
      },
    ],
  };
}

/** What a site's pond would collect in a year, as its marker label shows it. */
function siteLabel(site: CandidateSite): string {
  const m3 = site.expected_water?.volume_m3;
  return m3 != null ? `#${site.rank} · ${num(m3)} m³` : `#${site.rank}`;
}

/** Where to put a label inside a catchment: the area-weighted centroid of its
 *  largest ring. Good enough for the blob-like shapes catchments are; a
 *  crescent would put it just outside, which still reads unambiguously. */
function labelPoint(geometry: GeoJSON.Geometry): [number, number] | null {
  const rings: GeoJSON.Position[][] = [];
  if (geometry.type === "Polygon" && geometry.coordinates[0]) {
    rings.push(geometry.coordinates[0]);
  } else if (geometry.type === "MultiPolygon") {
    for (const poly of geometry.coordinates) if (poly[0]) rings.push(poly[0]);
  }
  let best: { a: number; x: number; y: number } | null = null;
  for (const ring of rings) {
    let a = 0;
    let cx = 0;
    let cy = 0;
    for (let i = 0; i < ring.length - 1; i++) {
      const [x0 = 0, y0 = 0] = ring[i] ?? [];
      const [x1 = 0, y1 = 0] = ring[i + 1] ?? [];
      const cross = x0 * y1 - x1 * y0;
      a += cross;
      cx += (x0 + x1) * cross;
      cy += (y0 + y1) * cross;
    }
    if (a === 0) continue;
    const entry = { a: Math.abs(a), x: cx / (3 * a), y: cy / (3 * a) };
    if (!best || entry.a > best.a) best = entry;
  }
  return best ? [best.x, best.y] : null;
}

/** Padding that keeps a framed area clear of what floats over the map: the
 *  legend down the left, the zoom buttons top right, the title block bottom
 *  right. Measured, because the legend collapses and its width is the user's
 *  choice -- a fixed 56 px framed the recommended site underneath it. */
function framePadding(mapEl: HTMLElement | null): maplibregl.PaddingOptions {
  const padding = { top: 56, right: 64, bottom: 76, left: 56 };
  const box = mapEl?.getBoundingClientRect();
  const legend = mapEl?.parentElement
    ?.querySelector(".legendbox:not(.is-collapsed)")
    ?.getBoundingClientRect();
  if (box && legend && legend.width > 0) {
    // Never more than half the map: on a narrow screen the area still gets room.
    padding.left = Math.min(Math.max(padding.left, legend.right - box.left + 28), box.width * 0.5);
  }
  return padding;
}

/** The popup for one site: the three results the brief asks for, in one place.
 *  Built as DOM with `textContent`, so nothing in a response is ever parsed as
 *  markup. */
function sitePopup(site: CandidateSite): HTMLElement {
  const root = document.createElement("div");
  const title = document.createElement("div");
  title.className = "sp-title";
  title.textContent = `Site #${site.rank} · ${num(site.suitability_score, 1)}/100`;
  root.append(title);

  const rows: [string, string, string?][] = [];
  const { lat, lon } = site.location;
  rows.push(["Pond location", `${lat.toFixed(5)}, ${lon.toFixed(5)}`, "latitude, longitude"]);
  const m = site.catchment.metrics;
  rows.push(["Catchment", `${num(m.area_ha, 1)} ha`, `${num(m.area_km2, 2)} km² drains here`]);
  const water = site.expected_water;
  if (water?.volume_m3 != null) {
    rows.push([
      "Collects",
      `${num(water.volume_m3)} m³ a year`,
      water.limited_by === "inflow"
        ? "limited by the inflow in a dry year"
        : "limited by the pond's live storage",
    ]);
  } else {
    rows.push(["Collects", "—", water?.basis ?? "no pond was sized here"]);
  }
  const inflow = water?.annual_inflow_m3;
  if (inflow?.mean != null && inflow.dependable_75_percent != null) {
    rows.push([
      "Inflow",
      `${num(inflow.mean)} m³ mean`,
      `${num(inflow.dependable_75_percent)} m³ in three years of four`,
    ]);
  } else {
    rows.push(["Inflow", "—", "rainfall unavailable on this run"]);
  }

  const table = document.createElement("dl");
  for (const [label, value, note] of rows) {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    if (note) {
      const small = document.createElement("small");
      small.textContent = note;
      dd.append(small);
    }
    table.append(dt, dd);
  }
  root.append(table);
  return root;
}

export function MapView({
  analysis,
  visibility,
  selectedRank,
  basemap,
  village,
  terrain,
  streams,
  explored,
  parcels,
  onDelineate,
  onSelectSite,
  drawing,
  drawnArea,
  onAreaDrawn,
  onDrawCancel,
  frameAreaKey,
}: Props) {
  const container = useRef<HTMLDivElement | null>(null);
  const map = useRef<MapLibreMap | null>(null);
  const ready = useRef(false);
  const onSelect = useRef(onSelectSite);
  onSelect.current = onSelectSite;
  const onExplore = useRef(onDelineate);
  onExplore.current = onDelineate;
  const rankMarkers = useRef<maplibregl.Marker[]>([]);
  const catchmentLabel = useRef<maplibregl.Marker | null>(null);
  const popup = useRef<maplibregl.Popup | null>(null);
  const readout = useRef<HTMLDivElement | null>(null);
  // Layer handlers are registered once, on load; they read the current analysis
  // and callbacks through refs rather than the values captured then.
  const analysisRef = useRef(analysis);
  analysisRef.current = analysis;
  const onDrawn = useRef(onAreaDrawn);
  onDrawn.current = onAreaDrawn;
  const onCancelDraw = useRef(onDrawCancel);
  onCancelDraw.current = onDrawCancel;
  const drawnRef = useRef(drawnArea);
  drawnRef.current = drawnArea;
  const [mapReady, setMapReady] = useState(false);
  const visibilityRef = useRef(visibility);
  visibilityRef.current = visibility;
  // The map is created once; the initial basemap must not re-run that effect.
  const initialBasemap = useRef(basemap);

  useEffect(() => {
    if (!container.current || map.current) return;
    const instance = new maplibregl.Map({
      container: container.current,
      style: styleFor(BASEMAPS[initialBasemap.current]),
      center: [78.9, 21.5],
      zoom: 3.5,
      attributionControl: { compact: true },
    });
    // MapLibre reports style and tile problems on its own `error` event and
    // nowhere else -- an invalid paint property or a 404 tile is otherwise
    // completely silent. Forwarding to console.error makes them visible in
    // development and, more usefully, makes the end-to-end test's
    // "nothing throws in the console" assertion cover the map as well as React.
    instance.on("error", (event) => {
      const detail = event.error?.message ?? String(event.error ?? "unknown");
      console.error(`maplibre: ${detail}`);
    });

    instance.addControl(
      new maplibregl.NavigationControl({ showCompass: false }),
      "top-right",
    );
    instance.addControl(
      new maplibregl.ScaleControl({ unit: "metric" }),
      "bottom-left",
    );

    instance.on("load", () => {
      instance.addSource("aoi", { type: "geojson", data: EMPTY });
      instance.addSource("contours", { type: "geojson", data: EMPTY });
      instance.addSource("catchment", { type: "geojson", data: EMPTY });
      instance.addSource("sites", {
        type: "geojson",
        data: sitesToGeoJSON(null),
      });
      instance.addSource("village", { type: "geojson", data: EMPTY });
      instance.addSource("streams", { type: "geojson", data: EMPTY });
      instance.addSource("explored", { type: "geojson", data: EMPTY });
      instance.addSource("explored-outlet", { type: "geojson", data: EMPTY });
      instance.addSource("parcels", { type: "geojson", data: EMPTY });
      instance.addSource("pond", { type: "geojson", data: EMPTY });
      instance.addSource("drawn-area", { type: "geojson", data: EMPTY });

      instance.addLayer({
        id: "village-fill",
        type: "fill",
        source: "village",
        paint: { "fill-color": "#34d399", "fill-opacity": 0.07 },
      });
      // Two line layers rather than one with a data-driven dash, because
      // `line-dasharray` is a cross-faded property whose expressions accept only
      // `zoom` -- a `["case", ["get", ...]]` there is invalid, and the throw
      // takes every layer added after it with it. `filter` *is* data-driven, so
      // the distinction moves there.
      instance.addLayer({
        id: "village-line-exact",
        type: "line",
        source: "village",
        filter: ["==", ["get", "is_village_boundary"], true],
        paint: { "line-color": "#34d399", "line-width": 2.5 },
      });
      instance.addLayer({
        id: "village-line-approx",
        type: "line",
        source: "village",
        filter: ["!=", ["get", "is_village_boundary"], true],
        // Dashed says "this is the area around the village", which is what the
        // API actually returned. A solid line would read as a claim it refuses
        // to make.
        paint: {
          "line-color": "#34d399",
          "line-width": 2,
          "line-dasharray": [3, 2],
        },
      });
      // Below the contours so the channels read as terrain rather than
      // annotation, and above the catchment fill so they stay visible inside it.
      // A user-delineated catchment, styled apart from the analysis' own so the
      // two are never confused: amber-green, dashed, and drawn above.
      instance.addLayer({
        id: "explored-fill",
        type: "fill",
        source: "explored",
        paint: { "fill-color": "#a3e635", "fill-opacity": 0.1 },
      });
      instance.addLayer({
        id: "explored-line",
        type: "line",
        source: "explored",
        paint: {
          "line-color": "#a3e635",
          "line-width": 2,
          "line-dasharray": [4, 2],
        },
      });
      instance.addLayer({
        id: "explored-outlet",
        type: "circle",
        source: "explored-outlet",
        paint: {
          "circle-radius": 5,
          "circle-color": "#a3e635",
          "circle-stroke-color": "#111827",
          "circle-stroke-width": 2,
        },
      });
      instance.addLayer({
        id: "parcel-fill",
        type: "fill",
        source: "parcels",
        // Green reads as "available" without competing with the cyan of the
        // hydrology layers or the amber of the site markers.
        paint: { "fill-color": "#4ade80", "fill-opacity": 0.18 },
      });
      instance.addLayer({
        id: "parcel-line",
        type: "line",
        source: "parcels",
        paint: {
          "line-color": "#4ade80",
          "line-width": 0.8,
          "line-opacity": 0.7,
        },
      });
      instance.addLayer({
        id: "stream-line",
        type: "line",
        source: "streams",
        paint: {
          "line-color": "#38bdf8",
          // Width from Strahler order, which is the reason for computing it: a
          // first-order headwater and a fourth-order trunk drawn identically
          // tell the reader nothing about which one a pond can span.
          "line-width": [
            "interpolate",
            ["linear"],
            ["get", "strahler_order"],
            1,
            0.8,
            5,
            3.6,
          ],
          "line-opacity": 0.9,
        },
      });
      instance.addLayer({
        id: "aoi-line",
        type: "line",
        source: "aoi",
        paint: {
          "line-color": "#7dd3fc",
          "line-width": 1,
          "line-dasharray": [3, 3],
        },
      });
      instance.addLayer({
        id: "contour-line",
        type: "line",
        source: "contours",
        paint: {
          "line-color": "#a78bfa",
          "line-width": 0.6,
          "line-opacity": 0.7,
        },
      });
      instance.addLayer({
        id: "catchment-fill",
        type: "fill",
        source: "catchment",
        paint: { "fill-color": "#22d3ee", "fill-opacity": 0.22 },
      });
      instance.addLayer({
        id: "catchment-line",
        type: "line",
        source: "catchment",
        paint: { "line-color": "#22d3ee", "line-width": 2 },
      });
      // The selected area: white with a dark casing, the convention for "your
      // selection", legible over both the photo and the street map. Above the
      // catchment so it is never lost inside one, below the sites.
      instance.addLayer({
        id: "drawn-area-fill",
        type: "fill",
        source: "drawn-area",
        paint: { "fill-color": "#ffffff", "fill-opacity": 0.06 },
      });
      instance.addLayer({
        id: "drawn-area-casing",
        type: "line",
        source: "drawn-area",
        paint: { "line-color": "#0f1720", "line-width": 4, "line-opacity": 0.55 },
      });
      instance.addLayer({
        id: "drawn-area-line",
        type: "line",
        source: "drawn-area",
        paint: { "line-color": "#ffffff", "line-width": 2 },
      });
      instance.addLayer({
        id: "pond-fill",
        type: "fill",
        source: "pond",
        paint: { "fill-color": "#f59e0b", "fill-opacity": 0.25 },
      });
      instance.addLayer({
        id: "pond-line",
        type: "line",
        source: "pond",
        // Dashed, because the outline is indicative: the design fixes the plan
        // dimensions but nothing fixes the orientation, so a solid rectangle
        // would claim a bearing the model never computed.
        paint: {
          "line-color": "#f59e0b",
          "line-width": 1.6,
          "line-dasharray": [3, 2],
        },
      });
      instance.addLayer({
        id: "site-halo",
        type: "circle",
        source: "sites",
        paint: {
          "circle-radius": 13,
          "circle-color": "#f59e0b",
          "circle-opacity": 0.18,
        },
      });
      instance.addLayer({
        id: "site-point",
        type: "circle",
        source: "sites",
        paint: {
          "circle-radius": 7,
          // Rank 1 is emphasised; the rest are visibly secondary.
          "circle-color": [
            "case",
            ["==", ["get", "rank"], 1],
            "#f59e0b",
            "#fbbf24",
          ],
          "circle-stroke-color": "#111827",
          "circle-stroke-width": 2,
        },
      });

      // Bare-map clicks delineate. Registered before the site handler so a
      // click on a marker does not also start an exploration -- MapLibre calls
      // layer handlers first, and `defaultPrevented` is how they say "handled".
      instance.on("click", (event) => {
        if (!onExplore.current || event.originalEvent.defaultPrevented) return;
        const hits = instance.queryRenderedFeatures(event.point, {
          layers: ["site-point", "site-halo"].filter((id) =>
            instance.getLayer(id),
          ),
        });
        if (hits.length) return;
        onExplore.current(event.lngLat.lng, event.lngLat.lat);
      });

      instance.on("click", "site-point", (event) => {
        const rank = event.features?.[0]?.properties?.["rank"];
        if (typeof rank !== "number") return;
        onSelect.current(rank);
        const site = analysisRef.current?.candidate_sites.find((s) => s.rank === rank);
        if (site) openPopup(instance, site);
      });
      for (const id of ["site-point", "site-halo"]) {
        instance.on("mouseenter", id, () => {
          instance.getCanvas().style.cursor = "pointer";
        });
        instance.on("mouseleave", id, () => {
          instance.getCanvas().style.cursor = "";
        });
      }
      ready.current = true;
      setMapReady(true);
      // Something a test (or a person inspecting the page) can wait on: the
      // canvas exists well before the map will accept overlays or drags.
      container.current?.setAttribute("data-ready", "true");
    });

    // The live size of the rectangle being drawn, beside the cursor. DOM rather
    // than React state: it changes on every pointer move, and re-rendering the
    // workspace sixty times a second to move one label would be the wrong trade.
    const label = document.createElement("div");
    label.className = "draw-readout";
    label.hidden = true;
    container.current.append(label);
    readout.current = label;

    map.current = instance;
    return () => {
      instance.remove();
      label.remove();
      rankMarkers.current = [];
      catchmentLabel.current = null;
      popup.current = null;
      map.current = null;
      ready.current = false;
    };
  }, []);

  function openPopup(instance: MapLibreMap, site: CandidateSite) {
    popup.current?.remove();
    popup.current = new maplibregl.Popup({
      className: "site-popup",
      offset: 16,
      maxWidth: "290px",
      focusAfterOpen: false,
    })
      .setLngLat([site.location.lon, site.location.lat])
      .setDOMContent(sitePopup(site))
      .addTo(instance);
  }

  // Draw mode: drags draw a rectangle; the live outline and its size follow the
  // pointer; release hands the rectangle up. Escape restores what was there.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !mapReady || !drawing) return;
    const source = () => instance.getSource("drawn-area") as maplibregl.GeoJSONSource | undefined;
    const show = (bbox: Bbox | null) =>
      source()?.setData(
        bbox
          ? { type: "FeatureCollection", features: [{ type: "Feature", geometry: bboxPolygon(bbox), properties: {} }] }
          : EMPTY,
      );
    const label = readout.current;
    const stop = startAreaDraw(instance, {
      onChange: (bbox, at) => {
        show(bbox);
        if (!label) return;
        const km2 = bboxAreaKm2(bbox);
        const bad = km2 > AREA_MAX_KM2 || km2 < AREA_MIN_KM2;
        label.hidden = false;
        label.classList.toggle("is-bad", bad);
        label.textContent =
          km2 > AREA_MAX_KM2
            ? `${num(km2, 1)} km² · over ${AREA_MAX_KM2} km²`
            : km2 < AREA_MIN_KM2
              ? `${num(km2, 3)} km² · under ${AREA_MIN_KM2} km²`
              : `${num(km2, km2 < 10 ? 2 : 1)} km²`;
        label.style.transform = `translate(${at.x + 14}px, ${at.y + 14}px)`;
      },
      onComplete: (bbox) => {
        if (label) label.hidden = true;
        onDrawn.current(bbox);
      },
      onCancel: () => {
        if (label) label.hidden = true;
        show(drawnRef.current);
        onCancelDraw.current();
      },
    });
    return () => {
      stop();
      if (label) label.hidden = true;
    };
  }, [drawing, mapReady]);

  // The selected area, whenever it changes from outside a drag.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !mapReady) return;
    const source = instance.getSource("drawn-area") as maplibregl.GeoJSONSource | undefined;
    source?.setData(
      drawnArea
        ? {
            type: "FeatureCollection",
            features: [{ type: "Feature", geometry: bboxPolygon(drawnArea), properties: {} }],
          }
        : EMPTY,
    );
  }, [drawnArea, mapReady]);

  // Frame the selected area when asked to -- not on every redraw, which would
  // yank the view out from under someone adjusting their rectangle.
  useEffect(() => {
    const instance = map.current;
    const area = drawnRef.current;
    if (!instance || !mapReady || frameAreaKey === 0 || !area) return;
    instance.fitBounds(
      [
        [area[0], area[1]],
        [area[2], area[3]],
      ],
      { padding: framePadding(container.current), duration: 900 },
    );
  }, [frameAreaKey, mapReady]);

  // Push new analysis data onto the map, and frame it.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current || !analysis) return;

    const setData = (id: string, data: GeoJSON.FeatureCollection) => {
      const source = instance.getSource(id) as
        maplibregl.GeoJSONSource | undefined;
      source?.setData(data);
    };

    setData("aoi", {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: analysis.area_of_interest,
          properties: {},
        },
      ],
    });
    setData("contours", analysis.contours ?? EMPTY);
    setData("sites", sitesToGeoJSON(analysis));

    // Rank labels are DOM markers, not a symbol layer. A MapLibre `text-field`
    // needs a `glyphs` endpoint, which would put the one label telling you
    // which site is which behind the same network that the basemap needs --
    // and `terrain_only` exists precisely for when that network is absent.
    for (const marker of rankMarkers.current) marker.remove();
    popup.current?.remove();
    rankMarkers.current = analysis.candidate_sites.map((site) => {
      const element = document.createElement("button");
      element.className = "site-rank";
      element.type = "button";
      // The selected site's marker carries the water its pond collects (see the
      // selection effect); every marker's tooltip and popup carry its own.
      // Written on all five, the long labels collide on sites 200 m apart.
      element.textContent = `#${site.rank}`;
      const m3 = site.expected_water?.volume_m3;
      element.title =
        `Site #${site.rank} — ${site.suitability_score}/100` +
        (m3 != null ? ` · collects ${num(m3)} m³ a year` : "");
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        onSelect.current(site.rank);
        openPopup(instance, site);
      });
      return new maplibregl.Marker({ element, offset: [0, -20] })
        .setLngLat([site.location.lon, site.location.lat])
        .addTo(instance);
    });

    // Framed on the area of interest, which both inputs carry -- the sheet's
    // extent for an upload, the drawn rectangle for an area -- widened to take
    // in the recommended catchment, which on a drawn area reaches into the
    // margin beyond the rectangle.
    const catchment = analysis.recommended_site?.catchment.geometry;
    const extent = unionBounds(
      boundsOf(analysis.area_of_interest),
      catchment ? boundsOf(catchment) : null,
    );
    if (extent) {
      instance.fitBounds(extent, { padding: framePadding(container.current), duration: 900 });
    }
  }, [analysis]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;
    const source = instance.getSource("catchment") as
      maplibregl.GeoJSONSource | undefined;
    source?.setData(catchmentToGeoJSON(analysis, selectedRank));

    // The drawn catchment belongs to one site; say which, on the map itself.
    const ranks = analysis?.candidate_sites ?? [];
    rankMarkers.current.forEach((marker, index) => {
      const site = ranks[index];
      const isSelected = site?.rank === selectedRank;
      const element = marker.getElement();
      element.classList.toggle("site-rank--selected", isSelected);
      element.setAttribute("aria-pressed", String(isSelected));
      if (site) element.textContent = isSelected ? siteLabel(site) : `#${site.rank}`;
      // Above its neighbours, so the one label with a number is never covered.
      element.style.zIndex = isSelected ? "2" : "";
    });

    // The catchment's area, written inside it.
    catchmentLabel.current?.remove();
    catchmentLabel.current = null;
    const chosen = analysis?.candidate_sites.find((s) => s.rank === selectedRank);
    const at = chosen?.catchment.geometry ? labelPoint(chosen.catchment.geometry) : null;
    if (chosen && at) {
      const element = document.createElement("div");
      element.className = "catchment-label";
      const ha = chosen.catchment.metrics.area_ha;
      element.textContent = `${num(ha, ha < 10 ? 1 : 0)} ha catchment`;
      element.style.display = visibilityRef.current.catchment ? "" : "none";
      catchmentLabel.current = new maplibregl.Marker({ element }).setLngLat(at).addTo(instance);
    }
  }, [analysis, selectedRank]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;
    const source = instance.getSource("streams") as
      maplibregl.GeoJSONSource | undefined;
    source?.setData(streams ?? EMPTY);
  }, [streams]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;
    const source = instance.getSource("parcels") as
      maplibregl.GeoJSONSource | undefined;
    source?.setData(parcels ?? EMPTY);
  }, [parcels]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;
    const source = instance.getSource("pond") as
      maplibregl.GeoJSONSource | undefined;
    source?.setData(pondFootprint(analysis, selectedRank));
  }, [analysis, selectedRank]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;
    const outline = instance.getSource("explored") as
      maplibregl.GeoJSONSource | undefined;
    const outlet = instance.getSource("explored-outlet") as
      maplibregl.GeoJSONSource | undefined;
    if (!explored) {
      outline?.setData(EMPTY);
      outlet?.setData(EMPTY);
      return;
    }
    outline?.setData({
      type: "FeatureCollection",
      features: [
        { type: "Feature", geometry: explored.geometry, properties: {} },
      ],
    });
    outlet?.setData({
      type: "FeatureCollection",
      features: [
        { type: "Feature", geometry: explored.outlet, properties: {} },
      ],
    });
  }, [explored]);

  // Slope and shaded relief: one image each, pinned to the grid's corners.
  //
  // Images rather than TiTiler tiles, so the layers work on a deployment that
  // runs nothing but the API -- with tiles they could be switched on and drew
  // nothing there. A new run brings new URLs, and the existing source is
  // repointed rather than rebuilt. Inserted below the first vector overlay so
  // the shading sits under the contours and the catchment rather than over them.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;

    const wanted = new Map(terrain.map((layer) => [layer.product, layer]));
    for (const product of TERRAIN_PRODUCTS) {
      const id = `terrain-${product}`;
      const layer = wanted.get(product);
      const source = instance.getSource(id) as maplibregl.ImageSource | undefined;
      if (!layer) {
        if (instance.getLayer(id)) instance.removeLayer(id);
        if (source) instance.removeSource(id);
        continue;
      }
      if (source) {
        source.updateImage({ url: layer.url, coordinates: layer.coordinates });
        continue;
      }
      instance.addSource(id, { type: "image", url: layer.url, coordinates: layer.coordinates });
      instance.addLayer(
        {
          id,
          type: "raster",
          source: id,
          paint: {
            // Hillshade is a texture the imagery should still show through;
            // slope is a measurement and wants to be readable on its own.
            "raster-opacity": product === "hillshade" ? 0.55 : 0.7,
            // Nearest, not linear: at 30 m a cell is a real measurement, and
            // smoothing between cells would draw detail that is not there.
            "raster-resampling": "nearest",
          },
        },
        instance.getLayer("village-fill") ? "village-fill" : undefined,
      );
    }
  }, [terrain]);

  // Draw the selected village and frame the map on it.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;
    const source = instance.getSource("village") as
      maplibregl.GeoJSONSource | undefined;
    if (!source) return;

    if (!village) {
      source.setData(EMPTY);
      return;
    }

    source.setData({
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: village.geometry,
          properties: {
            name: village.name,
            represents: village.represents,
            is_village_boundary: village.isVillageBoundary,
          },
        },
      ],
    });

    // Fit to the outline rather than flying to the focus point: the point is a
    // sub-district centroid, so a fixed zoom around it would frame either a
    // sliver or half the state depending on the tehsil's size.
    const bounds = boundsOf(village.geometry);
    if (bounds) {
      instance.fitBounds(bounds, { padding: 48, duration: 900, maxZoom: 14 });
    } else if (village.focus) {
      instance.flyTo({
        center: [village.focus.lon, village.focus.lat],
        zoom: 11,
      });
    }
  }, [village]);

  // Swap the basemap by replacing its source and layer rather than calling
  // setStyle, which would discard every overlay source we added on load. The
  // replacement is re-inserted beneath the first overlay so the draw order --
  // photo, then survey extent, then contours, then catchment, then sites --
  // survives the switch. Re-adding the source (rather than just retargeting its
  // tiles) is what carries the new attribution into MapLibre's control, which
  // both Esri and OpenStreetMap require to be displayed.
  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;
    const next = BASEMAPS[basemap];
    if (instance.getLayer("basemap")) instance.removeLayer("basemap");
    if (instance.getSource("basemap")) instance.removeSource("basemap");
    instance.addSource("basemap", {
      type: "raster",
      tiles: next.tiles,
      tileSize: 256,
      maxzoom: next.maxzoom,
      attribution: next.attribution,
    });
    instance.addLayer(
      {
        id: "basemap",
        type: "raster",
        source: "basemap",
        paint: { "raster-opacity": next.overlayOpacity },
      },
      "aoi-line",
    );
  }, [basemap]);

  useEffect(() => {
    const instance = map.current;
    if (!instance || !ready.current) return;
    const show = (layer: string, visible: boolean) =>
      instance.setLayoutProperty(
        layer,
        "visibility",
        visible ? "visible" : "none",
      );
    show("aoi-line", visibility.aoi);
    show("contour-line", visibility.contours);
    show("catchment-fill", visibility.catchment);
    show("catchment-line", visibility.catchment);
    for (const id of ["site-halo", "site-point"]) show(id, visibility.sites);
    for (const id of [
      "village-fill",
      "village-line-exact",
      "village-line-approx",
    ]) {
      show(id, visibility.village);
    }
    show("stream-line", visibility.streams);
    for (const id of ["parcel-fill", "parcel-line"])
      show(id, visibility.parcels);
    for (const id of ["pond-fill", "pond-line"]) show(id, visibility.pond);
    for (const id of ["explored-fill", "explored-line", "explored-outlet"]) {
      show(id, visibility.explored);
    }
    for (const product of TERRAIN_PRODUCTS) {
      const id = `terrain-${product}`;
      if (instance.getLayer(id)) show(id, visibility[product]);
    }
    // `terrain` is in this effect's dependencies for a reason: the terrain
    // layers are added by a *later* effect, so without it a newly added layer
    // keeps MapLibre's default of visible and ignores its own unchecked toggle.
    // Slope rendered over the whole map with its box cleared.
    for (const marker of rankMarkers.current) {
      marker.getElement().style.display = visibility.sites ? "" : "none";
    }
    for (const id of ["drawn-area-fill", "drawn-area-casing", "drawn-area-line"]) {
      show(id, visibility.drawn);
    }
    const label = catchmentLabel.current?.getElement();
    if (label) label.style.display = visibility.catchment ? "" : "none";
  }, [visibility, analysis, terrain]);

  return (
    <div
      ref={container}
      className="map"
      role="application"
      aria-label="Analysis map"
    />
  );
}
