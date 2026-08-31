import en from "./en.json";

/**
 * Strings are externalised from the first commit so Hindi and a regional
 * language can be added without touching a component (HLD NFR-15). The users
 * here are Gram Panchayat staff, not English-first GIS analysts.
 */
const catalogues: Record<string, Record<string, string>> = { en };

let active = "en";

export function setLocale(locale: string): void {
  if (catalogues[locale]) active = locale;
}

export function t(key: keyof typeof en): string {
  return catalogues[active]?.[key] ?? key;
}

/**
 * Look up a key whose absence is meaningful -- the `label.*` overrides that
 * `humanise()` consults before falling back to its generic transform.
 *
 * `t()` cannot serve here: it is typed to the catalogue's own keys and echoes
 * the key back on a miss, so it can never report "this one is not present".
 */
export function lookup(key: string): string | null {
  return catalogues[active]?.[key] ?? null;
}
