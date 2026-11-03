# Submission checklist

**Deadline: Nov 12, 2026, 12:00 PM PT = Nov 13, 1:30 AM IST.** Target: submit on
**Nov 11**. Devpost forms and YouTube processing can be slow.

**[OWNER]** marks a step only the owner can do (accounts, recording, the final
submit). The rest is done or prepared in the repo.

## Already done (Nov 3 to 4)

- [x] `main` holds every feature: shield, executor, agent, MCP gateway (C48), AG Grid
      (C49), sandbox proof (C50), passkey countersign (C51), demo owner decisions on
      the staged clock (C55), guided start (C57), the 480-run evaluation explorer (C58).
- [x] Full suite on the merged features (Nov 4): 4,097 Python tests pass (10 skipped)
      and 124 web tests pass, Ruff clean, four import contracts kept. The Docker image
      boots and serves the guide, the explorer and `/mcp/`.
- [x] README rewritten for judges: try-it links, what's new, honest results, run steps.
- [x] Devpost text, video script and screenshot gallery:
      [DEVPOST.md](DEVPOST.md), [VIDEO-SCRIPT.md](VIDEO-SCRIPT.md),
      [docs/media/submission/](../media/submission/).
- [x] Repository is public, Apache-2.0 `LICENSE` at the root, no secrets or private
      emails in tracked files (scanned Nov 3).
- [x] The video's key moments were checked on the running demo: dashboard approval
      at step 6 (201), step 7 after it, and the MCP refund-to-another-account BLOCK.

## 1. Hosted demo [OWNER, then Claude]

- [ ] Render: **New > Blueprint**, repo `Razeefshaik/CounterSign`, branch `main`.
      **Starter** plan if possible (about 40 s to first page, never sleeps); Free
      sleeps after 15 idle minutes and wakes in about 2 to 3 minutes. Add **no**
      environment variables.
- [ ] Logs show `dashboard ready`. Send the `onrender.com` URL to Claude.
- [ ] Claude runs `scripts/hosted_smoke.py` against it, then checks `/mcp` and the
      passkey flow over HTTPS, and replaces `‹HOSTED URL›` in `README.md` and
      `DEVPOST.md`.
- [ ] Keep it running, free and unrestricted, **until Dec 15** (judging is Dec 1 to
      15). On Free, an uptime pinger on `/api/v1/meta` every 10 minutes avoids the cold
      start.

## 2. GitHub [OWNER]

- [ ] Delete the 17 old branches on GitHub (`git push origin --delete <branch>…`, or ask
      Claude); every branch is backed up in `PayPalDevPost/archive/2026-11-03-branch-cleanup/`.
- [ ] About → description: "The AI proposes, the code countersigns: a safety layer
      for AI agents on PayPal disputes and payouts". Website: the hosted URL. Topics:
      `paypal`, `ai-agents`, `mcp`, `payouts`, `langchain`, `gemini`, `postgresql`,
      `ag-grid`, `passkeys`.
- [ ] Check the README renders on GitHub (Mermaid diagram, tables, images).

## 3. Video [OWNER]

- [ ] Watch the final cut (2:52) and approve it: "Simulated" labels on demo scenes, no unlicensed music.
- [ ] Upload to YouTube as **Public** using [YOUTUBE.md](YOUTUBE.md), with [countersign-captions.srt](countersign-captions.srt) as the captions; open the link while logged out.
- [ ] Replace `‹YOUTUBE URL›` in `README.md` and `DEVPOST.md` (Claude can do this).

## 4. Devpost form [OWNER]

- [ ] Paste each field from [DEVPOST.md](DEVPOST.md).
- [ ] Upload the gallery in the listed order from `docs/media/submission/`.
- [ ] Links: hosted demo, repository, video.
- [ ] Team members added (each named as they wish).
- [ ] **Prize categories:** select every one that applies. A project can win one main
      prize or honourable mention plus one sponsor prize.
  - Main: Best Overall.
  - Honourable mentions: **Best Use of Agentic Commerce** (MCP gateway), **Best Use
    of PayPal + AI**, **Most Impactful**, **Best Demo Delivery**, Most Creative.
  - Sponsor: **AG Grid** (the incident desk, execution history, payout runs and
    makers use AG Grid Community). Also Render if its requirement is "hosted on
    Render"; check the rules page.
- [ ] Preview the submission while logged out; every link opens.
- [ ] **Submit by Nov 11.**

## 5. Last checks before submitting

- [ ] No `‹…›` placeholder left: `grep -rn "‹" README.md docs/submission/`.
- [ ] Claims check, every hit must be an allowed form:
      `grep -n -i -E "payroll|never moves twice|money never|wrongful refund|guarantee|100%|always safe" README.md docs/submission/*.md`.
- [ ] The hosted demo plays the week from a fresh start (Restart, then Start the week and Next step to the
      end) and the passkey countersign works on it.
