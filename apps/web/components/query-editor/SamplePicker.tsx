import type { SampleQuery } from "@/lib/types";

interface Props {
  samples: SampleQuery[];
  onPick: (sample: SampleQuery) => void;
  selectedId?: string | null;
}

export function SamplePicker({ samples, onPick, selectedId }: Props) {
  if (samples.length === 0) return null;
  return (
    <section aria-labelledby="samples-heading">
      <h2 id="samples-heading" className="mb-2 text-sm font-semibold">
        Start from a slow query
      </h2>
      <ul className="grid gap-1.5">
        {samples.map((sample) => (
          <li key={sample.id}>
            <button
              type="button"
              onClick={() => onPick(sample)}
              aria-pressed={selectedId === sample.id}
              className="w-full rounded-md border border-line bg-surface px-3 py-2 text-left hover:border-accent aria-pressed:border-accent aria-pressed:bg-accent-soft"
            >
              <span className="block text-sm font-medium">{sample.title}</span>
              <span className="block text-xs text-ink-2">{sample.description}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
