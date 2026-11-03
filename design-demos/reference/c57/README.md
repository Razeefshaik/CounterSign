# C57 guided start: review evidence

Branch: `razeef/c57-guided-start`, based on fresh `origin/main` `97a464b`.
Runtime source: `16ffe85`. Later documentation/evidence commits do not change
the captured UI. Claude integrates; this branch does not merge to main.

The Command center now places a dismissible Start card directly under the hero
and above the existing action strip. Its explainer uses native details. After
each confirmed step, the API's label leads a short pointer to existing records;
the final hint offers the existing confirmed restart and Sandbox proof.

All commands use `createDemoBar`'s existing guarded request/polling path. Start
and compact Next share one click-burst guard and fixed busy/error feedback.
Public reset now includes the same confirmation as the compact Restart button.
No approval, passkey, shield, executor or money-moving logic changed.

The static pointers were checked against `dashboard.DEMO_STEPS`: step 1 stages
orders before step 2 loads makers; step 10 releases A's reserve before step 11
pays A. A changed server step name retains its API label and gets neutral history
guidance instead of an incorrect static claim. Only owner moments 6 and 8 use
the existing matte-rose treatment. A one-time accent-blue pulse after confirmed
step 1 does not move or resize the compact strip, and reduced motion disables it.

## Checks and screenshots

- Web tests: **115 passed**, including **13 new C57 tests**.
- C57 hosted asset test: **1 passed in 5.00 s**, no failures/skips.
- `ruff check .`: clean. JS syntax checks passed.
- No full pytest: the owner reserves full suites for integrated main.
- All 12 screenshots were captured; browser layout/label/banner checks reached
  all requested states. Keyboard Start, shared click-burst guard, dismissal and
  reduced-motion checks also passed before extended automation timed out in CDP.
  Extended decision-link/restart browser checks are incomplete; Node tests cover
  the shared command and confirmation paths. No universal browser pass is claimed.

`step-{0,6,11}-{light,dark}-{1440,390}.png` covers the start, owner moment and
completion at desktop and phone widths. Captures are scrolled to show guidance.
The full app retains its existing hero, glass rims, navigation and compact strip.

The browser preview uses **contract fixtures and simulated API state** on C57's
own loopback port 8196. Step labels come from the actual `DEMO_STEPS` tuple, read
as Python AST data without executing the backend. Controlled POSTs only advance
or reset the disposable preview state. They are not a financial run, sandbox
call, real-model result or proof of passkey signing. No database is used.
The temporary preview is stopped. Ports 8180/8197 and other tasks are untouched.

The existing tests can be rerun from the C57 worktree:

```powershell
node --test web/tests/*.test.mjs
python -m pytest tests/dashboard/test_guide_assets.py -q
ruff check .
```

## Shared-file integration notes

| File | Small C57 insertion |
| --- | --- |
| `web/index.html:16` | Link guide.css; the hidden guide host at line 89 follows the hero |
| `web/app.mjs:7` | Import guide; line 72 shares state/notice updates and line 76 binds existing commands |
| `web/demo.mjs:48` | Share confirmed restart, busy/error state and API-disappearance updates; all POSTs stay here |
| `docs/DECISIONS.md:606` | Append the C57 decision table |
| `docs/AGENT_STATE.md` | Add the self-contained C57 state section |
| `docs/AGENT_FUTURE_NOTES.md` | Append C57 continuation details |

New implementation files: `web/guide.mjs`, `web/guide.css`,
`web/tests/guide.test.mjs`, `tests/dashboard/test_guide_assets.py`.
`web/styles.css` and `web/command.mjs` are unchanged. No new dependency, backend
endpoint, contract or migration was needed. No scope approval was required.
