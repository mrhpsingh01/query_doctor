"use client";

import { useEffect, useState } from "react";
import { ApiError, api } from "@/lib/api";
import type { DbConnection } from "@/lib/types";

const FIELD = "block w-full rounded-md border border-line bg-surface px-3 py-2 text-sm";
const BUTTON = "rounded-md border px-3 py-1.5 text-sm font-medium disabled:cursor-not-allowed disabled:opacity-60";
const EMPTY = { name: "", host: "", port: "5432", database: "", username: "", password: "", sslmode: "prefer" as "prefer" | "require" };

/** Register databases to analyse. Off unless the server operator enables it; passwords are sent once and never shown again. */
export function ConnectionsPanel({ projectId }: { projectId: number }) {
  const [connections, setConnections] = useState<DbConnection[] | null>(null);
  const [disabled, setDisabled] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [deleting, setDeleting] = useState<number | null>(null);

  useEffect(() => {
    api
      .listConnections(projectId)
      .then(setConnections)
      .catch((e: unknown) => {
        if (e instanceof ApiError && e.status === 403) setDisabled(e.message);
        else setError(e instanceof Error ? e.message : "Connections could not be loaded.");
      });
  }, [projectId]);

  if (disabled) {
    return (
      <div className="grid gap-1 rounded-md border border-line bg-surface p-4 text-sm">
        <p className="font-medium">Custom connections are turned off.</p>
        <p className="text-ink-2">{disabled} Until then, analyses run against QueryDoctor&apos;s sample database.</p>
      </div>
    );
  }
  if (connections === null && !error) return <p className="text-sm text-ink-2">Loading…</p>;

  const set = (key: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm({ ...form, [key]: e.target.value });

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const required = [form.name, form.host, form.database, form.username, form.password];
    if (required.some((v) => v.trim() === "")) {
      setError("Fill in every field.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createConnection(projectId, {
        name: form.name, host: form.host, port: Number(form.port), database: form.database, username: form.username,
        password: form.password, sslmode: form.sslmode,
      });
      setConnections((prev) => [...(prev ?? []), created]);
      setForm(EMPTY); // the password leaves the page as soon as it has been sent
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The connection could not be saved. Try again.");
      setForm({ ...form, password: "" });
    } finally {
      setBusy(false);
    }
  }

  async function remove(id: number) {
    try {
      await api.deleteConnection(id);
      setConnections((prev) => (prev ?? []).filter((c) => c.id !== id));
      setDeleting(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "The connection could not be deleted.");
    }
  }

  return (
    <div className="grid gap-4">
      {error && (
        <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
          {error}
        </p>
      )}
      {(connections ?? []).length > 0 && (
        <ul className="divide-y divide-line rounded-md border border-line bg-surface">
          {(connections ?? []).map((c) => (
            <li key={c.id} className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 text-sm">
              <span>
                <span className="font-medium">{c.name}</span>{" "}
                <span className="text-ink-2">
                  {c.username}@{c.host}:{c.port}/{c.database} · ssl {c.sslmode}
                </span>
              </span>
              {deleting === c.id ? (
                <span className="flex flex-wrap items-center gap-2">
                  <span>Delete this connection and its saved password?</span>
                  <button type="button" className={`${BUTTON} border-heat-3 bg-heat-3 text-accent-ink`} onClick={() => remove(c.id)}>
                    Yes, delete
                  </button>
                  <button type="button" className={`${BUTTON} border-line bg-surface`} onClick={() => setDeleting(null)}>
                    Cancel
                  </button>
                </span>
              ) : (
                <button type="button" aria-label={`Delete ${c.name}`} className={`${BUTTON} border-line bg-surface`} onClick={() => setDeleting(c.id)}>
                  Delete
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={submit} aria-label="Add a connection" className="grid max-w-xl gap-3">
        <p className="text-sm text-ink-2">
          QueryDoctor connects read-only with a short time limit and refuses internal addresses. The password is encrypted and never shown again.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="grid gap-1 text-sm">
            Name
            <input className={FIELD} value={form.name} onChange={set("name")} autoComplete="off" />
          </label>
          <label className="grid gap-1 text-sm">
            Host
            <input className={FIELD} value={form.host} onChange={set("host")} autoComplete="off" />
          </label>
          <label className="grid gap-1 text-sm">
            Port
            <input className={FIELD} value={form.port} onChange={set("port")} inputMode="numeric" />
          </label>
          <label className="grid gap-1 text-sm">
            Database
            <input className={FIELD} value={form.database} onChange={set("database")} autoComplete="off" />
          </label>
          <label className="grid gap-1 text-sm">
            User
            <input className={FIELD} value={form.username} onChange={set("username")} autoComplete="off" />
          </label>
          <label className="grid gap-1 text-sm">
            Password
            <input className={FIELD} type="password" value={form.password} onChange={set("password")} autoComplete="new-password" />
          </label>
          <label className="grid gap-1 text-sm">
            SSL
            <select className={FIELD} value={form.sslmode} onChange={set("sslmode")}>
              <option value="prefer">Prefer</option>
              <option value="require">Require</option>
            </select>
          </label>
        </div>
        <div>
          <button type="submit" disabled={busy} className={`${BUTTON} border-accent bg-accent text-accent-ink`}>
            Add connection
          </button>
        </div>
      </form>
    </div>
  );
}
