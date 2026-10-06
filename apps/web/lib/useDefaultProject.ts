"use client";

import { useEffect, useState } from "react";
import { api } from "./api";
import type { Project } from "./types";

/** This release has a single seeded project ("Sample shop"); pages work against the first one. */
export function useDefaultProject(): { project: Project | null; error: string | null } {
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .listProjects()
      .then((projects) => setProject(projects[0] ?? null))
      .catch((e: Error) => setError(e.message));
  }, []);
  return { project, error };
}
