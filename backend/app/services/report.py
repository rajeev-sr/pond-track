"""PDF report generation (M7-1, M7-3).

Jinja2 for the document and WeasyPrint to render it, which means the report is
HTML and CSS -- so its layout is inspectable and adjustable without touching
Python, and the same template could serve a web preview.

The interesting work here is not the rendering. It is deciding what a reader
needs, and in particular making the *caveats* travel with the numbers: an
interpolated surface, a footprint with no orientation, unmodelled land tenure, a
degraded tier. A PDF outlives the API response that explained itself, and it gets
forwarded to people who never saw the tool.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.logging import get_logger
from app.services import report_figures

log = get_logger("services.report")

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "report.html.j2"

#: Plain-language explanation of each binding constraint. The constraint name
#: alone tells a reader which variable stopped the search; this tells them what
#: to do about it, which is the difference between a calculator and advice.
BINDING_EXPLANATION: dict[str, str] = {
    "parcel_area": (
        "The buildable land around the site is what caps the pond, not the water "
        "available. Acquiring or consolidating adjacent land would raise capacity "
        "more than any change to the design."
    ),
    "practical_excavation_depth": (
        "The pond is at the deepest that is practical to excavate and maintain by "
        "the usual methods. Going deeper needs machinery and side protection that "
        "change the cost basis, so widening is the cheaper way to add capacity."
    ),
    "sustainable_yield_share": (
        "The pond is already sized to the share of the catchment's yield it is "
        "reasonable to impound. A larger structure would stand empty in an "
        "average year and pass most inflow over the spillway anyway."
    ),
    "runoff_yield": (
        "There is not enough runoff to justify a larger pond. The constraint is "
        "water, not land or depth."
    ),
    "budget": "The cost ceiling bound the design before any physical limit did.",
}

TIER_MEANING: dict[str, str] = {
    "full": "Terrain, soil, land cover and rainfall were all available.",
    "no_soil_lulc": (
        "Rainfall was available but soil and land cover were not, so runoff uses "
        "an assumed hydrologic soil group and land availability is unscored."
    ),
    "terrain_only": (
        "Only the uploaded terrain was available. No runoff, soil or land-cover "
        "criterion contributed, so this is a terrain-suitability ranking."
    ),
}


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _rupees(value: float | None) -> str:
    """Indian digit grouping: 1,20,48,099 rather than 12,048,099."""
    if value is None:
        return "—"
    whole = int(round(value))
    text = str(abs(whole))
    if len(text) > 3:
        head, tail = text[:-3], text[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        text = ",".join(parts) + "," + tail
    return f"₹{'-' if whole < 0 else ''}{text}"


def _volume(value: float | None) -> str:
    return "—" if value is None else f"{int(round(value)):,} m³"


def build_context(result: dict[str, Any], *, warnings: list[str] | None = None) -> dict[str, Any]:
    """Everything the template needs, flattened out of the result document.

    Flattened here rather than reached for in the template, because a template
    that walks a deep structure fails silently: a renamed key renders an empty
    cell rather than raising, and a report with a blank number looks like a
    measurement of zero.
    """
    sites = result.get("candidate_sites") or []
    recommended = result.get("recommended_site") or (sites[0] if sites else None)
    if recommended is None:
        raise ValueError("the analysis found no candidate sites, so there is nothing to report")

    environment = result.get("environment") or {}
    tier = environment.get("analysis_tier") or "terrain_only"
    pond_block = recommended.get("pond") or {}
    pond = pond_block.get("recommended") if pond_block.get("available") else None
    catchment = recommended.get("catchment") or {}
    metrics = catchment.get("metrics") or {}
    runoff_block = recommended.get("runoff") or {}
    runoff_ok = runoff_block.get("available")

    rainfall_block = environment.get("rainfall") or {}
    monthly = rainfall_block.get("monthly_normals_mm")

    site_map = report_figures.site_map_png(result)
    rainfall_png = None
    if monthly and len(monthly) == 12:
        rainfall_png = report_figures.rainfall_png(
            monthly, (rainfall_block.get("monsoon") or {}).get("months") or []
        )

    contour_map = result.get("contour_map") or {}

    binding = pond_block.get("binding_constraint")
    capture_pct = None
    if pond and runoff_ok:
        dependable = (runoff_block.get("design_75_percent_dependable") or {}).get(
            "runoff_volume_m3"
        )
        if dependable:
            capture_pct = round(100.0 * pond["gross_capacity_m3"] / dependable, 1)

    criteria_rows = [
        {
            "name": entry.get("criterion", "").replace("_", " "),
            "weight": round(float(entry.get("weight", 0.0)), 3),
            "normalised": round(float(entry.get("normalised", 0.0)), 3),
            "contribution": round(float(entry.get("contribution", 0.0)), 3),
        }
        for entry in recommended.get("criteria_breakdown") or []
    ]

    site_rows = []
    for site in sites:
        block = site.get("pond") or {}
        design = block.get("recommended") if block.get("available") else None
        site_rows.append(
            {
                "rank": site.get("rank"),
                "score": site.get("suitability_score"),
                "kind": str(site.get("site_kind", "")).replace("_", " "),
                "area_ha": (site.get("catchment") or {}).get("metrics", {}).get("area_ha"),
                "capacity": _volume(design.get("gross_capacity_m3")) if design else "—",
                "binding": str(block.get("binding_constraint") or "—").replace("_", " "),
            }
        )

    source_rows = [
        {
            "layer": "Terrain",
            "provider": (
                "Uploaded contour map " f"({(result.get('input') or {}).get('filename', 'KML')})"
            ),
            "licence": "supplied by the user",
        }
    ]
    for name, key in (("Soil", "soil"), ("Land cover", "land_cover"), ("Rainfall", "rainfall")):
        block = environment.get(key) or {}
        source = block.get("source") or {}
        if source:
            source_rows.append(
                {
                    "layer": name,
                    "provider": f"{source.get('provider', '?')} — {source.get('dataset', '')}",
                    "licence": source.get("licence") or "—",
                }
            )

    return {
        "analysis_id": result.get("analysis_id"),
        "generated_on": (result.get("generated_at") or "")[:10],
        "site_label": (result.get("input") or {}).get("filename") or "uploaded contour map",
        "tier": tier,
        "tier_label": tier.replace("_", " ").title(),
        "tier_meaning": TIER_MEANING.get(tier, ""),
        "partial_warnings": list(warnings or []),
        "recommended": recommended,
        "site_count": len(sites),
        "site_rows": site_rows,
        "pond": pond,
        "cost_inr": _rupees(pond.get("estimated_cost_inr")) if pond else "—",
        "excavation_rate": int(
            round(float((pond_block.get("cost_basis") or {}).get("excavation_inr_per_m3", 130)))
        ),
        "binding_constraint": str(binding).replace("_", " ") if binding else None,
        "binding_explanation": BINDING_EXPLANATION.get(
            str(binding), "See the constraints evaluated in the full JSON result."
        ),
        "no_pond_reason": pond_block.get("reason") or "No feasible design was found.",
        "catchment": {
            "area_ha": metrics.get("area_ha"),
            "area_km2": metrics.get("area_km2"),
            "relief_m": metrics.get("relief_m"),
            "longest_flow_path_m": metrics.get("longest_flow_path_m"),
            "time_of_concentration_min": (
                None
                if metrics.get("time_of_concentration_min") is None
                else round(float(metrics["time_of_concentration_min"]))
            ),
            "mean_slope_pct": metrics.get("mean_slope_pct"),
        },
        "touches_edge": (catchment.get("quality") or {}).get("touches_survey_edge"),
        "rainfall": (
            None
            if not rainfall_block
            else {
                "mean_mm": (rainfall_block.get("annual") or {}).get("mean_mm"),
                "dependable_75_mm": (rainfall_block.get("annual") or {}).get("dependable_75_mm"),
                "cv": (rainfall_block.get("annual") or {}).get("coefficient_of_variation"),
                "period": " to ".join(
                    filter(
                        None,
                        [
                            (rainfall_block.get("period") or {}).get("start", "")[:4],
                            (rainfall_block.get("period") or {}).get("end", "")[:4],
                        ],
                    )
                ),
                "monsoon_months": (rainfall_block.get("monsoon") or {}).get("months") or [],
                "monsoon_share_pct": (rainfall_block.get("monsoon") or {}).get("share_pct"),
                "source": (rainfall_block.get("source") or {}).get("provider", "reanalysis"),
            }
        ),
        "runoff": (
            None
            if not runoff_ok
            else {
                "cn": (runoff_block.get("curve_number") or {}).get("composite_cn_amc2"),
                "hsg": (runoff_block.get("curve_number") or {}).get("hydrologic_soil_group"),
                "annual_m3": (runoff_block.get("annual_mean") or {}).get("runoff_volume_m3"),
                "coefficient": (runoff_block.get("annual_mean") or {}).get("runoff_coefficient"),
                "dependable_m3": (runoff_block.get("design_75_percent_dependable") or {}).get(
                    "runoff_volume_m3"
                ),
            }
        ),
        "capture_pct": capture_pct,
        "criteria_rows": criteria_rows,
        "criteria_count": len(criteria_rows),
        "consistency_ratio": 0.009,
        "contour_interval": contour_map.get("contour_interval_m") or "—",
        "has_contours": bool((result.get("contours") or {}).get("features")),
        "osm_caveat": True,
        "site_map_b64": base64.b64encode(site_map).decode("ascii"),
        "rainfall_b64": (
            "" if rainfall_png is None else base64.b64encode(rainfall_png).decode("ascii")
        ),
        "source_rows": source_rows,
    }


def render_html(result: dict[str, Any], *, warnings: list[str] | None = None) -> str:
    context = build_context(result, warnings=warnings)
    return _environment().get_template(TEMPLATE_NAME).render(**context)


def render_pdf(result: dict[str, Any], *, warnings: list[str] | None = None) -> bytes:
    """The report as PDF bytes.

    WeasyPrint is imported here rather than at module scope so that importing
    this module -- which the API does at startup -- does not require the Pango
    and Cairo libraries to be present. A deployment missing them then fails when
    a report is asked for, with a clear error, instead of refusing to boot.
    """
    from weasyprint import HTML

    html = render_html(result, warnings=warnings)
    pdf = HTML(string=html).write_pdf()
    if not pdf:
        raise RuntimeError("WeasyPrint produced an empty document")
    log.info("report rendered", bytes=len(pdf), analysis_id=result.get("analysis_id"))
    return bytes(pdf)
