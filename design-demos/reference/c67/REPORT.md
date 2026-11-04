# C67: real AI agent evidence

The owner's later addendum supersedes the provider plan in this historical report.
Current stage-one status is in [STAGE1.md](STAGE1.md). No new week or evaluation
has run. Public headings use AI agent; identity belongs in linked Run details.

## Prediction, recorded before any model call

1. CounterSign scored 170/240 with the model disabled because eleven failing scenarios needed agent-proposed evidence, refunds or cancellation; three other scenarios had deterministic follow-ups. Rules-only scored 215/240.
2. Required final actions drive most of the gap: CounterSign met that check in 175/240 versus rules-only's 230/240; task completion was 205/240 versus 220/240. All four measured invariant totals were zero for both systems.
3. Enabling Gemini may supply those missing actions and improve completion, but neither a higher score nor beating rules-only is guaranteed. Scenarios, prompts, grading and weights stay fixed, and every outcome will be published.

## Preparation

- Branch `razeef/c67-real-gemini`, base `745c9b4` including C62, isolated `CounterSign-c67` worktree.
- Public OpenRouter catalog lists exact `google/gemini-3.5-flash`, with tools, at $1.50/M input tokens and $9/M output tokens. Catalog lookup does not establish paid API access or successful tool turns.
- The Oct 27 C25C recording remains unchanged. Its model was `gemini-3.5-flash-lite` through Google; tool-read details, rationale and flags were not recorded.
- Owner approved a new single mock week after the OpenRouter round-trip smoke passes, with redacted decision evidence. Missing cancellations or different bait choices are reported, never rerun for a better result. Failed/quota-stopped evidence stays partial.
- The owner approved the Gemini smoke only ($0.25 / six requests), then requested free models. No free Gemini model appeared in the public OpenRouter catalog; the owner explicitly chose `google/gemma-4-31b-it:free` and accurate Gemma labelling, capped at $0 / 2,500 requests for its smoke, one week and one comparison round.

## Actual outcome

| Check | Exact model requested | Requests | Result | Cost |
| --- | --- | ---: | --- | ---: |
| Gemini tool round trip | google/gemini-3.5-flash | 2 | Passed; both responses reported the same exact model | $0.00162 reported |
| Free Gemma tool round trip | google/gemma-4-31b-it:free | 3 | All HTTP 429; MODEL_QUOTA_EXHAUSTED | $0 |
| New recorded week | google/gemma-4-31b-it:free | 0 | Not started: required tool proof failed | $0 |
| 48-scenario three-system comparison | google/gemma-4-31b-it:free | 0 | Not started: required tool proof failed | $0 |

Gemini smoke usage was 270 input / 135 output tokens. No successful Gemma response identified a served model. Provider error bodies were deliberately discarded. No alternate model or new attempt followed the quota stop. The prediction remains untested; no real-model ranking or task score exists.

The unchanged model-disabled results remain CounterSign 170/240 and rules-only 215/240. The unchanged Oct 27 Gemini week remains COMPLETE_WITH_GAPS, including both WAIT decisions and missing reads/rationale/flags. The product now distinguishes these from smoke evidence and the blocked new experiment.

## Suggested README text (README itself unchanged)

> Real AI agent week replays recorded decisions and the shield's verdicts. The hosted demo uses a separate, labelled scripted AI agent. New AI agent checks are limited to a read-tool round trip and a file-injection canary; no new comparison score exists. Run details link to the exact identity and instructions used. The earlier recorded week and model-disabled evaluation remain separate evidence.

## Scope and validation

No full pytest suite. No merge or deployment. README, web/styles.css, web/grid.mjs and submission files remain untouched.

Validation: **92 focused Python passed**, **132 Node passed**, Ruff clean, **four import contracts kept**, pip check clean. The final visual follow-up passed 11 Python publication tests and five panel tests. No full pytest suite. Recorder projection is unit-tested; no new model week exercised its live export path.

One Docker build succeeded, `countersign-c67:validation`, image `sha256:cc2788781dac087d838b36de3dd25f6800d977c62e9c5c949b893d8ac421dcd2`. The container served the new panel, explorer, CSS and data with correct types. A separate container with an **invented** OPEN_ROUTER_API_KEY refused startup before services started; no real key entered a container.

Final screenshot review found the simulation banner below the sticky header while scrolled. The scoped CSS now keeps it above the header; recorded step names distinguish the two WAIT baits and README outcomes appear separately from missing event-log verdicts. These three static assets were copied into the running container after the single build, then checked against local SHA-256 hashes. The image was **not rebuilt** after that static follow-up; `VALIDATION.json` records this limitation. Runtime provider/backend code is unchanged since the build.

Actual Chrome checks passed at light 1440, dark 1440 and phone 390: nine PNGs, no horizontal overflow, no runtime exceptions, no console warnings and no non-GET requests. Decisions links, missing fields, the uncovered banner and separate dataset selection passed. Screenshots and `browser.json` are in this folder. The new section matches the existing dashboard type/palette; no new visual direction was drawn and no universal design-inspector pass is claimed.

Own preview remains at `http://localhost:8201/#model-week`. The temporary unused C67 PostgreSQL container was removed after the quota stop. Existing services and the main checkout were preserved. Code commit `6807864`; final visual/evidence commit follows it. Branch is pushed without merge.

Shared files touched: pyproject.toml (optional pinned provider), .env.example (name only), docs/DECISIONS.md, docs/AGENT_STATE.md and docs/AGENT_FUTURE_NOTES.md. Small insertions outside new files: models/runtime/safety/config, demo_week/llm_smoke/hosted startup, evaluation environment allowlists, recording/bundle redaction, index/workspace/explorer integration. No shield, executor, approvals, migration, scenario, grading, weight or model prompt changes.
