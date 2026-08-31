"""★ Two elevation sources, one pipeline (ADR-7, M1-1).

The extensibility claim in the report is that a contour upload and a remote DEM
tile are interchangeable implementations of one protocol, so everything below
`DemGrid` is written once. A protocol with a single implementation demonstrates
nothing, so this file exercises both against the *same* downstream code.

The synthetic source stands in for the remote DEM in the offline tests: what is
under test is that the pipeline is source-agnostic, not that AWS is reachable.
`TestAgainstTheRealBucket` covers the live fetch and is `network`-marked.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from app.providers.elevation.base import Bounds, DemGrid, ElevationSource
from app.providers.elevation.contour_kml import parse_contour_file
from app.providers.elevation.copernicus_aws import CopernicusDemSource, tile_name
from app.services import hydrology as hyd
from app.services import siting
from app.services.interpolate import contours_to_dem
from app.tests.synthetic_kml import build_kml, concentric_rings


# ── two sources, both satisfying the protocol ────────────────────────────────
class ContourMapSource:
    """`ElevationSource` over a parsed contour map."""

    name = "uploaded_contour_map"

    def __init__(self, kml: bytes) -> None:
        self._parsed = parse_contour_file(kml)

    @property
    def bounds(self) -> Bounds:
        return self._parsed.bounds

    def to_dem(self, cell_size_m: float | None = None) -> DemGrid:
        dem, _report = contours_to_dem(self._parsed, cell_size_m=cell_size_m)
        return dem


class SyntheticRasterSource:
    """Stands in for a remote DEM: hands over a raster directly, no contours."""

    name = "synthetic_raster"

    def __init__(self, elevation: np.ndarray, cell: float = 5.0) -> None:
        self._z = elevation.astype(np.float32)
        self._cell = cell

    def to_dem(self, cell_size_m: float | None = None) -> DemGrid:
        cell = float(cell_size_m or self._cell)
        rows, _cols = self._z.shape
        return DemGrid(
            elevation=self._z,
            transform=(cell, 0.0, 500_000.0, 0.0, -cell, 2_340_000.0 + rows * cell),
            epsg=32644,
            cell_size_m=cell,
        )


def _sample_path() -> pathlib.Path | None:
    """Locate the sample map without depending on the working directory."""
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "contours_1m.kml"
        if candidate.is_file():
            return candidate
    return None


def valley_kml() -> bytes:
    return build_kml(
        concentric_rings(
            center=(81.29, 21.25),
            levels=tuple(270.0 + i for i in range(12)),
            step_deg=0.0009,
            vertices=72,
        )
    )


def bowl_raster(n: int = 90, depth: float = 6.0) -> np.ndarray:
    rr, cc = np.mgrid[0:n, 0:n]
    axis = n / 2.0
    z = 280.0 + (n - rr) * 0.2 + np.abs(cc - axis) * 0.12
    r = np.hypot(rr - int(n * 0.6), cc - int(axis))
    return z - np.where(r < 8.0, depth * (1.0 - r / 8.0), 0.0)


def run_pipeline(source: object) -> dict[str, object]:
    """The whole downstream pipeline, written once, source-agnostic."""
    dem = source.to_dem()  # type: ignore[attr-defined]
    conditioned = hyd.fill_depressions(dem)
    flow = hyd.build_flow(dem, conditioned)
    result = siting.identify_pond_sites(dem, conditioned, flow, max_sites=3, min_separation_m=60.0)
    if not result.sites:
        return {"dem": dem, "sites": 0}
    site = result.sites[0]
    catchment = hyd.delineate_catchment(
        dem, flow, site.outlet_row, site.outlet_col, snap_radius_cells=10
    )
    return {
        "dem": dem,
        "sites": len(result.sites),
        "catchment_ha": catchment.area_ha,
        "metrics": hyd.catchment_metrics(dem, conditioned, flow, catchment),
        "invariant_holds": catchment.accumulation_at_outlet == catchment.cell_count,
    }


class TestProtocolConformance:
    def test_both_sources_satisfy_the_protocol(self) -> None:
        assert isinstance(ContourMapSource(valley_kml()), ElevationSource)
        assert isinstance(SyntheticRasterSource(bowl_raster()), ElevationSource)

    def test_the_real_remote_source_satisfies_it_too(self) -> None:
        """Constructed, not called: no network in this assertion."""
        src = CopernicusDemSource(Bounds(81.28, 21.24, 81.31, 21.26))
        assert isinstance(src, ElevationSource)
        assert src.name

    def test_both_produce_a_projected_metric_dem(self) -> None:
        from app.core.crs import CRSGuard

        for source in (ContourMapSource(valley_kml()), SyntheticRasterSource(bowl_raster())):
            dem = source.to_dem()
            CRSGuard.require_projected(dem.epsg, "area calculation")
            assert dem.cell_size_m > 0
            assert dem.elevation.ndim == 2


class TestPipelineIsSourceAgnostic:
    """★ The same downstream code runs on both, and the invariants hold on both."""

    @pytest.mark.parametrize(
        "make_source",
        [
            pytest.param(lambda: ContourMapSource(valley_kml()), id="contour_map"),
            pytest.param(lambda: SyntheticRasterSource(bowl_raster()), id="raster"),
        ],
    )
    def test_pipeline_completes_and_invariants_hold(self, make_source) -> None:  # type: ignore[no-untyped-def]
        out = run_pipeline(make_source())
        assert out["sites"], "no candidate site found"
        assert float(out["catchment_ha"]) > 0  # type: ignore[arg-type]
        assert out["invariant_holds"], "accumulation at outlet != catchment cell count"

    @pytest.mark.parametrize(
        "make_source",
        [
            pytest.param(lambda: ContourMapSource(valley_kml()), id="contour_map"),
            pytest.param(lambda: SyntheticRasterSource(bowl_raster()), id="raster"),
        ],
    )
    def test_conditioning_leaves_no_interior_sink(self, make_source) -> None:  # type: ignore[no-untyped-def]
        dem = make_source().to_dem()
        cond = hyd.fill_depressions(dem)
        stuck = hyd.cells_without_lower_neighbour(cond.filled, cond.valid)
        edge = np.zeros_like(cond.valid)
        edge[0, :] = edge[-1, :] = True
        edge[:, 0] = edge[:, -1] = True
        outlets = cond.valid & (edge | hyd._dilate(~cond.valid))
        assert np.all(stuck <= outlets)

    def test_neither_source_leaks_into_the_downstream_result(self) -> None:
        """Nothing below DemGrid may branch on where the terrain came from."""
        contour = run_pipeline(ContourMapSource(valley_kml()))
        raster = run_pipeline(SyntheticRasterSource(bowl_raster()))
        # The metric *keys* must be identical: different terrain, same contract.
        assert set(contour["metrics"].keys()) == set(raster["metrics"].keys())  # type: ignore[union-attr]

    def test_the_two_sources_give_different_answers(self) -> None:
        """Sanity on the test itself: if both returned the same numbers, the
        parametrisation would be exercising one code path twice."""
        a = run_pipeline(ContourMapSource(valley_kml()))
        b = run_pipeline(SyntheticRasterSource(bowl_raster()))
        assert a["catchment_ha"] != b["catchment_ha"]


class TestTileNaming:
    """The one brittle part of reading a public bucket: the filename convention.

    Pinned deliberately -- if AWS restructures the bucket, this should fail loudly
    rather than the DEM silently coming back empty (plan risk R1).
    """

    def test_northern_eastern(self) -> None:
        assert tile_name(21.25, 81.30) == "Copernicus_DSM_COG_10_N21_00_E081_00_DEM"

    def test_floors_toward_the_south_west_corner(self) -> None:
        assert tile_name(21.99, 81.99) == "Copernicus_DSM_COG_10_N21_00_E081_00_DEM"
        assert tile_name(22.00, 82.00) == "Copernicus_DSM_COG_10_N22_00_E082_00_DEM"

    def test_southern_and_western_hemispheres(self) -> None:
        assert tile_name(-5.5, -60.5) == "Copernicus_DSM_COG_10_S06_00_W061_00_DEM"

    def test_zero_padding(self) -> None:
        assert tile_name(7.5, 3.5) == "Copernicus_DSM_COG_10_N07_00_E003_00_DEM"

    def test_tiles_covering_a_multi_tile_area(self) -> None:
        from app.providers.elevation.copernicus_aws import tiles_covering

        names = tiles_covering(Bounds(80.5, 20.5, 82.5, 21.5))
        assert len(names) == 6  # 3 longitudes x 2 latitudes
        assert "Copernicus_DSM_COG_10_N20_00_E080_00_DEM" in names
        assert "Copernicus_DSM_COG_10_N21_00_E082_00_DEM" in names


@pytest.mark.network
class TestAgainstTheRealBucket:
    """Live fetch. Deselected by default; run with `-m network`."""

    BOUNDS = Bounds(81.2814, 21.2398, 81.3126, 21.2636)  # the sample's extent

    def test_fetches_a_usable_dem(self) -> None:
        dem = CopernicusDemSource(self.BOUNDS).to_dem()
        pr = dem.provenance
        assert pr["elevation_source"] == "copernicus_dem_glo30"
        assert pr["tiles_used"]
        assert float(pr["coverage_pct"]) > 80.0  # type: ignore[arg-type]
        assert dem.cell_size_m == 30.0
        assert dem.epsg == 32644

    def test_agrees_with_the_contour_map_over_the_same_ground(self) -> None:
        """★ Independent cross-validation of both sources.

        The uploaded survey and a global DEM are produced by entirely different
        means. Agreeing on relief over the same 8.5 km2 is mutual corroboration:
        it is evidence the contour parsing is right *and* that the DEM fetch is
        georeferenced correctly.
        """
        sample = _sample_path()
        if sample is None:
            pytest.skip("sample contours_1m.kml not present (optional)")
        contour = parse_contour_file(sample.read_bytes())
        dem = CopernicusDemSource(contour.bounds).to_dem()
        pr = dem.provenance
        assert float(pr["relief_m"]) == pytest.approx(contour.relief_m, abs=5.0)  # type: ignore[arg-type]
        assert float(pr["elevation_min_m"]) == pytest.approx(  # type: ignore[arg-type]
            contour.levels[0], abs=6.0
        )

    def test_the_pipeline_runs_on_it(self) -> None:
        out = run_pipeline(CopernicusDemSource(self.BOUNDS))
        assert out["sites"]
        assert out["invariant_holds"]
