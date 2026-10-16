# Standalone showcase

From this directory run `npm ci` then `npm run dev`. Open http://localhost:3000.
The landing page, Paste EXPLAIN Output tab and the recorded Workload tab (a real capture with its Ranked Fix Queue) need no API, database, Redis or worker.
For Vercel, set the Root Directory to `apps/web`; `vercel.json` selects the Next.js preset. No environment variables are required (see `.env.example`).
Public showcase: [query-doctor-omega.vercel.app](https://query-doctor-omega.vercel.app/), deployed under `mrhpsingh01s-projects/query-doctor` from `main`.

The four cards replay real PostgreSQL 16 / MongoDB 7 pipeline captures in `lib/showcaseCaptured.json`.
Regenerate with `scripts/capture_showcase.py` from the repository root against disposable instances.
Each card provides raw downloadable evidence, provenance, planner comparisons, run distributions and
checksum outcomes. Both engines record counts plus SHA-256 digests; MongoDB hashes documents inside the server
and exports only hashes. The rejected case retains planner evidence and never earns benchmark validation.

Pasted plans stay in browser memory. JSON/text parsing is bounded to 1 MB and 400 operators / 48 levels.
Index ideas are heuristic and always unverified. PostgreSQL text parsing supports standard cost/actual
operator lines; JSON is preferable for complete metadata. MongoDB times may be cumulative estimates.

Live mode mounts the existing API-backed query runner. Configure `QD_API_URL` for server rewrites as
explained in `next.config.ts`. The full stack is started from the repository root with `bash run_demo.sh`.


Vercel automatically defaults to `NEXT_PUBLIC_QD_DEMO_MODE=true`. This restricts navigation to the showcase,
paste analyzer, recorded Workload and local setup guide; backend-only routes redirect home and live-query links cannot start API calls.
Set the variable explicitly to `false` when deploying a fully hosted API. To verify the Vercel mode locally:

```bash
NEXT_PUBLIC_QD_DEMO_MODE=true npm run build
npm run start -- --port 3110
QD_WEB_PORT=3110 QD_EXPECT_SHOWCASE_ONLY=true npx playwright test e2e/showcase-deployment.spec.ts
```
