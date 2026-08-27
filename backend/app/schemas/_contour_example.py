"""A real `POST /analyzeContour` response, trimmed for documentation (MC-23).

Captured from an actual run against the sample contour map, then abbreviated
wherever a block simply repeats (per-criterion entries, the stage-storage
curve, GeoJSON coordinate rings). Generated from a live response rather than
hand-written, so it cannot drift from what the API returns.

This capture is worth reading closely: SoilGrids returned HTTP 503 during the
run, so `analysis_tier` is `no_soil_lulc` rather than `full`, the Hydrologic
Soil Group falls back to an assumed C, and `provider_failures` names what
happened. That is the degradation ladder working, and it makes a more
instructive example than a clean run would.
"""

from __future__ import annotations

from typing import Any

ANALYZE_CONTOUR_EXAMPLE: dict[str, Any] = {
    "analysis_id": "753351e6d3674a25",
    "generated_at": "2026-08-26T19:33:25.727568+00:00",
    "elapsed_s": 7.383,
    "stage_timings_s": {
        "parse": 0.139,
        "interpolate": 1.096,
        "condition": 0.607,
        "flow_routing": 0.251,
        "enrichment": 4.046,
        "siting": 0.121,
        "catchments": 1.122,
    },
    "input": {
        "filename": "contours_1m.kml",
        "size_bytes": 6710528,
        "options": {
            "cell_size_m": None,
            "max_sites": 3,
            "max_slope_pct": 8.0,
            "min_upstream_ha": 1.0,
            "score_threshold": 0.55,
            "min_separation_m": 300.0,
            "min_depression_depth_m": 0.3,
            "snap_radius_m": 150.0,
            "include_catchment_geometry": True,
            "include_contours": False,
            "stream_threshold_ha": 5.0,
            "enrich": True,
            "rainfall_years": 30,
            "enrichment_budget_s": 20.0,
        },
    },
    "contour_map": {
        "elevation_source": "uploaded_contour_map",
        "elevation_strategy": "placemark_name",
        "lines_parsed": 1355,
        "lines_unresolved": 0,
        "vertices_used": 159113,
        "levels": 32,
        "contour_interval_m": 1.0,
        "elevation_min_m": 267.0,
        "elevation_max_m": 298.0,
        "relief_m": 31.0,
        "bounds_4326": [81.2814044952393, 21.2398224433387, 81.3126468658447, 21.2635806472203],
        "centroid_4326": [81.29702568054199, 21.251701545279502],
        "working_crs_epsg": 32644,
        "has_boundary_polygon": True,
        "warnings": [],
    },
    "interpolated_terrain": {
        "grid_resolution_m": 5.0,
        "grid_resolution_derived": True,
        "mean_contour_spacing_m": 14.96,
        "total_contour_length_m": 568761.4,
        "grid_size": [650, 527],
        "grid_cells": 342550,
        "interpolation_method": "linear_tin_delaunay",
        "vertices_after_resample": 115808,
        "vertices_before_resample": 159113,
        "smoothing_sigma_cells": 0.75,
        "hull_coverage_pct": 97.1,
        "interpolated_elevation_min_m": 267.0,
        "interpolated_elevation_max_m": 297.989,
        "interpolated_relief_m": 30.989,
        "depressions_filled_cells": 99493,
        "deepest_depression_m": 11.998,
        "outlet_cells": 2350,
        "valid_cells": 334914,
        "max_upstream_cells": 154237,
        "max_upstream_area_ha": 385.592,
    },
    "area_of_interest": {"type": "Polygon", "coordinates": "... bbox ring ..."},
    "suitability": {
        "analysis_tier": "no_soil_lulc",
        "tier_meaning": "terrain + rainfall: runoff is estimated using an assumed "
        "soil group and land cover, both stated in the response",
        "layers_used": [
            "elevation",
            "slope",
            "flow_accumulation",
            "depression_depth",
            "land_use_land_cover",
            "rainfall",
            "reference_evapotranspiration",
        ],
        "layers_unavailable": ["soil_hydrologic_group"],
        "criteria_weights": {
            "flow_accumulation": 0.2877,
            "slope": 0.2466,
            "depression_depth": 0.1918,
            "plan_concavity": 0.1096,
            "land_availability": 0.1644,
        },
        "constraints_applied": {
            "max_slope_pct": 8.0,
            "min_upstream_area_ha": 1.0,
            "score_threshold": 0.55,
            "min_separation_m": 300.0,
            "min_depression_depth_m": 0.3,
            "edge_buffer_cells": 3.0,
            "land_cover_exclusion_applied": True,
        },
        "feasible_cells": 5292,
    },
    "environment": {
        "analysis_tier": "no_soil_lulc",
        "tier_meaning": "terrain + rainfall: runoff is estimated using an assumed "
        "soil group and land cover, both stated in the response",
        "layers_used": [
            "elevation",
            "slope",
            "flow_accumulation",
            "depression_depth",
            "land_use_land_cover",
            "rainfall",
            "reference_evapotranspiration",
        ],
        "layers_unavailable": ["soil_hydrologic_group"],
        "provider_failures": [
            {
                "layer": "soil_hydrologic_group",
                "reason": "HTTP 503 from rest.isric.org",
                "provider": "soilgrids",
            }
        ],
        "enrichment_elapsed_s": 4.05,
        "enrichment_budget_s": 20.0,
        "enrichment_skipped": False,
        "soil": None,
        "land_cover": {
            "dominant_class": "cropland",
            "class_fractions_pct": {
                "cropland": 46.01,
                "grassland": 26.16,
                "built_up": 9.47,
                "tree_cover": 8.46,
                "permanent_water": 8.06,
                "bare_sparse_vegetation": 1.8,
                "shrubland": 0.04,
            },
            "tiles_used": ["ESA_WorldCover_10m_2021_v200_N21E081_Map"],
            "source": {
                "provider": "ESA WorldCover",
                "dataset": "ESA WorldCover 2021 v200",
                "resolution": "10 m",
                "licence": "CC-BY 4.0",
                "requires_credential": False,
            },
        },
        "rainfall": {
            "period": {"start": "1996-01-01", "end": "2025-12-31", "complete_years": 30},
            "annual": {
                "mean_mm": 1312.8,
                "median_mm": 1261.4,
                "std_dev_mm": 227.9,
                "coefficient_of_variation": 0.174,
                "min_mm": 819.8,
                "max_mm": 1857.6,
                "dependable_50_mm": 1261.4,
                "dependable_75_mm": 1167.9,
                "dependable_90_mm": 1088.5,
            },
            "monsoon": {
                "type": "southwest",
                "months": ["Jun", "Jul", "Aug", "Sep"],
                "share_pct": 88.9,
                "note": "Window derived from the monthly normals "
                "-- the four consecutive months carrying "
                "the largest share -- not assumed to be "
                "June-September.",
            },
            "rainy_days_per_year": 86.0,
            "max_1day_mm": 173.4,
            "reanalysis_model": "default (best available)",
            "source": {
                "provider": "Open-Meteo",
                "dataset": "ERA5-Land reanalysis (archive)",
                "resolution": "0.1 deg (~11 km)",
                "licence": "CC-BY 4.0, non-commercial use",
                "requires_credential": False,
            },
            "data_caveat": "ERA5-Land reanalysis at ~11 km. Suitable for "
            "design screening, but for a submitted scheme "
            "cross-check against IMD's 0.25 deg gauge-based "
            "grid, which is the authoritative Indian "
            "record.",
        },
    },
    "recommended_site": {
        "rank": 1,
        "suitability_score": 81.3,
        "site_kind": "channel_position",
        "location": {
            "lon": 81.2954525,
            "lat": 21.2511366,
            "projected_x_m": 530654.47,
            "projected_y_m": 2349970.67,
            "grid_row": 275,
            "grid_col": 292,
        },
        "terrain": {
            "elevation_m": 278.0,
            "depression_depth_m": 4.0,
            "slope_pct": 3.98,
            "upstream_cells": 66794,
            "upstream_area_ha": 166.985,
            "slope_pct_at_site": 0.04,
        },
        "region": {"cells": 6, "area_m2": 150.0, "area_ha": 0.015},
        "catchment_pour_point": {"grid_row": 275, "grid_col": 292},
        "criteria_breakdown": [
            {
                "criterion": "flow_accumulation",
                "raw_value": 66794.0,
                "normalised": 0.9727,
                "weight": 0.2877,
                "contribution": 0.2798,
            },
            {
                "criterion": "slope",
                "raw_value": 3.9799,
                "normalised": 0.5473,
                "weight": 0.2466,
                "contribution": 0.135,
            },
            "... one entry per criterion ...",
        ],
        "catchment": {
            "metrics": {
                "area_m2": 1875275.0,
                "area_ha": 187.528,
                "area_km2": 1.87528,
                "cell_count": 75011,
                "perimeter_m": 8980.0,
                "elevation_min_m": 281.32,
                "elevation_max_m": 297.99,
                "relief_m": 16.66,
                "mean_slope_pct": 3.96,
                "max_slope_pct": 45.03,
                "longest_flow_path_m": 2870.4,
                "time_of_concentration_min": 65.0,
                "form_factor": 0.2276,
                "compactness_coefficient": 1.85,
                "outlet_accumulation_cells": 75011,
                "touches_grid_edge": False,
            },
            "pour_point": {
                "type": "Point",
                "coordinates": [81.2940086, 21.2520426],
                "grid_row": 255,
                "grid_col": 262,
            },
            "snapped": {"was_snapped": True, "distance_m": 180.3, "search_radius_m": 150.0},
            "quality": {"touches_survey_edge": False, "confidence": "high"},
            "geometry": {"type": "Polygon", "coordinates": "... GeoJSON ring, EPSG:4326 " "..."},
        },
        "runoff": {
            "method": "SCS-CN, daily, Ia = 0.3 S",
            "catchment_area_ha": 187.528,
            "curve_number": {
                "composite_cn_amc2": 83.7,
                "composite_cn_amc1_dry": 68.4,
                "composite_cn_amc3_wet": 92.2,
                "hydrologic_soil_group": "C",
                "land_cover_source": "esa_worldcover " "(zonal, within the " "catchment)",
                "breakdown": [
                    {
                        "land_cover": "cropland",
                        "hydrologic_soil_group": "C",
                        "area_fraction": 0.414,
                        "curve_number": 85.0,
                        "weighted_contribution": 35.19,
                    },
                    {
                        "land_cover": "grassland",
                        "hydrologic_soil_group": "C",
                        "area_fraction": 0.3154,
                        "curve_number": 79.0,
                        "weighted_contribution": 24.92,
                    },
                    "... one entry per " "land-cover class ...",
                ],
            },
            "annual_mean": {
                "runoff_depth_mm": 256.5,
                "runoff_volume_m3": 480958.0,
                "runoff_coefficient": 0.195,
            },
            "design_75_percent_dependable": {
                "runoff_depth_mm": 165.2,
                "runoff_volume_m3": 309828.0,
                "note": "Dependable "
                "*runoff*, taken "
                "from the ranked "
                "annual runoff "
                "series rather "
                "than computed "
                "from dependable "
                "rainfall -- the "
                "SCS equation is "
                "non-linear, so "
                "the two are not "
                "the same.",
            },
            "monthly_mean_runoff_mm": [
                0.7,
                0.0,
                0.0,
                0.0,
                0.0,
                31.0,
                90.9,
                80.8,
                48.8,
                4.1,
                0.1,
                0.1,
            ],
            "assumptions": [
                "soil data was unavailable; Hydrologic Soil "
                "Group assumed to be C (mid-range) -- "
                "verify before using the figure for design",
                "Ia = 0.3 S (Indian practice per CWC/IMD, " "not the US default 0.2 S)",
                "SCS-CN applied to the daily series and " "summed, never to annual totals",
                "Antecedent moisture classified per day " "from the preceding 5 days of rainfall",
                "Growing season taken as the derived " "monsoon window [6, 7, 8, 9]",
                "Curve numbers from NRCS TR-55 mapped to "
                "esa_worldcover (zonal, within the "
                "catchment) classes, cross-checked against "
                "the Indian Handbook of Hydrology",
            ],
        },
        "pond": {
            "available": True,
            "recommended": {
                "depth_m": 4.5,
                "freeboard_m": 0.5,
                "side_slope": "1V : 1.5H",
                "top_length_m": 141.4,
                "top_width_m": 141.4,
                "bottom_length_m": 127.9,
                "bottom_width_m": 127.9,
                "top_area_m2": 20000.0,
                "bottom_area_m2": 16363.9,
                "gross_capacity_m3": 81682.0,
                "dead_storage_silt_m3": 8168.2,
                "live_storage_m3": 73513.8,
                "excavation_volume_m3": 81682.0,
                "embankment_volume_m3": 20420.5,
                "estimated_cost_inr": 12048099.0,
            },
            "terrain_derived_capacity_m3": 48193.8,
            "binding_constraint": "practical_excavation_depth",
            "constraints_evaluated": {
                "practical_excavation_depth": 4.5,
                "sustainable_yield_share": 4.5,
            },
            "hydrological_check": {
                "annual_inflow_m3": 480958.0,
                "capacity_to_inflow_ratio": 0.16983,
                "interpretation": "capacity is well " "matched to the " "catchment's yield",
            },
            "footprint": {
                "usable_buildable_area_m2": 20000.0,
                "usable_buildable_area_ha": 2.0,
                "capped_at_max": True,
                "max_considered_m2": 20000.0,
                "note": "Contiguous land around the site that "
                "passed every feasibility mask, not the "
                "extent of the scoring cluster.",
            },
            "stage_storage_curve": [
                {
                    "depth_m": 0.0,
                    "water_level_m": 278.0,
                    "flooded_area_m2": 0.0,
                    "storage_volume_m3": 0.0,
                    "unbounded": False,
                },
                {
                    "depth_m": 0.25,
                    "water_level_m": 278.25,
                    "flooded_area_m2": 1450.0,
                    "storage_volume_m3": 279.1,
                    "unbounded": False,
                },
                {
                    "depth_m": 0.5,
                    "water_level_m": 278.5,
                    "flooded_area_m2": 1950.0,
                    "storage_volume_m3": 702.6,
                    "unbounded": False,
                },
                "... one entry per 0.25 m of depth " "...",
            ],
            "recommendations": [
                "Depth is not constrained by groundwater "
                "here because no water-table measurement "
                "was supplied. Check the pre-monsoon "
                "level (CGWB observation wells) before "
                "excavating: cutting into a shallow table "
                "converts a storage pond into a seepage "
                "pit.",
                "Provide 0.5 m freeboard above full "
                "supply level and a surplus weir sized "
                "for the design storm.",
                "Reserve 10% of capacity as dead storage "
                "for silt and provide a silt trap at the "
                "inlet.",
            ],
            "cost_basis": {
                "excavation_inr_per_m3": 130.0,
                "embankment_inr_per_m3": 70.0,
                "note": "Indicative order of magnitude, not a " "tender estimate.",
            },
        },
    },
    "candidate_sites": ["... same shape as recommended_site, one per candidate ..."],
    "warnings": [
        "99,493 cell(s) lay in closed depressions and were flooded to their spill "
        "level (deepest 12.00 m); these mark natural basins and are prime pond sites",
        "the deepest depression is 12.00 m; verify it is a real landform rather than "
        "an interpolation artefact",
        "1 candidate(s) suppressed for lying within 300 m of a higher-ranked site",
        "era5_land: 100% of days null",
        "soil_hydrologic_group unavailable (HTTP 503 from rest.isric.org); the "
        "analysis continued at tier 'no_soil_lulc'",
    ],
}
