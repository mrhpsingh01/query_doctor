"use client";

import dynamic from "next/dynamic";
import { useState, useSyncExternalStore } from "react";

interface Props {
  value: string;
  onChange: (value: string) => void;
  /** "json" for a MongoDB query. Default: SQL. */
  language?: "sql" | "json";
}

const FIELD =
  "block w-full rounded-md border border-line bg-surface p-3 font-mono text-[13px] leading-relaxed text-ink";

const LABEL = { sql: "SQL", json: "MongoDB query" } as const;

function PlainEditor({ value, onChange, language = "sql" }: Props) {
  return (
    <textarea
      aria-label={LABEL[language]}
      className={`${FIELD} min-h-[260px]`}
      spellCheck={false}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

// Monaco is loaded on demand (and only in the browser). The plain textarea is the loading state and the fallback
// when the editor cannot be fetched, so the form always works.
const Monaco = dynamic(() => import("@monaco-editor/react"), { ssr: false });

const DARK_QUERY = "(prefers-color-scheme: dark)";
function subscribeDark(callback: () => void) {
  const query = window.matchMedia(DARK_QUERY);
  query.addEventListener("change", callback);
  return () => query.removeEventListener("change", callback);
}
const prefersDark = () => window.matchMedia(DARK_QUERY).matches;

export function SqlEditor({ value, onChange, language = "sql" }: Props) {
  const dark = useSyncExternalStore(subscribeDark, prefersDark, () => false);
  const [ready, setReady] = useState(false);

  return (
    <div className="relative min-h-[260px] overflow-hidden rounded-md border border-line bg-surface">
      {!ready && <PlainEditor value={value} onChange={onChange} language={language} />}
      <div className={ready ? "" : "pointer-events-none absolute inset-0 opacity-0"} aria-hidden={!ready}>
        <Monaco
          height="260px"
          language={language}
          theme={dark ? "vs-dark" : "vs"}
          value={value}
          onMount={() => setReady(true)}
          onChange={(v) => onChange(v ?? "")}
          options={{
            minimap: { enabled: false },
            fontSize: 13,
            fontFamily: "var(--font-code), ui-monospace, monospace",
            scrollBeyondLastLine: false,
            automaticLayout: true,
            padding: { top: 12, bottom: 12 },
            ariaLabel: LABEL[language],
            tabSize: 2,
          }}
        />
      </div>
    </div>
  );
}
