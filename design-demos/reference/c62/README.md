# C62 Friday checkpoint: verification

Branch: `razeef/c62-friday-checkpoint`, based on `75c95cb`. No merge.

Code commits: `6b1ddfd` mock state and timers; `68b3de6` builder, restore,
staged-time projections and focused tests; `27b012b` guidance and docs;
`5c1d657` unique integration test module name.

## Checks actually run

- New focused Python files only: **20 passed**, including **4 integration tests**
  on live PostgreSQL 16 in owned database `countersign_c62` inside our isolated
  Docker container (socket port 55433). No full or directory-wide pytest.
- `node --test web/tests/*.test.mjs`: **127 passed**. Changed JS passes `node --check`.
- `ruff check .`: clean. `lint-imports`: **4 kept, 0 broken**.
- Docker image built **once**, `countersign-c62:friday`. A direct-script rehearsal
  on the previous image caught build/boot problems before this final build.
- Combined collection caught two new test modules sharing a basename. Renamed
  the integration module to `tests/integration/test_friday_replay_restore.py`,
  then all 20 passed together in the built image.

## Hosted checks

| Check | Result |
| --- | --- |
| Friday boot, unrestricted CPU | 17.188 s, unsigned step 6 |
| Friday boot, `--cpus=1` | 12.032 s, unsigned step 6 |
| Plain approve | 201 in both runs |
| Real scripted step 7 | passed after plain approval |
| Back to Friday, unrestricted CPU | 0.734 s |
| Back to Friday, `--cpus=1` | 0.985 s |
| Monday boot, same image | 19.078 s, empty step 0 |

Timings include Docker launch/readiness polling, or POST plus polling to the
confirmed unsigned state. One measurement each; cache/load differ between runs.
The one-CPU restore is below the 10-second target. The app pool stays open.

Docker `image inspect .Size`: **238,402,125 → 250,255,901 bytes**
(238.40 → 250.26 MB, +11.85 MB).
The baseline is the existing `countersign-main:c61` image, not a separately
rebuilt controlled baseline. `hosted.json` retains the measurements.

## Clock and restore decisions

- Approval-request expiry, record versions, hashes and dispute deadlines retain
  their staged dates; the existing C55 decision clock handles approval. Both
  plain approval and real P-256 WebAuthn approval pass with real time 45 days later.
- Passkey challenges still expire two real minutes after issuance. Restore clears
  challenges and preserves registered keys and increasing signature counters.
- Mock tokens/timers/signature history retain staged state. Clear the shared
  HTTP token cache after restore so a later token cannot refer to reset state.
- Cash age/stale and maker 30-day totals use staged time in demo projections.
  A balance already stale in the original Friday remains honestly stale; it is
  not artificially marked fresh. Live projections remain unchanged.
- Webhook registrations/signing state survive the round trip; only the local
  callback address is rebound to the boot's app port. No new wall-clock webhook
  window was introduced.
- One constrained DB transaction restores the fixed tables; no trigger is disabled.
  Owner POSTs and demo commands share the gate. Idle uses monotonic activity time
  and waits for owner operations and unexpired passkey prompts. A possible lost
  commit response fails closed until container restart.

## Browser evidence

`story-light.png`, `story-dark.png`, `story-phone.png`, `end-light.png`,
`end-dark.png`, `end-phone.png` show the actual hosted app at 1440×1000 and
390×844. Reduced motion and capture-only animation disabling keep screenshots
stable. The guide is server-confirmed; screenshots are not new sandbox/model proof.
`browser.json` records text, geometry and runtime exceptions. Some asynchronous
supporting widgets may still be loading when the guide is captured.

## Approved scope and limits

Owner approved the written contract bump **1.0.8 → 1.0.9**. The existing
advertised dashboard metadata stays **1.0.6**. No other approval was needed.
No `.env` values read, no real PayPal/model calls, no dependencies added,
no `web/styles.css` edits, no protected ports or other databases changed.
Preview: `http://localhost:8198` (dedicated one-CPU container).

## Existing files changed

New implementation/tests are in dedicated Friday files. Existing-file insertions
are listed below; numbers are new-side hunk starts for review, relative to base.

| Existing file | Changed line starts |
| --- | --- |
| `Dockerfile` | 47 |
| `README.md` | 17, 379, 394 |
| `docs/AGENT_FUTURE_NOTES.md` | 748 |
| `docs/AGENT_STATE.md` | 3, 14 |
| `docs/CONTRACTS.md` | 3, 444 |
| `docs/DECISIONS.md` | 633 |
| `docs/submission/CHECKLIST.md` | 79 |
| `docs/submission/DEVPOST.md` | 186 |
| `mock_paypal/app.py` | 106 |
| `mock_paypal/clock.py` | 24, 27, 30, 35, 45 |
| `mock_paypal/payouts.py` | 98, 147, 155, 268 |
| `mock_paypal/resources.py` | 140, 143, 354, 369 |
| `mock_paypal/webhooks.py` | 162, 164 |
| `scripts/demo_week.py` | 28, 1666 |
| `scripts/hosted_start.sh` | 27, 35, 86, 113, 125, 131, 137, 145, 159 |
| `src/countersign/api/dashboard.py` | 523, 620, 959, 1039, 1066, 1126, 1138, 1153 |
| `src/countersign/main.py` | 145, 184, 202 |
| `src/countersign/paypal/http.py` | 176 |
| `web/app.mjs` | 76 |
| `web/demo.mjs` | 12, 14, 104 |
| `web/guide.mjs` | 4, 40, 44, 47, 71, 82 |
| `web/index.html` | 20 |
