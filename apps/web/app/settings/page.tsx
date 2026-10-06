"use client";

import { ConnectionsPanel } from "@/components/settings/ConnectionsPanel";
import { PrivacyPanel } from "@/components/settings/PrivacyPanel";
import { useDefaultProject } from "@/lib/useDefaultProject";

export default function Settings() {
  const { project } = useDefaultProject();
  return (
    <div className="grid max-w-2xl gap-8">
      <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>

      <section className="grid gap-1">
        <h2 className="text-lg font-semibold">What QueryDoctor does to the database</h2>
        <ul className="list-disc pl-5 text-ink-2">
          <li>Every query runs in a read-only session with a 10 second limit and is rolled back.</li>
          <li>Only a single SELECT is accepted. Anything that writes or changes the schema is rejected.</li>
          <li>Index ideas are tested as hypothetical indexes first. Real indexes are only created in a throwaway copy of the sample database, and only after you approve a benchmark.</li>
        </ul>
      </section>

      <section aria-labelledby="privacy-h" className="grid gap-2">
        <h2 id="privacy-h" className="text-lg font-semibold">Privacy</h2>
        <PrivacyPanel />
      </section>

      <section aria-labelledby="connections-h" className="grid gap-2">
        <h2 id="connections-h" className="text-lg font-semibold">Your own databases</h2>
        <p className="text-ink-2">
          By default analyses run against QueryDoctor&apos;s sample shop database. An operator can allow connections to PostgreSQL databases you
          register here.
        </p>
        {project && <ConnectionsPanel projectId={project.id} />}
      </section>
    </div>
  );
}
