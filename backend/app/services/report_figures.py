"""Static figures for the PDF report (M7-2).

Matplotlib rather than a screenshot of the web map, for two reasons that both
matter for a document meant to be printed: the output is vector, so contour lines
stay sharp at any zoom, and it needs no browser, so generating a report is not
gated on a headless Chrome being installed.

No basemap tiles. The plan named contextily, which fetches raster tiles from a
web service -- and a report generator that fails when the network is down is
worse than one that draws the analysis on white. The contours *are* the base map
here: this is a contour-map tool, and the sheet the user uploaded is the most
authoritative background available for it.
"""

from __future__ import annotations

import io
from typing import Any

import matplotlib

# Agg before pyplot: there is no display in a container, and the default
# interactive backend fails at import rather than at draw time.
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MplPolygon

#: Print-oriented: 150 dpi is enough for a raster fallback without bloating the
#: PDF, and the figures are vector anyway.
DPI = 150

INK = "#111111"
MUTED = "#8a8a8a"
CONTOUR = "#b9a7e0"
CATCHMENT = "#1f9bb5"
SITE = "#e08a1f"
POND = "#d05a1f"


def _rings(geometry: dict[str, Any] | None) -> list[list[tuple[float, float]]]:
    """Every exterior ring of a Polygon or MultiPolygon, as coordinate lists."""
    if not geometry:
        return []
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [[(float(x), float(y)) for x, y in ring] for ring in coords[:1]]
    if kind == "MultiPolygon":
        return [[(float(x), float(y)) for x, y in part[0]] for part in coords if part and part[0]]
    return []


def _lines(geometry: dict[str, Any] | None) -> list[list[tuple[float, float]]]:
    if not geometry:
        return []
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "LineString":
        return [[(float(x), float(y)) for x, y in coords]]
    if kind == "MultiLineString":
        return [[(float(x), float(y)) for x, y in part] for part in coords]
    return []


def site_map_png(result: dict[str, Any], *, width_in: float = 6.4) -> bytes:
    """The analysis as one figure: contours, catchments, sites, pond footprints.

    Returns PNG bytes so the caller can embed it as a data URI -- WeasyPrint
    resolves those without a filesystem path, which keeps the renderer from
    needing a writable temp directory.
    """
    aoi = result.get("area_of_interest")
    rings = _rings(aoi)
    if not rings:
        raise ValueError("the result carries no area_of_interest to draw")

    xs = [x for ring in rings for x, _ in ring]
    ys = [y for ring in rings for _, y in ring]
    west, east, south, north = min(xs), max(xs), min(ys), max(ys)

    # Aspect from latitude, so the sheet is not stretched: a degree of longitude
    # is shorter than a degree of latitude everywhere but the equator.
    import math

    mid_lat = (south + north) / 2.0
    aspect = 1.0 / max(math.cos(math.radians(mid_lat)), 1e-6)
    span_x, span_y = east - west, north - south
    height_in = width_in * (span_y * aspect) / max(span_x, 1e-9)
    height_in = max(2.4, min(height_in, 7.5))

    figure, axes = plt.subplots(figsize=(width_in, height_in), dpi=DPI)

    contours = result.get("contours") or {}
    drawn_contours = 0
    for feature in contours.get("features", []) if isinstance(contours, dict) else []:
        for line in _lines(feature.get("geometry")):
            if len(line) < 2:
                continue
            axes.plot(
                [p[0] for p in line],
                [p[1] for p in line],
                color=CONTOUR,
                linewidth=0.25,
                zorder=1,
            )
            drawn_contours += 1

    for ring in rings:
        axes.add_patch(
            MplPolygon(
                ring,
                closed=True,
                fill=False,
                edgecolor=MUTED,
                linewidth=0.8,
                linestyle=(0, (4, 3)),
                zorder=2,
            )
        )

    for site in result.get("candidate_sites") or []:
        catchment = (site.get("catchment") or {}).get("geometry")
        for ring in _rings(catchment):
            axes.add_patch(
                MplPolygon(
                    ring,
                    closed=True,
                    facecolor=CATCHMENT,
                    alpha=0.13,
                    edgecolor=CATCHMENT,
                    linewidth=1.0,
                    zorder=3,
                )
            )

        location = site.get("location") or {}
        if "lon" not in location:
            continue
        lon, lat = float(location["lon"]), float(location["lat"])
        axes.plot(
            lon,
            lat,
            marker="o",
            markersize=7,
            color=SITE,
            markeredgecolor="white",
            markeredgewidth=1.2,
            zorder=6,
        )
        axes.annotate(
            f"#{site.get('rank')}",
            (lon, lat),
            textcoords="offset points",
            xytext=(8, 5),
            fontsize=8,
            color=INK,
            zorder=7,
        )

    axes.set_xlim(west - span_x * 0.02, east + span_x * 0.02)
    axes.set_ylim(south - span_y * 0.02, north + span_y * 0.02)
    axes.set_aspect(aspect)
    # No axis furniture: a lon/lat tick frame on a village-scale figure is
    # numbers nobody reads. The scale bar carries what a reader needs.
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(False)

    _scale_bar(axes, west, south, span_x, span_y, mid_lat)
    _legend(axes, drawn_contours > 0)

    buffer = io.BytesIO()
    figure.tight_layout(pad=0.2)
    figure.savefig(buffer, format="png", dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()


def _scale_bar(
    axes: Any, west: float, south: float, span_x: float, span_y: float, lat: float
) -> None:
    """A metric scale bar, because degrees mean nothing to a reader on the ground."""
    import math

    m_per_deg_lon = 111_320.0 * max(math.cos(math.radians(lat)), 1e-6)
    span_m = span_x * m_per_deg_lon
    # A round number that occupies roughly a quarter of the frame.
    for candidate in (10_000, 5000, 2000, 1000, 500, 200, 100):
        if candidate <= span_m * 0.3:
            length_m = candidate
            break
    else:
        length_m = 100
    length_deg = length_m / m_per_deg_lon

    x0 = west + span_x * 0.04
    y0 = south + span_y * 0.04
    axes.plot([x0, x0 + length_deg], [y0, y0], color=INK, linewidth=2.2, zorder=8)
    label = f"{length_m / 1000:g} km" if length_m >= 1000 else f"{length_m:g} m"
    axes.annotate(
        label,
        (x0 + length_deg / 2, y0),
        textcoords="offset points",
        xytext=(0, 4),
        ha="center",
        fontsize=7,
        color=INK,
        zorder=8,
    )


def _legend(axes: Any, with_contours: bool) -> None:
    from matplotlib.lines import Line2D

    entries = [
        Line2D([], [], color=SITE, marker="o", linestyle="", markersize=6, label="Candidate site"),
        Line2D([], [], color=CATCHMENT, linewidth=1.4, label="Catchment"),
        Line2D([], [], color=MUTED, linewidth=0.9, linestyle=(0, (4, 3)), label="Survey extent"),
    ]
    if with_contours:
        entries.insert(2, Line2D([], [], color=CONTOUR, linewidth=0.9, label="Contours"))
    axes.legend(
        handles=entries,
        loc="upper right",
        fontsize=7,
        frameon=True,
        framealpha=0.9,
        edgecolor="#dddddd",
    )


def rainfall_png(
    monthly_mm: list[float], monsoon_months: list[str], *, width_in: float = 6.4
) -> bytes:
    """Monthly rainfall normals as a column chart, matching the web UI's reading.

    Same emphasis idea as on screen: one measure, one hue, with the monsoon
    window picked out rather than given a second categorical colour.
    """
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    if len(monthly_mm) != 12:
        raise ValueError(f"expected 12 monthly values, got {len(monthly_mm)}")

    figure, axes = plt.subplots(figsize=(width_in, 2.0), dpi=DPI)
    colours = ["#2a78d6" if name in monsoon_months else "#b8c2cc" for name in names]
    axes.bar(names, monthly_mm, color=colours, width=0.62)

    peak = max(range(12), key=lambda i: monthly_mm[i])
    axes.annotate(
        f"{monthly_mm[peak]:,.0f} mm",
        (peak, monthly_mm[peak]),
        textcoords="offset points",
        xytext=(0, 3),
        ha="center",
        fontsize=7,
        color=INK,
    )

    axes.set_ylabel("mm", fontsize=7)
    axes.tick_params(labelsize=7)
    axes.spines["top"].set_visible(False)
    axes.spines["right"].set_visible(False)
    axes.spines["left"].set_color("#cccccc")
    axes.spines["bottom"].set_color("#cccccc")
    axes.grid(axis="y", color="#eeeeee", linewidth=0.7)
    axes.set_axisbelow(True)

    buffer = io.BytesIO()
    figure.tight_layout(pad=0.2)
    figure.savefig(buffer, format="png", dpi=DPI, bbox_inches="tight", facecolor="white")
    plt.close(figure)
    return buffer.getvalue()
