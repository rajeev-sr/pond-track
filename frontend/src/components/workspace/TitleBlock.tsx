import { num } from "../../format";
import type { ContourAnalysis } from "../../api/types";
import { useCollapsed } from "../../state/collapsed";
import { MinButton } from "./MinButton";

/** The title block a survey plate carries, bottom-right: which sheet, at what
 *  resolution, in which coordinate system. Every value is read from the analysis
 *  — the CRS in particular is derived from the sheet's own longitude, so printing
 *  a fixed one would be a lie about how the tool works. */
export function TitleBlock({ analysis }: { analysis: ContourAnalysis | null }) {
  const [collapsed, toggle] = useCollapsed("titleblock");
  if (!analysis) return null;
  const map = analysis.contour_map;
  const grid = analysis.interpolated_terrain;
  return (
    <div className="titleblock">
      {!collapsed && (
        <>
      <div>
        <span className="stamp">Grid</span>
        <span className="val">
          {grid.grid_size[0]} × {grid.grid_size[1]} @ {num(grid.grid_resolution_m, 1)} m
        </span>
      </div>
      <div>
        <span className="stamp">Interval</span>
        <span className="val">
          {map.contour_interval_m != null ? `${num(map.contour_interval_m, 1)} m` : "—"}
        </span>
      </div>
      <div>
        <span className="stamp">CRS</span>
        <span className="val">EPSG:{map.working_crs_epsg}</span>
      </div>
        </>
      )}
      <div className="tb-toggle">
        {/* Collapsed, the block is a single control. Without a label it is an
            anonymous button sitting next to the basemap attribution, so it keeps
            its name. */}
        {collapsed && <span className="stamp">Sheet</span>}
        <MinButton collapsed={collapsed} onToggle={toggle} label="the title block" />
      </div>
    </div>
  );
}
