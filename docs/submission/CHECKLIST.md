# Submission checklist

**Deadline: Nov 12, 2026, 12:00 PM PT = Nov 13, 1:30 AM IST.** Target: submit on
**Nov 11**. [Official rules](https://paypalaihackathon.devpost.com/rules).

**[OWNER]** marks account access, final recording, deployment choices or final
submission. Prepared repository materials do not establish a completed public deployment.

## Prepared on main

- [x] Source includes shield, executor, agent, MCP gateway, AG Grid, sandbox proof,
      passkey countersign, guided replay and the historical evaluation explorer.
- [x] C62 Friday checkpoint is merged: the hosted image starts at the unsigned
      run, supports Monday replay and restores Friday after idle activity guards.
- [x] Validation evidence is linked in [C62's report](../../design-demos/reference/c62/README.md).
      It describes focused checks on that feature, not a new full-suite run for C68.
- [x] README and [Devpost fields](DEVPOST.md) name the audience, every page, actual
      runtime PayPal endpoints and the official judging criteria.
- [x] Video/upload materials and existing gallery are available:
      [script](VIDEO-SCRIPT.md), [YouTube fields](YOUTUBE.md),
      [submission captures](../media/submission/). Final cut and new captures remain owner work.
- [x] [Apache-2.0 licence](../../LICENSE) is present at the repository root.

## 1. Hosted demo [OWNER, then validation]

- [ ] Render: **New > Blueprint**, repo `Razeefshaik/CounterSign`, branch `main`.
      The [Blueprint](../../render.yaml) defaults to Free. Choose Starter if desired;
      a Free instance can sleep. Add no PayPal or model credentials.
- [ ] Logs show `dashboard ready`. Record the actual public `onrender.com` URL.
- [ ] Run `scripts/hosted_smoke.py` against that URL. Check `/mcp` and passkey
      approval over HTTPS before replacing `‹HOSTED URL›` in the README and Devpost text.
- [ ] Verify unsigned Friday, **Review and sign**, plain approval, new-device
      passkey setup, **Next step**, **Watch from Monday** and **Back to Friday**.
      Check idle return waits for an active decision or passkey prompt.
- [ ] Keep the working demo available **free of charge and without restriction
      until Dec 15, 2026, 8:00 AM PT**, the end of judging. Include the complete
      no-key Docker run route in the public repository as another way to test it.
- [ ] Record actual hosted readiness and cold-start behavior. Do not describe
      local Docker timings as measurements from Render.

## 2. GitHub [OWNER]

- [ ] Verify the repository is public and the open-source licence is detected and
      **visible in the repository About section**, as the official rules require.
- [ ] About description: "The AI proposes, the code countersigns: a safety layer
      for AI agents on PayPal disputes and payouts". Website: the verified hosted URL.
      Topics: `paypal`, `ai-agents`, `mcp`, `payouts`, `langchain`, `gemini`,
      `postgresql`, `ag-grid`, `passkeys`.
- [ ] Check README rendering on GitHub: banner, lock GIF, image table, Mermaid,
      local file links and section anchors. C68 repairs the earlier unclosed source-run block.
- [ ] Refresh existing captures for the Friday story and current wording. Replace
      the older Payout runs, Cash and Money graph images; add a Try to trick capture.
- [ ] Review tracked files for private values using the established secret scan.
      Do not expose environment-file contents in submission text or screenshots.

## 3. Video [OWNER]

- [ ] Review Claude's final cut against the current Friday flow; keep the
      simulation label in mock scenes and identify separate sandbox evidence.
- [ ] Keep it under three minutes and include footage of the working product.
      Check rights to music, graphics and third-party material.
- [ ] Upload to YouTube as **Public** using [YOUTUBE.md](YOUTUBE.md), with
      [captions](countersign-captions.srt); open the result while logged out.
- [ ] Replace `‹YOUTUBE URL›` in the README and Devpost text after the upload is verified.
      Claude owns chapter edits while re-cutting the video.

## 4. Devpost form [OWNER]

- [ ] Paste the prepared fields from [DEVPOST.md](DEVPOST.md), checking the field limits.
- [ ] Upload the refreshed gallery in the listed order. A passkey setup image is
      not a completed-signature capture; keep its caption accurate.
- [ ] Add the live demo, public repository and public video links; confirm team names.
- [ ] Review all applicable categories against the official list:
  - **Grand Prizes:** **1st Place**, **2nd Place**, **3rd Place**; all eligible submissions.
  - **Honorable Mention Prizes:** **Most Creative**, **Most Impactful**,
    **Best Demo Delivery**, **Best Use of PayPal + AI**, **Best Use of Agentic Commerce**.
  - **Sponsor Prizes:** **Best Use of AG Grid** and **Best Use of Render**
    (each has 1st, 2nd and 3rd place awards). AG Grid use is in the source;
    verify the completed Render deployment before presenting it as hosted evidence.
- [ ] Observe the award limit: **one Grand Prize OR one Honorable Mention Prize,
      plus one Sponsor Prize**. The limit is about awards, not a claim that a
      project has already qualified or won.
- [ ] Preview the submission while logged out. Every published link must open.
- [ ] **Submit by Nov 11**, ahead of the official deadline.

## 5. Last checks before submitting

- [ ] Resolve `‹HOSTED URL›` and `‹YOUTUBE URL›` only after validating the real links.
      Run `rg -n '‹|›' README.md docs/submission/` to locate remaining placeholders.
- [ ] Recheck links, Markdown fences, tool names, endpoint inventory and claims
      against the source. C68 uses a throwaway checker; no application tests are
      claimed to have run for these documentation changes.
- [ ] Keep the historical model-disabled comparison intact: rules-only outscored
      CounterSign. Do not promote targeted reruns to a new full score or a real-model
      week to a benchmark.
- [ ] Keep sandbox, mock and real-model evidence distinct. No invented customers,
      revenue, production savings or universal safety claims.
- [ ] Retest Friday start and the full Monday-to-following-week story on the public
      build, including the simulation label, owner approval and restoration controls.
