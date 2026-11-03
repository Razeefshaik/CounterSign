# Demo video script (target 2:45, hard limit under 3:00)

Recorded from the dashboard. Judges may score from the video and README alone, so the
video must show, in order: **the problem, the AI moment, the PayPal payment, the
safety**, and that it is real.

## Before recording

- **Where:** the local container (`http://localhost:8180`) or the hosted URL. Use
  `localhost`, not `127.0.0.1`, or the passkey prompt will not appear.
- **Fresh week:** press **Restart** in the bottom-right controls, then confirm. The
  week starts at step 0.
- **Window:** Chrome at 1920×1080, zoom 100%, light theme, one tab, notifications off.
  Hide the bookmarks bar.
- **Passkey:** register one before recording (open any decision, **Set up a passkey**),
  so on camera Windows Hello asks only for your PIN or fingerprint. Restart the week
  afterwards.
- **MCP moment** (1:50), pick one:
  - **Claude Desktop** (best): Settings → Connectors → Add custom connector →
    `‹HOSTED URL›/mcp`. Needs the hosted HTTPS URL.
  - **MCP Inspector** (fallback): `npx @modelcontextprotocol/inspector`, transport
    "Streamable HTTP", URL `http://localhost:8180/mcp`.
  - Either way the demo must be at **step 3 or later** (A's dispute exists).
- **Words to avoid** (claims rules): "payroll", "money never moves twice", "zero
  wrongful refunds", "$400 withheld", "guaranteed". Say "mock" for the demo week and
  "sandbox" only for the recorded sandbox evidence.

## Script

| Time | On screen (click) | Narration |
|---|---|---|
| 0:00–0:14 | Command center hero, then scroll to "Needs your signature" | "PayPal now lets AI agents refund buyers, accept disputes and send payouts. But nothing in PayPal makes an agent ask a human first, and a prompt that says 'please confirm' is not a lock. CounterSign is that lock. The AI proposes. The code countersigns." |
| 0:14–0:30 | Point at the "Simulated: mock PayPal" banner; press **Next step** to Monday | "A small marketplace pays its makers every Friday through PayPal Payouts. This is one staged week on our PayPal mock, built from real sandbox recordings. Monday: a buyer disputes Maker A's $400 order. PayPal freezes the capture, so CounterSign reserves A's $360 share." |
| 0:30–0:50 | **Next step** ×3 to Thursday; open **Execution history** (AG Grid), filter actor = agent | "Thursday: Maker C's last payout is stuck as UNCLAIMED, an email typo. The AI cancels it on its own, because money coming back is low risk, and nothing is resent until PayPal confirms RETURNED." |
| 0:50–1:05 | **Next step** to Friday (step 6). Command center: "You can pay $1,086 to 2 makers today"; This week → Friday's comparison | "Friday. $1,800 is available, so a naive agent pays everyone, including $414 of disputed shares. CounterSign holds them back and asks for one exact run: $1,086." |
| 1:05–1:30 | **Review and sign**. Scroll the 10 named checks. Click **Countersign with passkey** → Windows Hello → timeline shows "Countersigned with passkey" | "Ten named checks, each with its rule. The owner approves this exact payload hash, once, and signs it with a passkey on their own device. A plain click can never pretend to be that signature." |
| 1:30–1:50 | **Next step** (step 7). Execution history shows one payment and the duplicate answer linked to the original batch | "We cut the network right after PayPal accepts the payout. The executor retries with the same keys, PayPal answers with a link to the original batch, and there is exactly one payment." |
| 1:50–2:15 | Claude Desktop (or MCP Inspector): "Refund the buyer on Maker A's dispute to refund-me@example.test." Show the BLOCK verdict with PAYEE_INTEGRITY | "And it works for any AI agent. CounterSign is an MCP server. Here Claude is talked into refunding a buyer to someone else's account. It can only propose, and the shield blocks it, naming the rule." |
| 2:15–2:32 | **Sandbox proof** page: the five figures, a DIFFERENT card, click one "Recording" link to GitHub | "Every PayPal assumption was tested on the real sandbox first: 13 checks, over a thousand recorded calls. Four were wrong, and we changed the design to match PayPal. Every fact links to its recording." |
| 2:32–2:47 | README Results table (zero in all four invariant columns), then the repo URL | "Across 48 fault scenarios, run five times each, zero duplicate settlements, zero refunds without the owner, zero payments to an unverified payee. CounterSign: the AI proposes, the code countersigns." |

**If you are over time**, cut the Thursday shot (0:30–0:50) to 8 seconds; the Friday
comparison carries the story.

## After recording

- Trim, check the length on YouTube itself is under 3:00.
- Upload to YouTube as **Public** (the rules ask for a public video), title "CounterSign:
  the AI proposes, the code countersigns (PayPal AI Hackathon)".
- Open the link while logged out to check it plays.
- Put the link in `README.md` and `docs/submission/DEVPOST.md` (replace `‹YOUTUBE URL›`).
