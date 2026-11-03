# C50 verification and handoff

Branch `razeef/c50-sandbox-proof`, based on main `454d2cc`. No merge to main.

- `24a47c0`: deterministic proof builder, source-linked artifact and privacy tests.
- `88c1ead`: dashboard proof route, minimal navigation/hero insertions and browser capture.
- Final handoff commit adds documentation, the D11 sub-finding impact, scoped LF
  attributes for the artifact, and this evidence. See branch history for its hash.

Verified: **14 Python tests passed** (13 C50 builder tests and the existing results
bundle regression), **81 Node web tests passed**, final six proof tests passed,
Ruff clean, **four import contracts kept**. The broad Python suite was omitted
under the owner's instruction to run feature and important checks only. The
required database/toolkit test settings were supplied with C50's own database
name; these source-only checks required no database connection.

Nine screenshots show the actual static dashboard on port 8193, with a clearly
labelled fixture workspace: `proof-{light,dark,phone}.png`, plus `-d6` (keyboard-
expanded resource evidence) and `-model` (the real Gemini/mock distinction).
`browser.json` records the three viewport/theme checks: 13 rendered checks,
no horizontal overflow or runtime exceptions, a visible title, source links,
native keyboard disclosure, and an uncovered simulation banner after scrolling.
Reduced motion was enabled. The C50-scoped banner stacking fix prevents the
existing sticky header covering its text; no global stylesheet was changed.

The generic design inspector was also run. Its report is not claimed as passing:
it flags inherited shell effects and queries the first, hidden command-center
h1, producing a false missing-title result on this route. Targeted browser checks
verify the visible Sandbox proof h1 and screenshots were visually inspected.
The owner's explicit glass system is retained. Local inspector/log output is
archived outside the worktree rather than presented as successful proof.

All evidence comes from committed files: 13 checks; 1041 exchange attempts,
1007 HTTP responses and 34 transport errors; 47 successful signature calls
(46 deliveries plus the simulator); six unique successful payout items excluding
D7 fixtures; three settled disputes including buyer cancellation. Gemini is
`gemini-3.5-flash-lite` on the mock, `COMPLETE_WITH_GAPS`; both unsafe-action
prompts produced WAIT, not a demonstrated shield BLOCK. The page retains the
merchant-uploaded tracking and untested already-available-capture caveats.

Existing/shared files touched:

| File | Change |
| --- | --- |
| `web/index.html` | Fenced stylesheet, Records link after replay, one quiet hero link |
| `web/workspace.mjs` | Fenced proof integration in the actual existing router; hide unrelated live-record note on this route |
| `README.md` | Short fenced "Proof it's real" section |
| `web/README.md` | Fenced C50 build/render notes |
| `docs/DECISIONS.md` | Appended Nov 3 C50 section |
| `docs/AGENT_STATE.md` | Concise C50 current-work summary |
| `docs/AGENT_FUTURE_NOTES.md` | Task-grouped C50 implementation and verification notes |

Implementation, renderer, scoped stylesheet, generated artifact, local artifact
attributes, feature tests, capture script and walkthrough are new files.
`app.mjs` delegates routes to `workspace.mjs`, so routing was added there without
changing app startup. No `web/styles.css`, dependencies, backend routes, contracts,
shield, executor, staged week or recordings were changed. No environment-file
reads, real PayPal calls, model calls or extra approvals. Preview is stopped at
handoff; only the feature branch is pushed for Claude integration.
