# Stage-one repeat: offline preflight

The owner authorized one repeat after the startup-warning parser fix. The first
failed recording remains unchanged. This folder contains offline preflight
evidence; the authenticated outcome belongs in the new run recording.

`read-tool-schemas.json` contains the eight real allowlisted read schemas. Five
were obtained from the pinned toolkit's local metadata using the isolated
interpreter, with socket connection functions denied. Three came from the current
application definitions. No application read, PayPal call or model call occurred.

Five schemas have optional properties and therefore fail the strict requirement
that every property be required. All original objects already disallow extra
properties. The output-only adapter makes optional arguments required with this
encoding: `null` omits the argument; `{"value": ...}` passes its value, including
explicit null. Decoding revalidates the original schema and preserves defaults.
The actual tool definitions and allowlist are unchanged.

`offline-audit.json` records the per-tool audit, transformed-schema checks and
unauthenticated loopback wire proof: zero native tools. This proof does not
establish what happens in an authenticated run. The strict rules follow the
[official structured-output documentation](https://developers.openai.com/api/docs/guides/structured-outputs).

Startup error items are counted only before `turn.started`; their text is never
retained. Error items after turn start abort with MODEL_CLI_ERROR_ITEM. A native
tool item aborts at any point, including before turn start. A fixed known type or
`unknown` is retained on rejection, without the item's contents.

Preflight: 50 focused parser, runner and schema tests passed; scoped Ruff passed.
The repeat has a three-turn cap, no retry and no later stage.
