# C65 PayPal calls: review evidence

Branch: `razeef/c65-paypal-calls`, based on `origin/main` at `745c9b4` (includes C62).
No merge. The branch is for owner review.

## Implementation commits

- `aaefb84` — safe bounded mock calls and outbound webhook deliveries.
- `f558e40` — reproducible coverage from every numbered sandbox recording.
- `d017adc` — guarded read-only dashboard API.
- `29df3c7` — product counts, two Community grids, retry highlighting and guide links.
- `050b566` — fail safely on malformed provider errors without exposing their values.

The live page reads simulated evidence through the dashboard; it never contacts
the mock directly. The separate recorded grid links to the public sandbox files.
The detail log retains 500 rows; cumulative product counters retain the week total.

## Checks actually run

- `pytest tests/mock_paypal/test_call_log_c65.py tests/unit/test_paypal_coverage_c65.py tests/dashboard/test_paypal_calls_api_c65.py`: **13 passed**, 3.78 seconds.
- `node --test web/tests/*.test.mjs`: **132 passed**, zero failures/skips, 2.69 seconds.
- `ruff check .`: clean. `lint-imports`: **4 contracts kept, 0 broken**.
- Node syntax checks on the new page and workspace controller: passed.
- `python -m scripts.build_paypal_coverage --check`: **1,041 attempts**, including **34 transport errors**. All D1–D13 counts agree with the recording index.
- `git diff --check`: passed. Public evidence contains no Authorization or access-token fields.
- No full or directory-wide Python suite; integration/full validation remains for merge.

One final Docker build reused the installed `countersign-c62:friday` runtime,
copied this branch's source, and regenerated the Friday artifact from scripted
steps 1–6. Image: `countersign-c65:validation`. Validation ran on own port **8199**,
limited to **1 CPU / 768 MB**, with an internal PostgreSQL 16 database. No shared
host database or other worktree was changed.

The baked Friday log contained **91 calls**. After approval, step 7 recorded the
lost **201** response and the **400** duplicate pointing to the original batch,
with identical request and sender-key hashes. Back to Friday restored the whole
log exactly in **1.765 seconds**. All eleven staged steps completed. The initial
validation client disconnected once during step 7; the app completed that step,
and retrying the read confirmed success. The container had no OOM or restart.
See [hosted.json](hosted.json) and the three safe call-log JSON snapshots.

Actual hosted-browser screenshots: [light](calls-light.png), [dark](calls-dark.png),
and [390px phone](calls-phone.png). All were inspected; both grids, eight product
tiles and the 1,041 total loaded, with **no horizontal page overflow or uncaught
runtime exceptions**. The retry filter isolates the two matching payout POSTs.
Wide grid columns scroll within the grid on small screens. Reduced motion was on.
See [browser.json](browser.json). The existing glass theme, rim and demo dock were
preserved. The design detector's inherited-theme findings were not treated as
permission to redesign shared styling.

The owned validation container was stopped and removed after these checks to
release memory. The other task's `countersign-c67-postgres` was left running.
The validation image remains available; no Docker prune or volume deletion ran.

## Counts from evidence

| Product | Completed simulated week | Recorded sandbox attempts |
| --- | ---: | ---: |
| Payouts | 81 | 65 |
| Disputes | 61 | 30 |
| Orders | 0 | 13 |
| Payments | 2 | 111 |
| Tracking | 1 | 2 |
| Reporting | 19 | 736 |
| Webhooks | 52 | 53 |
| OAuth | 15 | 31 |
| **Total** | **231** | **1,041** |

These are different sources, not comparative performance measurements. The mock
week stages orders through its excluded control surface; it records no Orders
API requests in this replay. Webhooks includes outbound deliveries as well as
API requests. No sandbox or model was called during this task.

## Existing files touched

Line numbers refer to this branch's implementation commits; each insertion is
marked C65. New feature files hold the implementation.

| Existing file | Lines | Change |
| --- | --- | --- |
| `mock_paypal/app.py` | 280–284 | Install call router and outer logging middleware |
| `mock_paypal/store.py` | 5, 37–40 | Bounded log and cumulative counters, cleared by reset |
| `mock_paypal/webhooks.py` | 193–196 | Safe outbound delivery evidence |
| `src/countersign/api/dashboard.py` | 1118–1120 | Include guarded read-only router |
| `web/index.html` | 18, 69 | Scoped stylesheet and Records navigation |
| `web/workspace.mjs` | 10, 17, 30, 38, 62, 67–68 | Page mount, title and disposal |
| `web/guide.mjs` | 21–22 | Evidence links on steps 1, 5, 7, 8 and 11 |
| `docs/CONTRACTS.md` | 3, 474 onward | Approved written version 1.0.10 and route contract |
| `docs/DECISIONS.md` | 653 onward | C65 design reasons |
| `docs/AGENT_STATE.md`, `docs/AGENT_FUTURE_NOTES.md` | C65 sections | Required context handoff |

No dependencies, `.env` reads, real PayPal/LLM calls, write tools, `styles.css`,
`grid.mjs`, root README or submission-document edits. No blockers remain.

## Approval and suggested README text

The owner approved documenting `GET /api/v1/demo/paypal-calls`, demo-only and
read-only, with written contract **1.0.10**. Advertised dashboard version stays
**1.0.6**. No additional scope approval was needed.

Suggested README addition (not applied):

> Open **Records → PayPal calls** to inspect simulated calls, hashed-key retries and product counts.
> The separate sandbox grid links to all **1,041 recorded attempts** across D1–D13.
> Financial writes remain inside the executor's shield-gated adapter.
