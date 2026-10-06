"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PrivacyInfo } from "@/lib/types";

function List({ id, title, items }: { id: string; title: string; items: string[] }) {
  return (
    <section aria-labelledby={id} className="grid gap-1">
      <h3 id={id} className="font-semibold">
        {title}
      </h3>
      <ul className="list-disc pl-5 text-ink-2">
        {items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </section>
  );
}

/** What QueryDoctor keeps about your queries, read from the server so the page cannot drift from the code. */
export function PrivacyPanel() {
  const [info, setInfo] = useState<PrivacyInfo | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getPrivacy().then(setInfo).catch((e: Error) => setError(e.message));
  }, []);

  if (error) {
    return (
      <p role="alert" className="rounded-md border border-heat-3 bg-heat-3-soft px-3 py-2 text-sm">
        {error}
      </p>
    );
  }
  if (!info) return <p className="text-sm text-ink-2">Loading…</p>;
  return (
    <div className="grid gap-4">
      <List id="stored-h" title="What is stored" items={info.stores} />
      <List id="never-h" title="What is never stored" items={info.never_stores} />
      <List id="delete-h" title="Deleting your data" items={info.deletion} />
      <p className="text-sm text-ink-2">Custom connections are {info.custom_connections_enabled ? "on" : "off"} on this server.</p>
    </div>
  );
}
