import { useCallback, useState } from "react";

const PREFIX = "contour.collapsed.";

/**
 * Whether one map overlay is collapsed, remembered per viewer.
 *
 * Kept in `localStorage` rather than in component state because the panels
 * unmount: navigating to the method page and back would otherwise reopen a
 * legend the reader had deliberately folded away, every time.
 *
 * Every access is guarded. `localStorage` is not merely empty in a private
 * window or with site data blocked — the accessor itself throws, and an overlay
 * on a map is not worth taking the page down for.
 */
export function useCollapsed(
  key: string,
  initial = false,
): [boolean, () => void, (value: boolean) => void] {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const stored = window.localStorage.getItem(PREFIX + key);
      return stored === null ? initial : stored === "1";
    } catch {
      return initial;
    }
  });

  const write = useCallback(
    (value: boolean) => {
      setCollapsed(value);
      try {
        window.localStorage.setItem(PREFIX + key, value ? "1" : "0");
      } catch {
        // A viewer who cannot store the preference still gets the toggle for
        // this session; only its persistence is lost.
      }
    },
    [key],
  );

  const toggle = useCallback(() => write(!collapsed), [write, collapsed]);

  return [collapsed, toggle, write];
}
