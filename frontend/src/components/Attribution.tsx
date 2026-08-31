import { t } from "../i18n";

/**
 * Data attribution.
 *
 * Every source below requires attribution as a condition of use: Esri and
 * OpenStreetMap for the basemaps, CC BY 4.0 for WorldCover and SoilGrids,
 * and the Copernicus licence for the DEM and the ERA5-Land reanalysis behind
 * the rainfall series. Listing them is a licence obligation, not a courtesy,
 * so this block renders whether or not an analysis has run.
 *
 * The names and licences are held here as data rather than in the translation
 * catalogue on purpose: they are legal text that must appear verbatim, and a
 * translator editing "OpenStreetMap contributors" into another language would
 * break the attribution rather than localise it. Only the heading is
 * translated.
 */
interface Source {
  name: string;
  what: string;
  licence: string;
  href: string;
}

const SOURCES: Source[] = [
  {
    name: "Esri World Imagery",
    what: "satellite basemap",
    licence: "© Esri, Maxar, Earthstar Geographics",
    href: "https://www.arcgis.com/home/item.html?id=10df2279f9684e4a9f6a7f08febac2a9",
  },
  {
    name: "OpenStreetMap",
    what: "street basemap",
    licence: "© OpenStreetMap contributors, ODbL",
    href: "https://www.openstreetmap.org/copyright",
  },
  {
    name: "ESA WorldCover 10 m v200",
    what: "land use / land cover",
    licence: "CC BY 4.0",
    href: "https://esa-worldcover.org/",
  },
  {
    name: "ISRIC SoilGrids v2",
    what: "soil texture → hydrologic soil group",
    licence: "CC BY 4.0",
    href: "https://soilgrids.org/",
  },
  {
    name: "Open-Meteo / ERA5-Land",
    what: "30-year daily rainfall",
    licence: "Copernicus Climate Change Service (C3S)",
    href: "https://open-meteo.com/",
  },
  {
    name: "Copernicus DEM GLO-30",
    what: "cross-check elevation",
    licence: "© ESA, DLR e.V., Airbus DS",
    href: "https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model",
  },
];

export function Attribution() {
  return (
    <section className="panel attribution" aria-labelledby="attribution-heading">
      <h2 id="attribution-heading">{t("attribution.heading")}</h2>
      <dl className="sources">
        {SOURCES.map((source) => (
          <div key={source.name}>
            <dt>
              <a href={source.href} target="_blank" rel="noreferrer noopener">
                {source.name}
              </a>
            </dt>
            <dd>
              <span className="muted">{source.what}</span>
              <span className="small muted"> · {source.licence}</span>
            </dd>
          </div>
        ))}
      </dl>
      <p className="small muted">{t("attribution.note")}</p>
    </section>
  );
}
