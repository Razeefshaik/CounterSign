# C48 evidence

Real official MCP SDK 2.3.0 client sessions against the new gateway, with
simulated PayPal and isolated PostgreSQL test schemas in `countersign_c48`.
No real LLM, Claude Desktop, sandbox or production PayPal session is claimed.

- `http.json`: SDK initialize, exact tool list and an injected alternate refund
  destination blocked over TCP 8191. Environment requested record; HTTP forced
  preview. No persistent writes.
- `stdio-preview.json` / `stdio-record.json`: same blocked request through the
  actual `python -m countersign.gateway` subprocess. Only record stores it.
- `stdio-preview-owner.json` / `stdio-record-owner.json`: the plain refund
  request returns NEEDS_OWNER. Preview stores nothing; record returns a decision
  route. The test verifies that the stored model comes from MCP initialize data.
- `decision-light.png`, `decision-dark.png`, `decision-phone.png`: actual
  dashboard decision backed by a recorded external proposal through TCP 8191.
  This independent test fixture uses a **$540** claim; the video walkthrough's
  **$60** example requires the corresponding staged dispute. Images are not
  fabricated and do not establish completed execution.
- `browser.json`: three viewport/theme assertions, visible simulation label,
  no horizontal overflow and zero JavaScript runtime exceptions. Reduced motion
  is enabled. The capture script sends no owner actions. After capture, the test
  approves via the real owner HTTP route, changes a bound record, and verifies
  that the ordinary executor refuses it without a mock refund.

Reproduce with both database/toolkit variables from the parallel-task rules and
`COUNTERSIGN_C48_RECORD_EVIDENCE=1`, then run the gateway integration tests.
Browser capture uses the existing Windows Chrome installation and Node's builtin
CDP client; no project dependency was added for screenshots. Each test closes
its port 8191 listener and deletes its disposable database schema. Transcripts
contain sanitized responses, not private configuration or raw requests.
