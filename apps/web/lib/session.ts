let memory: string | null = null;
const KEY = "qd-session";

function makeId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  return Array.from({ length: 24 }, () => Math.floor(Math.random() * 36).toString(36)).join("");
}

/** An anonymous, per-browser id so a vote can be changed but not repeated. Falls back to memory when storage is blocked. */
export function sessionId(): string {
  if (memory) return memory;
  try {
    const stored = window.localStorage.getItem(KEY);
    if (stored) return (memory = stored);
    memory = makeId();
    window.localStorage.setItem(KEY, memory);
  } catch {
    memory ??= makeId();
  }
  return memory;
}
