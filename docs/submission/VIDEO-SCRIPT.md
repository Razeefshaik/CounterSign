# Demo video: final narration

Under 3:00 (2:52). Built in Remotion from code, in `PayPalDevPost/remotion-video` (outside this repository), with real screen recordings of the running dashboard. Narration: ElevenLabs. Music: Eleven Music.

**Words to avoid** (claims rules): "payroll", "money never moves twice", "zero wrongful refunds", "$400 withheld", "guaranteed". Say "mock" for the demo week and "sandbox" only for the recorded sandbox evidence. No third-party logos or product names on screen beyond the tools the project uses.

| Time | Scene | Narration |
| --- | --- | --- |
| 0:00 | Cold open | Friday. Nine A.M. An AI agent has the keys to a marketplace's PayPal account. It's about to send fifteen hundred dollars... and four hundred and fourteen of it... is UNDER DISPUTE. |
| 0:13 | The stakes | AI agents can now refund buyers... accept disputes... and send payouts. Nothing in PayPal makes them ask a human first. And a prompt that says "please confirm"... is NOT a lock. |
| 0:26 | The reveal | So we built the lock. CounterSign. The AI proposes. The code countersigns. |
| 0:34 | The shield | Every proposal goes through a shield of sixteen named rules. Allow. Block. Or wait for the owner. Only then... does the executor call PayPal. |
| 0:44 | The staged week | One staged week, on a PayPal mock built from real sandbox recordings. Monday. A buyer disputes a four hundred dollar order. PayPal freezes the money... and CounterSign holds back the maker's share. Thursday. A payout is stuck on a typo. The AI cancels it, on its own... and waits for PayPal to confirm. |
| 1:06 | Friday | Friday. The naive agent pays everyone... disputed shares included. CounterSign holds back four hundred and fourteen dollars... and asks the owner for one exact run. One thousand and eighty-six dollars. |
| 1:20 | The passkey | Ten named checks. One payload hash. The owner signs it with a passkey... on their own device. A plain click can NEVER fake that signature. |
| 1:31 | The network cut | Then we cut the network... right after PayPal accepts the payout. The executor retries, with the same keys. PayPal points back to the original batch. Exactly. One. Payment. |
| 1:44 | Any AI agent | And it isn't just our agent. CounterSign is an MCP server... so any AI agent can plug in. Here, one is tricked into refunding a buyer to a stranger's account. Blocked. Three rules, named. Nothing sent. |
| 1:59 | Built on, real footage | And this is no mockup. This is the real build, on PayPal's real APIs. Payouts. Disputes. Orders. Webhooks. PayPal's Agent Toolkit... with every write tool switched off. Google Gemini reads the mess. The code decides. And every single run... is yours to slice, in AG Grid. |
| 2:19 | Sandbox proof | Every PayPal assumption was tested on the real sandbox first. Thirteen checks. Over a thousand recorded calls. Four assumptions were wrong... so the design changed to match PayPal. |
| 2:32 | Results | Forty-eight fault scenarios. Five runs each. Zero duplicate settlements. Zero refunds without the owner. Zero payments to an unverified payee. |
| 2:42 | Close | AI can move money now. CounterSign makes sure it asks first. The AI proposes. The code countersigns. |

## Upload

See [YOUTUBE.md](YOUTUBE.md). Upload [countersign-captions.srt](countersign-captions.srt) as the English captions. Check the length on YouTube itself is under 3:00, and open the link while logged out.
