"use client";

import { useState } from "react";
import type { Engine, SampleQuery } from "@/lib/types";
import { ExecuteWarning } from "./ExecuteWarning";
import { SamplePicker } from "./SamplePicker";
import { SqlEditor } from "./SqlEditor";

export interface QuerySubmission {
  sql: string;
  params: unknown[];
  execute: boolean;
  engine: Engine;
}

const ENGINES: { value: Engine; label: string }[] = [
  { value: "postgres", label: "PostgreSQL" },
  { value: "mongodb", label: "MongoDB" },
];

const MONGO_HINT = 'One find or aggregate as JSON, for example {"find": "orders", "filter": {"customer_id": {"$param": 1}}}. Mark each value with {"$param": 1}, {"$param": 2}… and list them below.';
const engineOf = (s: SampleQuery): Engine => s.engine ?? "postgres";

interface Props {
  /** Samples of every engine; the editor shows the ones of the selected engine. */
  samples: SampleQuery[];
  onSubmit: (submission: QuerySubmission) => void;
  busy?: boolean;
  /** An error from the server for the last submission. */
  error?: string | null;
  initialSql?: string;
  /** The parameters field's text (a JSON array) when opening with SQL that is not a catalog sample. */
  initialParams?: string;
  initialSampleId?: string | null;
  /** The engine to open with (a linked MongoDB sample implies MongoDB). Default: PostgreSQL. */
  initialEngine?: Engine;
  onEngineChange?: (engine: Engine) => void;
}

const FIELD = "block w-full rounded-md border border-line bg-surface px-3 py-2 font-mono text-[13px] text-ink";

export function QueryEditor({
  samples, onSubmit, busy = false, error = null, initialSql = "", initialParams = "[]", initialSampleId, initialEngine, onEngineChange,
}: Props) {
  const initialSample = samples.find((s) => s.id === initialSampleId);
  const [engine, setEngine] = useState<Engine>(initialSample ? engineOf(initialSample) : (initialEngine ?? "postgres"));
  const [sql, setSql] = useState(initialSample?.sql ?? initialSql);
  const [paramsText, setParamsText] = useState(initialSample ? JSON.stringify(initialSample.params) : initialParams);
  const [execute, setExecute] = useState(false);
  const [paramsError, setParamsError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(initialSample?.id ?? null);

  function chooseEngine(next: Engine) {
    if (next === engine) return;
    setEngine(next);
    setSql(""); // a query of the other engine would only be an error
    setParamsText("[]");
    setParamsError(null);
    setSelected(null);
    onEngineChange?.(next);
  }

  function pick(sample: SampleQuery) {
    setSql(sample.sql);
    setParamsText(JSON.stringify(sample.params));
    setParamsError(null);
    setSelected(sample.id);
  }

  function submit(event: React.FormEvent) {
    event.preventDefault();
    let params: unknown;
    try {
      params = JSON.parse(paramsText.trim() === "" ? "[]" : paramsText);
    } catch {
      params = null;
    }
    if (!Array.isArray(params)) {
      setParamsError('Parameters must be a JSON array, for example [42, "2024-01-01"].');
      return;
    }
    setParamsError(null);
    onSubmit({ sql, params, execute, engine });
  }

  const message = paramsError ?? error;
  const label = busy ? "Analyzing…" : execute ? "Analyze and run query" : "Analyze query";
  const mongo = engine === "mongodb";
  const shown = samples.filter((s) => engineOf(s) === engine);

  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <form onSubmit={submit} className="grid gap-4" aria-label="New analysis">
        <fieldset className="grid gap-2">
          <legend className="mb-1 text-sm font-semibold">Database</legend>
          <div className="flex flex-wrap gap-2">
            {ENGINES.map((option) => (
              <label
                key={option.value}
                className={`flex cursor-pointer items-center gap-2 rounded-md border px-3 py-2 text-sm ${
                  engine === option.value ? "border-accent bg-accent-soft" : "border-line bg-surface"
                }`}
              >
                <input type="radio" name="engine" checked={engine === option.value} onChange={() => chooseEngine(option.value)} />
                <span className="font-medium">{option.label}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div>
          <label className="mb-1.5 block text-sm font-semibold" htmlFor="sql-editor">
            {mongo ? "MongoDB query (JSON)" : "SQL query"}
          </label>
          <div id="sql-editor">
            <SqlEditor value={sql} onChange={setSql} language={mongo ? "json" : "sql"} />
          </div>
          <p className="mt-1.5 text-xs text-ink-2">
            {mongo ? MONGO_HINT : "One SELECT statement. Use $1, $2… for values and list them below."}
          </p>
        </div>

        <div>
          <label className="mb-1.5 block text-sm font-semibold" htmlFor="params">
            Parameters (JSON array)
          </label>
          <input
            id="params"
            className={FIELD}
            spellCheck={false}
            value={paramsText}
            onChange={(e) => setParamsText(e.target.value)}
          />
        </div>

        <fieldset className="grid gap-2">
          <legend className="mb-1 text-sm font-semibold">How to analyze</legend>
          <div className="flex flex-wrap gap-2">
            {[
              { value: false, label: "Explain only", hint: "Plans the query without running it" },
              { value: true, label: "Execute with analysis", hint: "Runs it to measure real timings" },
            ].map((option) => (
              <label
                key={option.label}
                className={`flex cursor-pointer items-start gap-2 rounded-md border px-3 py-2 text-sm ${
                  execute === option.value ? "border-accent bg-accent-soft" : "border-line bg-surface"
                }`}
              >
                <input
                  type="radio"
                  name="mode"
                  className="mt-1"
                  checked={execute === option.value}
                  onChange={() => setExecute(option.value)}
                />
                <span>
                  <span className="block font-medium">{option.label}</span>
                  <span className="block text-xs text-ink-2">{option.hint}</span>
                </span>
              </label>
            ))}
          </div>
          {execute && <ExecuteWarning engine={engine} />}
        </fieldset>

        {message && (
          <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
            {message}
          </p>
        )}

        <div>
          <button
            type="submit"
            disabled={busy || sql.trim() === ""}
            className="rounded-md bg-accent px-4 py-2 text-sm font-semibold text-accent-ink disabled:cursor-not-allowed disabled:opacity-50"
          >
            {label}
          </button>
        </div>
      </form>

      <aside>
        <SamplePicker samples={shown} onPick={pick} selectedId={selected} />
      </aside>
    </div>
  );
}
