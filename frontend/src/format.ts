import { lookup } from "./i18n";

/** Number formatting shared across the UI. Units always accompany a value. */

const nf = (digits: number) =>
  new Intl.NumberFormat("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });

export const num = (value: number, digits = 0): string => nf(digits).format(value);

export function area(hectares: number): string {
  return hectares >= 100 ? `${num(hectares / 100, 2)} km²` : `${num(hectares, 2)} ha`;
}

export function volume(cubicMetres: number): string {
  return cubicMetres >= 1_000_000
    ? `${num(cubicMetres / 1_000_000, 2)} million m³`
    : `${num(cubicMetres)} m³`;
}

export function rupees(value: number): string {
  // Indian numbering: ₹12,04,810 rather than ₹1,204,810.
  return `₹${new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value)}`;
}

export function distance(metres: number): string {
  return metres >= 1000 ? `${num(metres / 1000, 2)} km` : `${num(metres)} m`;
}

/**
 * Turn a backend identifier into something a person reads.
 *
 * Most enum values de-underscore cleanly, so the generic transform is the
 * default and a new backend value renders sensibly without a frontend change.
 * The dictionary is only for the handful the transform gets wrong -- acronyms
 * ("lulc", "usda") and names that need punctuation the identifier can't carry.
 */
export function humanise(key: string): string {
  const label = lookup(`label.${key}`);
  if (label !== null) return label;
  return key.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
