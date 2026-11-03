# C48 handoff

Branch: `razeef/c48-mcp-gateway`, ready for Claude's required boundary review.
Started on `1303ad2`; includes current main `9eefa6e` through feature-branch
merge `60a41d9`. Nothing was merged into main.

Feature commits:

- `610ccaf`: shared binding and persisted-row characterization.
- `6f1a30f`: exact MCP capability set, transports, boundary tests and capture script.
- `abe5bc8`: contracts, setup/video instructions and actual recorded evidence.
- `60a41d9`: upstream C52 approval-test clock fix, unchanged from main.

Verified checks:

- **23 passed**: 19 gateway cases plus four original-binding row comparisons.
- **43 passed**: unchanged engine/agent harness tests after extraction.
- **28 passed**: complete affected approval test file after including C52.
- **70 passed**: Node web tests.
- Ruff clean; **four import contracts kept, zero broken**.
- Installed dependency consistency passed; public evidence JSON privacy scan passed.
- Three real browser captures: light, dark, 390px phone, reduced motion,
  visible simulation banner, no horizontal overflow or JavaScript exceptions.

The broad Python run was intentionally stopped at 21% at the owner's request
to finish with feature and important checks only. Its earlier date-dependent
owner-test failure was resolved by the upstream C52 fix and the 28-test rerun.
**A completed full-suite pass is not claimed.**

Both required test environment settings were supplied; all database tests used
`countersign_c48`. Real MCP SDK clients exercised initialize, tool discovery,
blocked alternate refund destinations, preview/record stdio and forced-preview
HTTP on 8191. Record-mode owner decisions were checked through the dashboard
API, rendered in the decision page, approved through the existing owner endpoint,
then refused by the normal executor after a bound record changed. The public
captures use a synthetic $540 fixture; the video recipe's $60 claim is separate.
No real model, Claude Desktop, sandbox or production PayPal test is claimed.

Existing/shared files touched:

| File | Location / purpose |
| --- | --- |
| `README.md` | Appended the exact requested MCP setup section |
| `pyproject.toml` | Exact SDK pin and fourth import contract |
| `docs/CONTRACTS.md` | Version 1.0.7 and additive gateway section 7 |
| `docs/DECISIONS.md` | Appended Nov 3 C48 decisions |
| `docs/AGENT_STATE.md` | C48 current-work summary |
| `docs/AGENT_FUTURE_NOTES.md` | C48 reusable implementation and verification notes |
| `docs/walkthrough/c48-mcp-gateway.md` | New local/hosted video setup recipe |
| `src/countersign/harness.py` | Thin wrapper calling the extracted binding |
| `src/countersign/main.py` | Guarded MCP mount and transport lifespan |
| `tests/integration/approvals/test_approvals.py` | Seven upstream C52 test-clock lines brought in from main |

All other C48 implementation, tests and artifacts are new files. The shield,
executor and staged week retain their existing behavior. No extra approval was
requested. HTTP preview cannot create a dashboard decision; local stdio record
is required for that shot. Payout proposals reuse an existing planned manifest.
