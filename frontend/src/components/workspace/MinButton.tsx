interface Props {
  collapsed: boolean;
  onToggle: () => void;
  /** Named in the accessible label, so the control says what it folds. */
  label: string;
}

/** The collapse control on a map overlay.
 *
 *  A hairline square with a minus or plus, matching the drawing's own furniture
 *  rather than looking like a chrome window button. `aria-expanded` carries the
 *  state, because the glyph alone is a visual cue.
 */
export function MinButton({ collapsed, onToggle, label }: Props) {
  return (
    <button
      type="button"
      className="minbtn"
      onClick={onToggle}
      aria-expanded={!collapsed}
      aria-label={`${collapsed ? "Expand" : "Collapse"} ${label}`}
      title={`${collapsed ? "Expand" : "Collapse"} ${label}`}
    >
      <svg width="9" height="9" viewBox="0 0 9 9" aria-hidden="true">
        <line x1="0.5" y1="4.5" x2="8.5" y2="4.5" stroke="currentColor" strokeWidth="1.3" />
        {collapsed && (
          <line x1="4.5" y1="0.5" x2="4.5" y2="8.5" stroke="currentColor" strokeWidth="1.3" />
        )}
      </svg>
    </button>
  );
}
