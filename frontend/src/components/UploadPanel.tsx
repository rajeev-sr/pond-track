import { useId, useRef, useState } from "react";

import type { AnalyzeOptions } from "../api/types";
import { t } from "../i18n";

interface Props {
  busy: boolean;
  options: AnalyzeOptions;
  onOptionsChange: (options: AnalyzeOptions) => void;
  onAnalyse: (file: File) => void;
  onCancel: () => void;
}

const ACCEPT = ".kml,.kmz,.xml";

export function UploadPanel({ busy, options, onOptionsChange, onAnalyse, onCancel }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement | null>(null);
  const ids = { cell: useId(), sites: useId(), slope: useId(), enrich: useId() };

  const accept = (candidate: File | undefined) => {
    if (candidate) setFile(candidate);
  };

  return (
    <section className="panel" aria-labelledby="upload-heading">
      <h2 id="upload-heading">{t("upload.heading")}</h2>

      <div
        className={`dropzone${dragging ? " dropzone--active" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          accept(e.dataTransfer.files[0]);
        }}
      >
        <p>{file ? file.name : t("upload.prompt")}</p>
        {file && <p className="muted">{(file.size / 1e6).toFixed(1)} MB</p>}
        <button type="button" className="secondary" onClick={() => input.current?.click()}>
          {t("upload.choose")}
        </button>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          className="visually-hidden"
          aria-label={t("upload.choose")}
          onChange={(e) => accept(e.target.files?.[0])}
        />
      </div>

      <details className="options">
        <summary>{t("options.heading")}</summary>

        <label htmlFor={ids.sites}>
          {t("options.maxSites")}
          <span className="value">{options.maxSites}</span>
        </label>
        {/* 25 is the API's own ceiling (`max_sites` is `le=25`); the slider used
            to stop at 10, which looked like the limit and was not. How many are
            actually returned is bounded by the terrain -- ask for 25 on a small
            sheet and you get however many distinct sites clear the score and
            separation thresholds. */}
        <input
          id={ids.sites}
          type="range"
          min={1}
          max={25}
          value={options.maxSites}
          onChange={(e) => onOptionsChange({ ...options, maxSites: Number(e.target.value) })}
        />

        <label htmlFor={ids.slope}>
          {t("options.maxSlope")}
          <span className="value">{options.maxSlopePct}%</span>
        </label>
        <input
          id={ids.slope}
          type="range"
          min={1}
          max={30}
          value={options.maxSlopePct}
          onChange={(e) => onOptionsChange({ ...options, maxSlopePct: Number(e.target.value) })}
        />

        <label htmlFor={ids.cell}>
          {t("options.cellSize")}
          <span className="value">
            {options.cellSizeM === null ? "auto" : `${options.cellSizeM} m`}
          </span>
        </label>
        <input
          id={ids.cell}
          type="range"
          min={0}
          max={30}
          value={options.cellSizeM ?? 0}
          onChange={(e) => {
            const v = Number(e.target.value);
            onOptionsChange({ ...options, cellSizeM: v === 0 ? null : Math.max(1, v) });
          }}
        />
        <p className="muted small">{t("options.cellSizeAuto")}</p>

        <label className="checkbox" htmlFor={ids.enrich}>
          <input
            id={ids.enrich}
            type="checkbox"
            checked={options.enrich}
            onChange={(e) => onOptionsChange({ ...options, enrich: e.target.checked })}
          />
          {t("options.enrich")}
        </label>
        <p className="muted small">{t("options.enrichHint")}</p>
      </details>

      {busy ? (
        <button type="button" className="primary" onClick={onCancel}>
          {t("upload.cancel")}
        </button>
      ) : (
        <button
          type="button"
          className="primary"
          disabled={!file}
          onClick={() => file && onAnalyse(file)}
        >
          {t("upload.analyse")}
        </button>
      )}
    </section>
  );
}
