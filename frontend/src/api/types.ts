/**
 * Response shapes for the contour endpoints.
 *
 * Hand-written rather than generated, and deliberately partial: only the fields
 * the UI actually reads are typed. A generated full-surface client would couple
 * every rendering change to a backend regeneration step, and the deep provenance
 * blocks are passed through to the raw-JSON view untouched.
 */

export type AnalysisTier = "full" | "no_soil_lulc" | "terrain_only";
export type SiteKind = "natural_depression" | "channel_position";

export interface ContourMapSummary {
  elevation_source: string;
  elevation_strategy: string;
  lines_parsed: number;
  lines_unresolved: number;
  vertices_used: number;
  levels: number;
  contour_interval_m: number | null;
  elevation_min_m: number;
  elevation_max_m: number;
  relief_m: number;
  bounds_4326: [number, number, number, number];
  centroid_4326: [number, number];
  working_crs_epsg: number;
  warnings: string[];
}

export interface InterpolatedTerrain {
  grid_resolution_m: number;
  grid_resolution_derived: boolean;
  mean_contour_spacing_m: number;
  grid_size: [number, number];
  grid_cells: number;
  hull_coverage_pct: number;
  interpolation_method: string;
  depressions_filled_cells: number;
  deepest_depression_m: number;
  max_upstream_area_ha: number;
}

export interface CriterionBreakdown {
  criterion: string;
  raw_value: number;
  normalised: number;
  weight: number;
  contribution: number;
}

export interface CatchmentMetrics {
  area_ha: number;
  area_km2: number;
  cell_count: number;
  perimeter_m: number;
  elevation_min_m: number;
  elevation_max_m: number;
  relief_m: number;
  mean_slope_pct: number | null;
  longest_flow_path_m: number;
  time_of_concentration_min: number | null;
  form_factor: number | null;
  compactness_coefficient: number | null;
  touches_grid_edge: boolean;
}

export interface CandidateSite {
  rank: number;
  suitability_score: number;
  site_kind: SiteKind;
  location: { lon: number; lat: number; grid_row: number; grid_col: number };
  terrain: {
    elevation_m: number;
    depression_depth_m: number;
    slope_pct: number;
    upstream_area_ha: number;
  };
  region: { cells: number; area_ha: number };
  criteria_breakdown: CriterionBreakdown[];
  catchment: {
    metrics: CatchmentMetrics;
    pour_point: { coordinates: [number, number] };
    snapped: { was_snapped: boolean; distance_m: number };
    quality: { touches_survey_edge: boolean; confidence: string };
    geometry?: GeoJSON.Geometry | null;
  };
  runoff?: {
    available: boolean;
    reason?: string;
    curve_number?: { composite_cn_amc2: number; hydrologic_soil_group: string };
    annual_mean?: { runoff_volume_m3: number; runoff_coefficient: number };
    design_75_percent_dependable?: { runoff_volume_m3: number };
  };
  pond?: {
    available: boolean;
    reason?: string;
    /** Flood-fill curve from the site point. `unbounded` marks the depths at
     *  which the water was no longer contained by terrain, so the storage
     *  beyond that point is not an impoundment. */
    stage_storage_curve?: StagePoint[];
    recommended?: {
      depth_m: number;
      top_length_m: number;
      top_width_m: number;
      gross_capacity_m3: number;
      live_storage_m3: number;
      estimated_cost_inr: number;
    };
    binding_constraint?: string;
    footprint?: { usable_buildable_area_ha: number };
  };
}

export interface StagePoint {
  depth_m: number;
  water_level_m: number;
  flooded_area_m2: number;
  storage_volume_m3: number;
  unbounded: boolean;
}

/** One connected patch of land that passed every exclusion (FR-3). */
export interface ParcelProperties {
  parcel_id: number;
  area_m2: number;
  area_ha: number;
  mean_slope_pct: number;
  max_slope_pct: number;
  dominant_land_cover: string;
  hydrologic_soil_group: string | null;
  distance_to_road_m: number | null;
  distance_to_settlement_m: number | null;
  mean_flow_accumulation_cells: number | null;
  ownership: string | null;
}

export interface LandAvailability {
  dem_id: string;
  summary: {
    parcel_count: number;
    total_available_ha: number;
    criteria: {
      max_slope_pct: number;
      min_parcel_area_m2: number;
      cropland_allowed: boolean;
      osm_exclusions_applied: boolean;
    };
    removed_by: Record<string, number>;
    parcels_dropped_below_min_area: number;
    considered_cells: number;
  };
  parcels: GeoJSON.FeatureCollection<
    GeoJSON.Polygon | GeoJSON.MultiPolygon,
    ParcelProperties
  >;
  unavailable: { layer: string; provider: string; reason: string }[];
  sources: {
    land_cover: Record<string, unknown> | null;
    osm: (Record<string, unknown> & { from_cache?: boolean }) | null;
  };
}

/** One pipeline stage, as reported by GET /analysis/{id}/status. */
export interface JobStep {
  name: string;
  label: string;
  /** Measured share of a cold run, so a client can draw the steps to scale. */
  weight: number;
  optional: boolean;
  outcome: "pending" | "running" | "done" | "failed" | "skipped";
}

export type JobState =
  | "queued"
  | "running"
  | "retrying"
  | "partial"
  | "done"
  | "failed"
  | "cancelled";

export interface JobStatus {
  job_id: string;
  state: JobState;
  state_meaning: string;
  /** Weighted by each step's measured cost, not by step count. */
  progress_pct: number;
  current_step: string | null;
  current_step_label: string | null;
  attempt: number;
  is_terminal: boolean;
  steps: JobStep[];
  warnings: string[];
  error: {
    type: string;
    title: string;
    detail: string;
    trace_id?: string;
  } | null;
  elapsed_s: number | null;
  result_url: string | null;
}

export interface JobStart {
  job_id: string;
  state: JobState;
  status_url: string;
  result_url: string;
  /** "celery" when a worker took it, "in_process" when this API ran it. */
  executor: string;
  estimated_duration_s: number;
  poll_after_s: number;
}

export interface Environment {
  analysis_tier: AnalysisTier;
  tier_meaning: string;
  layers_used: string[];
  layers_unavailable: string[];
  provider_failures: { layer: string; provider: string; reason: string }[];
  enrichment_elapsed_s: number;
  enrichment_skipped: boolean;
  soil: { usda_texture_class: string; hydrologic_soil_group: string } | null;
  land_cover: {
    dominant_class: string;
    class_fractions_pct: Record<string, number>;
  } | null;
  rainfall: {
    annual: {
      mean_mm: number;
      dependable_75_mm: number;
      coefficient_of_variation: number;
    };
    monsoon: { type: string; months: string[]; share_pct: number };
    /** Twelve mean-monthly totals, Jan..Dec. Absent only on older payloads. */
    monthly_normals_mm?: number[];
  } | null;
}

export interface ContourAnalysis {
  analysis_id: string;
  elapsed_s: number;
  stage_timings_s: Record<string, number>;
  contour_map: ContourMapSummary;
  interpolated_terrain: InterpolatedTerrain;
  area_of_interest: GeoJSON.Geometry;
  suitability: {
    analysis_tier: AnalysisTier;
    criteria_weights: Record<string, number>;
    feasible_cells: number;
  };
  environment: Environment;
  recommended_site: CandidateSite | null;
  candidate_sites: CandidateSite[];
  contours?: GeoJSON.FeatureCollection;
  /** Handle on the interpolated DEM, for requesting terrain tiles. */
  dem_id?: string;
  warnings: string[];
}

/** RFC 7807 problem details — every error the API returns has this shape. */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  trace_id?: string;
  errors?: { field: string; message: string }[];
}

export interface AnalyzeOptions {
  maxSites: number;
  cellSizeM: number | null;
  maxSlopePct: number;
  enrich: boolean;
  includeContours: boolean;
}

// ── Villages (M2-1, M2-2) ──────────────────────────────────────────────────

/** Where a coordinate came from. A sub-district centre frames a map; it is not
 *  the village's location, and `approximate` says so. */
export interface Focus {
  lon: number;
  lat: number;
  is_centre_of: string | null;
  approximate: boolean;
}

/** The elected body a village belongs to — and the only LGD code in open data. */
export interface GramPanchayat {
  name: string;
  lgd_code: string;
}

export interface VillageIdentifiers {
  /** Always null: no open source publishes a village LGD code. */
  lgd_code: string | null;
  census_2011_id: string | null;
  census_2001_id?: string | null;
  shrid: string | null;
}

export interface VillageMatch {
  id: string;
  name: string;
  /** Name plus hierarchy — what separates ten villages called Khapri. */
  display: string;
  hierarchy: {
    state: string | null;
    district: string | null;
    subdistrict: string | null;
    block: string | null;
  };
  identifiers: VillageIdentifiers;
  similarity: number;
  matched_by: "exact" | "folded" | "prefix" | "trigram";
  boundary_level: "village" | "subdistrict" | "district" | "state" | null;
  gram_panchayats: GramPanchayat[];
  /** Another result shares this name *and* hierarchy — show the code. */
  hierarchy_is_ambiguous: boolean;
  focus: Focus | null;
}

export interface VillageSearchResult {
  query: string;
  /** The canonical form actually searched on, after transliteration folding. */
  query_folded: string;
  filters: Record<string, string | null>;
  count: number;
  results: VillageMatch[];
  /** Set when the result needs explaining — nothing matched, or nothing well. */
  note: string | null;
}

export interface VillageBoundary {
  village_id: string;
  available: boolean;
  reason: string | null;
  geometry: GeoJSON.Geometry | null;
  /** What the polygon outlines. Read this before using `area_ha`. */
  represents: "village" | "subdistrict" | "district" | "state" | null;
  is_village_boundary: boolean;
  of: string | null;
  area_ha: number | null;
  source: string | null;
  caveat: string | null;
}

// ── Terrain raster layers (M2-3, M2-4) ─────────────────────────────────────

export type TerrainProduct = "dem" | "slope" | "hillshade";

export interface TerrainLayer {
  product: TerrainProduct;
  /** XYZ template with {z}/{x}/{y} left for the map client to fill. */
  tile_url_template: string;
  legend: string;
  min_zoom: number;
  max_zoom: number;
  tile_size: number;
  /** The raster was already on disk from an earlier request for this DEM. */
  reused: boolean;
  raster: {
    epsg: number;
    resolution_m: number;
    width_px: number;
    height_px: number;
    dtype: string;
    bounds_4326: [number, number, number, number];
    size_bytes: number;
    stats: Record<string, number>;
  };
}

export interface TerrainDerivatives {
  dem_id: string;
  working_crs: string;
  resolution_m: number;
  grid_size: [number, number];
  bounds_4326: [number, number, number, number];
  hillshade: { azimuth_deg: number; altitude_deg: number; z_factor: number };
  layers: TerrainLayer[];
  note: string | null;
}

// ── Drainage network (M3-3) ────────────────────────────────────────────────

export interface StreamNetworkReport {
  threshold_ha: number;
  threshold_cells: number;
  stream_cell_count: number;
  reach_count: number;
  max_strahler_order: number;
  total_length_m: number;
  total_length_km: number;
  drainage_density_km_per_km2: number | null;
  area_analysed_km2: number | null;
  by_order: Record<string, { reaches: number; length_m: number }>;
}

/** Which drainage network to ask for.
 *
 *  `site` clips it to the catchment above the recommended site; `sheet` returns
 *  every channel in the survey. The distinction matters beyond display: drainage
 *  density is a basin property, so the API reports it for `site` and leaves it
 *  null for `sheet` rather than dividing by a rectangle.
 */
export type StreamScope = "site" | "sheet";

export interface StreamsResponse {
  dem_id: string;
  network: StreamNetworkReport;
  streams: GeoJSON.FeatureCollection;
  catchment?: {
    area_ha: number;
    area_km2: number;
    outlet: GeoJSON.Geometry;
    snapped: { was_snapped: boolean; distance_m: number };
  };
}

// ── Interactive catchment delineation (M3-9b) ──────────────────────────────

export interface CatchmentSnap {
  was_snapped: boolean;
  moved_m: number;
  search_radius_m: number;
  /** The point moved as far as it was allowed, so it may not have reached a
   *  channel at all — the answer deserves less confidence. */
  hit_the_search_limit: boolean;
}

export interface DelineatedCatchment {
  dem_id: string;
  requested: GeoJSON.Geometry;
  outlet: GeoJSON.Geometry;
  snapped: CatchmentSnap;
  metrics: {
    area_ha: number;
    area_km2: number;
    relief_m: number;
    mean_slope_pct: number | null;
    longest_flow_path_m: number;
    time_of_concentration_min: number | null;
    form_factor: number | null;
    compactness_coefficient: number | null;
    touches_grid_edge: boolean;
  };
  geometry: GeoJSON.Geometry;
}
