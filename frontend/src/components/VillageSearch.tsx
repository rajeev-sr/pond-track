import { useCallback, useEffect, useId, useRef, useState } from "react";

import { ApiError, searchVillages } from "../api/client";
import type { VillageMatch } from "../api/types";
import { t } from "../i18n";

interface Props {
  onSelect: (village: VillageMatch) => void;
  selectedId: string | null;
}

/** Long enough that a keystroke does not fire a request, short enough that the
 *  list feels live. Indian village names are short, so waiting for a pause in
 *  typing would make the field feel broken. */
const DEBOUNCE_MS = 220;

/** The API folds a query away below two characters and returns 400. Not sending
 *  it at all is a better answer than showing the user that error. */
const MIN_CHARS = 2;

export function VillageSearch({ onSelect, selectedId }: Props) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<VillageMatch[]>([]);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);

  const abort = useRef<AbortController | null>(null);
  // Set when the query changed because a suggestion was chosen, not typed.
  // Without it, `choose` closes the list and the resulting query change
  // immediately searches again and reopens it, so a click appears to do nothing.
  const chosen = useRef(false);
  const listId = useId();
  const inputId = useId();

  useEffect(() => {
    if (chosen.current) {
      chosen.current = false;
      return;
    }
    const term = query.trim();
    if (term.length < MIN_CHARS) {
      abort.current?.abort();
      setResults([]);
      setNote(null);
      setError(null);
      setBusy(false);
      return;
    }

    const timer = window.setTimeout(() => {
      // Abort the previous request rather than letting it resolve: a slow reply
      // for "kut" arriving after a fast one for "kutela" would overwrite the
      // better result with a worse one.
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      setBusy(true);
      setError(null);

      searchVillages(term, { limit: 8 }, controller.signal)
        .then((found) => {
          setResults(found.results);
          setNote(found.note);
          setActive(found.results.length ? 0 : -1);
          setOpen(true);
        })
        .catch((err: unknown) => {
          if ((err as Error).name === "AbortError") return;
          setResults([]);
          setError(err instanceof ApiError ? err.problem.detail : (err as Error).message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setBusy(false);
        });
    }, DEBOUNCE_MS);

    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => () => abort.current?.abort(), []);

  const choose = useCallback(
    (village: VillageMatch) => {
      onSelect(village);
      chosen.current = true;
      setQuery(village.name);
      setOpen(false);
      setNote(null);
    },
    [onSelect],
  );

  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (!open || !results.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % results.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i - 1 + results.length) % results.length);
    } else if (event.key === "Enter") {
      const chosen = results[active];
      if (chosen) {
        event.preventDefault();
        choose(chosen);
      }
    } else if (event.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <section className="panel" aria-labelledby={`${inputId}-label`}>
      <h2 id={`${inputId}-label`}>{t("village.heading")}</h2>

      {/* A combobox, not a plain input: without the roles a screen reader
          announces neither that suggestions appeared nor which one is active. */}
      <div className="village-search">
        <input
          id={inputId}
          type="search"
          role="combobox"
          aria-expanded={open && results.length > 0}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          autoComplete="off"
          placeholder={t("village.placeholder")}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => results.length && setOpen(true)}
        />
        {busy && <span className="spinner spinner--inline" aria-hidden="true" />}
      </div>
      <p className="small muted">{t("village.hint")}</p>

      {error && (
        <p className="warn-line" role="alert">
          {error}
        </p>
      )}

      {open && results.length > 0 && (
        <ul className="suggestions" id={listId} role="listbox" aria-label={t("village.heading")}>
          {results.map((village, index) => (
            <li
              key={village.id}
              id={`${listId}-${index}`}
              role="option"
              aria-selected={village.id === selectedId}
              className={[
                index === active ? "is-active" : "",
                village.id === selectedId ? "is-chosen" : "",
              ]
                .filter(Boolean)
                .join(" ")}
            >
              <button type="button" onClick={() => choose(village)}>
                <span className="suggestion-name">{village.name}</span>
                <span className="suggestion-where">
                  {[village.hierarchy.subdistrict, village.hierarchy.district]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                {/* Where the hierarchy cannot separate two villages, the
                    Panchayat can: Durg's two Khapris are in `Khapri K` and
                    `Khapri`. Showing it only then keeps the list readable. */}
                {village.hierarchy_is_ambiguous && (
                  <span className="suggestion-disambiguator">
                    {village.gram_panchayats[0]
                      ? `${t("village.panchayat")} ${village.gram_panchayats[0].name}`
                      : `${t("village.code")} ${village.identifiers.census_2011_id ?? "—"}`}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}

      {note && open && (
        <p className="small muted note" role="status">
          {note}
        </p>
      )}
    </section>
  );
}
