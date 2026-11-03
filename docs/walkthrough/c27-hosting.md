# C27: hosting the demo

**Status:** C47 Nov 3 local image, full tests, resource-sized HTTP smokes and browser checks verified; not deployed yet. **Render (free) is the owner's selected host as of Oct 28**, replacing the Oct 27 Cloud Run choice. The owner deploys with the Render steps below; no cloud resources or credentials were used for these checks.

## What gets hosted

One Docker container runs the whole demo:

- the CounterSign app and dashboard (`web/`) on the platform's `$PORT`;
- the bundled PayPal mock (`mock_paypal`) on loopback port 8099, never exposed;
- a fresh PostgreSQL 16 on loopback port 55433, rebuilt from an empty, migrated template on every boot.

The mock is in-memory, so the database is disposable too. After every restart or wake-up, both start clean together and always match.

<!-- C51 -->
## Passkey countersigning

The decision view offers **Countersign with passkey** above plain approval in a
WebAuthn-capable browser. First use registers a key, then signs a new challenge
bound to the displayed proposal/hash. The signed timeline names its short fingerprint.
Cancellation or an error never silently uses plain approval.

Set both `COUNTERSIGN_PASSKEY_RP_ID` (hostname without scheme/port) and
`COUNTERSIGN_PASSKEY_ORIGIN` (exact browser origin without a trailing slash).
Locally use `localhost` and `http://localhost:8194` and browse that hostname, not
127.0.0.1. WebAuthn permits HTTP localhost. Outside the hosted demo factory both
settings are required; live mode never trusts request-host defaults.

Only `create_hosted_app` in demo mode on the loopback mock can default to the
request host and `X-Forwarded-Proto` scheme. Render's edge must provide the
canonical host and trusted HTTPS forwarding header. Explicit settings can pin
the Render/custom hostname, e.g. `demo.example.com` and `https://demo.example.com`.
Registration and approval must keep the same pair. Public demo registration uses
the existing unauthenticated demo guard; it does not prove a visitor is the real
marketplace owner. Live mode requires the owner token for every passkey request.

The hosted database is disposable: credentials disappear on restart or wake-up,
so visitors must register again. Normal financial demo resets retain credentials;
old challenges cannot approve missing/changed proposals. Counterless or
non-incrementing authenticators are refused; plain approval remains the fallback.
Passkeys need no real model or PayPal calls.
<!-- /C51 -->

It needs **no secrets**. The database password is random on every boot and is never printed. The mock takes the fake `mock-client` / `mock-secret` credentials. There are no PayPal sandbox keys, model keys or `.env` file in the image (`.dockerignore` excludes `.env*`).

## Options compared (checked Oct 27, 2026)

| Host | Cost | Sleep and cold start | PostgreSQL | Expiry | Single container fits? |
| --- | --- | --- | --- | --- | --- |
| **Render**, free web service (Docker) | $0, no card needed | Sleeps after 15 min without traffic; wake takes about 1 min, plus our boot (about 85 s at 0.1 CPU, measured) | Embedded in the container. Render's free Postgres expires (see next column), so we don't use it | Free Postgres expires after 30 days, then 14 days' grace before it is deleted. The web service does not expire; 750 free instance hours a month | Yes: 512 MB RAM, 0.1 CPU; our container uses about 115 MB |
| **Google Cloud Run** + embedded Postgres | Free tier: 2M requests, 180k vCPU-s, 360k GiB-s a month (request billing). Needs a billing account and a card | Scales to zero; cold start is our boot at 1 vCPU (about 8 to 10 s measured at 2 CPUs) | Embedded (or Neon) | None for the free tier | Yes |
| Koyeb, free instance | $0, card required ($29 pre-authorisation, then cancelled) | Sleeps after 1 hour without traffic | Free Postgres has only 5 active hours a month, so embedded | None stated | Yes, but 512 MB / 0.1 vCPU (same speed as Render) |
| Hugging Face Spaces (Docker) | Docker Spaces now need a paid PRO plan | Sleeps when unused | Embedded (disk is not persistent) | None stated | Yes, and fast (2 vCPU, 16 GB), but not free any more |
| Fly.io | No free tier for new organisations (trial: 2 machine hours or 7 days) | n/a | n/a | Trial ends | Yes, but not free |
| Railway | $5 trial for 30 days, then $1 credit a month | n/a | Managed | Credit runs out within days at 512 MB | Yes, but not free for the judging window |
| Neon (database only) | Free, no card: 100 CU-hours a project, 1 GB | Database suspends after 5 min idle | Managed PostgreSQL | None | Not a host; would pair with Render or Cloud Run |

Sources (all read Oct 27, 2026): [Render free](https://render.com/docs/free), [Render instance types](https://render.com/docs/compute-plans), [Render Blueprint spec](https://render.com/docs/blueprint-spec), [Cloud Run pricing](https://cloud.google.com/run/pricing), [Google Cloud free features](https://docs.cloud.google.com/free/docs/free-cloud-features), [Koyeb pricing FAQ](https://www.koyeb.com/docs/faq/pricing), [Koyeb scale to zero](https://www.koyeb.com/docs/run-and-scale/scale-to-zero), [Hugging Face Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [Hugging Face Docker Spaces](https://huggingface.co/docs/hub/spaces-sdks-docker), [Fly.io pricing](https://docs.fly.io/about/pricing), [Railway free trial](https://docs.railway.com/reference/pricing/free-trial), [Neon pricing](https://neon.com/pricing). Oracle and Azure free tiers were not re-checked: an always-on VM needs more setup than this card allows.

## Recommendation

**The owner selected Render on Oct 28** (it briefly chose Cloud Run on Oct 27). Cloud Run stays documented below as the fallback if Render's speed is unacceptable.

Why Render:

- Its Free plan costs $0, and it builds the repository's `Dockerfile` from `render.yaml`, which takes a few clicks.
- 750 free instance hours a month cover one service running all month, so judges can open the URL days after Nov 13.
- Embedding PostgreSQL avoids the free Render database's 30-day expiry. A database made on Nov 10 would be deleted soon after judging.
- The container fits within 512 MB; the Nov 3 release check below records memory after the complete week.

The cost is speed. Free's 0.1 CPU makes the staged week slower; use the Nov 3 measurements below when choosing Free or Starter. After 15 idle minutes, Render also needs to wake the service (about a minute), on top of the container's boot. The owner can optionally point an uptime pinger at `/api/v1/meta` every 10 minutes during judging; the click list below includes the current Free limits. Restarts always create a fresh disposable week.

Why Cloud Run is the runner-up: it starts about ten times faster (1 vCPU) and its free tier is permanent, but it needs a Google Cloud billing account with a card, plus `gcloud` setup. Choose it if the cold start on Render is unacceptable. The same image works there, because the start script reads `$PORT`.

## Files

| File | What it does |
| --- | --- |
| `Dockerfile` | Python 3.12 slim (bookworm), PostgreSQL 16 from the PostgreSQL apt repository, `tini`, editable install of the project, precompiled bytecode, non-root user `app` (UID 1000). At build time it creates the migrated template database |
| `.dockerignore` | Keeps `.env*`, `.git`, `.venv`, caches, `recordings/`, `tests/`, `eval/` and `docs/` out of the build |
| `scripts/hosted_start.sh` | The entrypoint. `--build-template` (image build) runs `initdb` and `alembic -c migrations/alembic.ini upgrade head` into the template. A normal boot copies the template, starts PostgreSQL on loopback with a new random password, starts the mock on loopback, registers its webhook through `scripts.mock_rehearsal --reset`, then starts the app with `uvicorn --factory countersign.main:create_hosted_app` on `$PORT` |
| `render.yaml` | Render Blueprint: one free Docker web service, health check `/api/v1/meta`, auto deploy off |
| `src/countersign/main.py` | `create_hosted_app()`: demo-only settings, no background workers, and the dashboard served from `web/` after every API route |
| `src/countersign/config.py` | `load_hosted_settings()` and `require_hosted_demo()`: refuse live mode, a non-mock PayPal URL, and any `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` or `GOOGLE_API_KEY` |
| `tests/unit/test_hosted.py` | Tests the guard and the dashboard mount |

## What stays off

- **Live mode.** The start script forces `COUNTERSIGN_MODE=demo`, `PAYPAL_BASE_URL=http://127.0.0.1:8099` and the fake mock credentials on every boot. A platform variable cannot change them. The app factory refuses anything other than demo mode on the loopback mock.
- **Sandbox and model credentials.** If `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET` or `GOOGLE_API_KEY` is set, the container refuses to boot (exit code 64) and logs only the variable name.
- **Real PayPal and real LLMs.** The PayPal origin guard accepts only loopback in demo mode, and no model is configured. The optional demo week uses the scripted model.
- **Background workers.** The hosted app never runs the executor, reconciler or inbox loops itself. Only the staged demo CLI (or later the M17 demo bar) ticks the engine.
- **The mock control API.** It listens on loopback only, and the platform exposes only `$PORT`.

## Owner steps (Render)

Allow about five minutes for these clicks; the image build and boot take extra time.

1. After approving and merging C47 to `main`, sign in to the [Render dashboard](https://dashboard.render.com/) with GitHub. Choose **New > Blueprint > Connect** for the CounterSign repository. Select branch **main** and keep Blueprint Path **render.yaml**. See [Render's Blueprint setup](https://render.com/docs/infrastructure-as-code).
2. Review the single `countersign-demo` web service. The committed Blueprint selects **Free: $0/month, 0.1 CPU, 512 MB**. For faster judging, the paid **0.5c-512mb (formerly Starter): $7/month, 0.5 CPU, 512 MB** is available. Prices checked Nov 3 on [Render pricing](https://render.com/pricing); [compute plans](https://render.com/docs/compute-plans) lists the specs. To upgrade after creation, open the service's **Compute > Edit**, choose that plan and **Save**. Keep `render.yaml`'s `plan` in sync with that owner choice before the next Blueprint sync, or its `free` setting can restore Free.
3. Keep **COUNTERSIGN_HOSTED_SEED=none** as the only application variable, then click **Deploy Blueprint**. Add no PayPal keys, model keys, `OWNER_TOKEN` or `DATABASE_URL`; the container supplies its local mock/database settings and Render supplies `PORT`.
4. Open **Logs**. Boot should show, in order: `[hosted] starting PostgreSQL 16`, `[hosted] starting the PayPal mock on loopback`, `[hosted] starting the app on port ...`, `[hosted] dashboard ready`. Open the service URL and confirm **Simulated: mock PayPal**. Optionally configure an uptime pinger to GET `https://YOUR-SERVICE.onrender.com/api/v1/meta` every 10 minutes during judging. Free sleeps after 15 idle minutes and shares 750 monthly hours per workspace; see [Free limits](https://render.com/docs/free). A pinger is optional and does not preserve data across restarts.
5. Auto deploy is off. After later code merges, open the service's **Deploys > Manual Deploy > Deploy latest commit**. Blueprint configuration syncs are separate from code auto deploy; review them before applying. See [manual deploys](https://render.com/docs/deploys).

After the first deploy, replace `YOUR-SERVICE` with the actual hostname and run this
from the repository root, while nobody is presenting (it resets the shared week):

```powershell
.venv\Scripts\python.exe scripts/hosted_smoke.py https://YOUR-SERVICE.onrender.com --timeout 900
```

Success ends with `Hosted smoke passed: 11 steps, Friday paid once, cash $354, B reserve $54, playground BLOCK`.

### Google Cloud Run (fallback)

The same image runs unchanged. It has about 130 MB of memory in use once the week is played, after M15's bounded mock snapshots, and it is ready in about 12 s.

1. **[OWNER]** In the Google Cloud console:
   - create a project;
   - link the billing account that has the free credits;
   - add a budget alert, for example $5.
2. **[OWNER]** Install the gcloud CLI, then run:
   ```powershell
   gcloud auth login
   gcloud config set project YOUR_PROJECT_ID
   gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
   ```
3. **[OWNER]** Deploy from the repository root:
   ```powershell
   gcloud run deploy countersign-demo --source . --region us-central1 --port 8080 --memory 1Gi --cpu 1 --min-instances 0 --max-instances 1 --no-cpu-throttling --timeout 300 --allow-unauthenticated --set-env-vars COUNTERSIGN_HOSTED_SEED=none
   ```
   - **Seed `none`:** the presenter plays the week with the M17 demo bar.
   - **Seed `week`:** use this instead to open on the finished week.
   - **No other variables:** the container refuses PayPal and Gemini credentials.
   - **`.env` stays local:** `--source .` uploads files not ignored by `.gitignore`, and `.env` is ignored.
4. **Check the deploy:**
   - open the printed URL and look for the "Simulated: mock PayPal" banner;
   - `URL/api/v1/meta` shows mode `demo`;
   - the bar's "Run next staged step" works.
5. **After later merges:** run the same deploy command again.

`--max-instances 1` caps cost, and the service scales to zero when idle. Every wake-up starts from a clean database and mock.

## How to verify

1. Open `https://<service>.onrender.com/`. The dashboard loads, and the persistent banner **Simulated: mock PayPal** is visible.
2. Open `/api/v1/meta`. It must show `"mode":"demo"`, `"paypal_env":"mock"`, `"simulated":true`.
3. In the Render logs, the boot lines are, in order: `starting PostgreSQL 16`, `starting the PayPal mock on loopback`, `starting the app on port …`, `dashboard ready`. No URL, password or token appears.
4. Optional negative check (on a local Docker only): `docker run --rm -e PAYPAL_CLIENT_ID=fake countersign-demo` must print a refusal and exit with code 64.

Local check (Docker Desktop):

```powershell
docker build -t countersign-demo .
docker run --rm --cpus=0.1 --memory=512m -p 127.0.0.1:8080:8080 countersign-demo
# then open http://127.0.0.1:8080/
```

## Nov 3 release check

Final runtime image: `countersign-c47`, built from **`e661516`** on
`razeef/c47-release-check` (base `origin/main` **`1303ad2`**). Image manifest:
`sha256:cc0c3d9d57f81729e7a6244c4193b190df70e65a7551e10bf7169fccee34ae3a`.
The later handoff commit changes documentation only.

During verification, `main` advanced to **`02a4a47` (C48 MCP gateway)**. These
results cover base `1303ad2` plus the C47 fixes, not that newer C48 code. After
reviewing/merging C47, verify the combined main before deploying it to Render.

Both resource limits use a fresh container, seed `none`, and loopback port 8190.
Boot is measured immediately before `docker run -d` to the first HTTP 200 from
`/api/v1/meta`, polled every 250 ms. It includes Docker launch, excludes image
build/download, and is **local warm-image evidence, not a Render cloud cold start**.
The final resource checks run after pytest and Chrome have finished. The owner's
container on 8180 was untouched.

| Check | Free size: 0.1 CPU, 512 MiB | Starter size: 0.5 CPU, 512 MiB |
| --- | --- | --- |
| First meta HTTP 200 | **128.984 s** | **40.328 s** |
| Full public HTTP smoke, `--timeout 900` | **PASS, 1168.407 s** | **PASS, 168.390 s** |
| Docker memory after the completed week | **133.3 MiB / 512 MiB** | **133.3 MiB / 512 MiB** |

The smoke checks reset and all 11 staged steps, Friday's $1,086 paid exactly once,
$354 final available cash, Maker B's $54 non-cash reserve, four playground attacks,
and refund-to-other-account BLOCK with `written=false`. `--timeout 900` limits the
wait for each staged command, not the entire week.

Both resource runs serve all **10** checked files with HTTP 200 and the
correct MIME: `hero.css` (`text/css`), both backdrop WebPs, four hero WebPs and the
milestone WebP (`image/webp`), `countersign-mark.svg` (`image/svg+xml`) and `demo.mjs`
(`text/javascript`). Each invented PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and
GOOGLE_API_KEY probe exits **64**, naming only the variable and never its value.
Free cgroup memory.current/peak are **189788160 / 244346880 bytes**; Starter's are
**190435328 / 243662848 bytes**. Unlike Docker's displayed memory, these include
file cache. Neither completed run was OOM-killed.

Verification on Windows with SelectorEventLoop and explicit test DB 55433 plus
`COUNTERSIGN_TEST_TOOLKIT_PYTHON`: **4,019 Python passed, 0 failed, 5 skipped** in
2132.33 s. All five skips require optional Toolkit/LangChain packages in the main
interpreter; the separate toolkit child setting was supplied. **75 Node tests
passed**, Ruff is clean and all **3 import contracts** are kept. After the final
UI addition, **36 focused hosted/demo-bar Python checks passed** in 29.49 s;
backend Python code remained unchanged.

The final image passed **57 browser checks**: eight routes (overview, decision,
runs, cash, graph, audit, replay, trick), 390/1440/1870 px, light/dark, maker dialogs
and Esc, restart confirmation/cancel, and a quick Next step after restart. Exactly
one reset and one step POST reached the disposable 8190 app. No JavaScript console
errors, page exceptions or unexpected network errors occurred. Four automatic
browser network-log entries for the contract-required cash-unavailable 503 after
reset are recorded separately; the UI correctly shows unavailable before a
balance exists. The simulation banner stayed visible across routed pages; manual
review found that the maker dialog backdrop obscures it while the dialog is open.
The owner approved an in-dialog server-derived label in `e661516`; manual review
confirms it is readable above the backdrop at phone width. The final browser
matrix also checks its position and hit visibility inside the modal.
**54 screenshots** and
`verification.json` are in sibling `PayPalDevPost/design-demos/reference/c47/`.

C47 fixes three release checks: the owner HTTP test now shares its fixture's
frozen clock; WebP headers no longer depend on the platform MIME database; and a
single demo poller follows a new command accepted during the previous dashboard
refresh. The last case was reproduced in the real browser and a failing Node
regression before being fixed. A fourth fix, approved by the owner after manual
review, repeats the simulation label inside the maker dialog using the existing
eyebrow style. Financial code and contracts did not change.

An earlier `88ba5ee` Free run concurrent with pytest stopped after step 5 with
`Hosted smoke FAILED: HTTP failure or unexpected API shape`; a subsequent state
GET timed out. It booted in 157.422 s, failed after 338.797 s and used 165.5 MiB
at that point (not after a completed week), with no OOM. This failed attempt is
preserved alongside the final measurements. Evidence logs/JSON are in sibling
`design-demos/.impeccable/c47/`.

The first final-image browser pass also recorded a meta 503 on Decision during
rapid route changes, before any POST. The harness could advance from Overview
while its optional API reads were unfinished. The final visual matrix waits for
local requests to complete and 500 ms of network idle before capturing each
route; it is not a load test. The original failure is retained in
`reference/c47/first-final-meta-503.json`. The mock-clock read's existing 3 s
deadline was not changed.

## What was measured locally (Oct 27)

| Run | Result |
| --- | --- |
| `--cpus=0.1 --memory=512m`, seed `none` | Dashboard ready 85 s after start, 115 MB memory. `/` 200, `/api/v1/meta` shows demo/mock, and incidents, disputes and payout runs all return 200 |
| `--cpus=2`, seed `week` | Ready in 8 s, and the scripted demo week completed in 56 s, but the mock grew to 813 MB |
| `--cpus=0.1 --memory=512m`, seed `week` | The mock reached the 512 MB limit and the week stopped (`DEMO_STOPPED_REVIEW_CHECKPOINT`) |
| `PAYPAL_CLIENT_ID` set | Refused, exit 64 |
| `COUNTERSIGN_MODE=live` plus the sandbox URL set | Still served demo mode on the mock (forced) |

## M21 local readiness measurements (Oct 27)

Built from `f330b3f` plus M21 adapter changes; Linux amd64 on Docker Desktop.
No real model, PayPal, cloud account, or environment file was used.

| Check | Result |
| --- | --- |
| Cold start: `--cpus=1 --memory=512m`, seed `none`, new disposable container | **4.181 s** from immediately before `docker run -d` to first HTTP 200 on `/api/v1/meta`; polled every 100 ms, includes Docker launch, excludes image download/build |
| Full public HTTP smoke | **PASS**: reset, all 11 staged steps, one DONE Friday run totalling 108600 minor USD, each Friday line one successful item, cash available 35400, B's active non-cash reserve 5400, four playground attacks, refund-to-other-account BLOCK with written=false |
| Memory after the week | `docker stats`: **137.8 MiB / 512 MiB**; cgroup memory.current **196636672 bytes**, peak **244563968 bytes** (cgroup includes file cache; Docker's display subtracts inactive cache) |
| Image size | `docker images` / `docker system df -v`: **860 MB** total; image-inspect Size **204439669 bytes** (a different size reported by this Docker backend; the larger displayed disk usage was used for the threshold) |
| Image exclusions | `/app/tests`, `/app/recordings`, `/app/eval` and `/app/.env` absent (existence checks only) |
| Credential refusal | Each of PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET and GOOGLE_API_KEY, separately set to an invented value: exit **64**, variable name only, value absent |

Image: `countersign-m21:verified`, manifest
`sha256:e893efd754f342c160617e5d8f326f5eae68d1a98695d110edfae728156ee53c`.
Both thresholds pass (under 1 GB and under 20 s), so no Dockerfile or dependency
change was needed. This is a local warm-image boot, not a measured Cloud Run cold
start. The HTTP script runs outside the container and never reads its database,
checkpoint, private mock controls, environment, or filesystem.

Reproduce locally from the reviewed branch (normal Docker build context excludes
`.env*`, recordings, tests, eval results and docs):

```powershell
docker build -t countersign-demo .
docker run --rm --name countersign-demo-check --cpus=1 --memory=512m -p 127.0.0.1:8080:8080 countersign-demo
# In another terminal, with project dependencies installed:
python scripts/hosted_smoke.py http://127.0.0.1:8080
```

The smoke command **resets the shared staged week**, then advances all 11 steps.
Run it on a disposable demo when nobody is presenting. It checks mode before any
POST, refuses redirects, waits for each background command, and exits non-zero
with a fixed diagnostic on a mismatch or timeout. It prints no response bodies
or credentials. Existing `tests/unit/test_hosted.py` covers all three credential
variables; M21 also verified the actual image's entrypoint with each fake value.

## Render readiness measurements (Oct 28)

Image built from `main` after C33, plus the mock-control timeout fix below. Both runs used `--memory=512m`, seed `none`, and the full `scripts/hosted_smoke.py` over HTTP.

| Check | Render Free size (`--cpus=0.1`) | Render Starter size (`--cpus=0.5`) |
| --- | --- | --- |
| First 200 on `/api/v1/meta` | 89 to 121 s (Render adds about 1 min to wake after 15 idle minutes) | 14 s (Starter does not sleep) |
| Full smoke: reset, 11 steps, playground | **PASS** in 710 s (40 to 125 s per step) | **PASS** in 118 s (about 11 s per step) |
| Memory after the week | 134 MiB of 512 | 135 MiB of 512 |

**Fix found at 0.1 CPU:** the demo CLI's loopback mock-control client had a 30 s read timeout. A multi-day clock jump makes the mock deliver every due transition, and at 0.1 CPU that took longer, so a step stopped with `DEMO_STOPPED_REVIEW_CHECKPOINT` (`httpx.ReadTimeout` on `/_mock/clock/set`; a second click resumed it). That client now waits 300 s. Setting the clock is absolute, so a retry was already safe. No PayPal, engine or shield timeout changed.

**Choosing the plan.** Free works, but each demo-bar step takes about a minute and a cold visitor waits about 3 minutes. For the judging window, Starter (0.5 CPU, no sleep) makes every step feel quick; change `plan: free` to `plan: starter` in `render.yaml` or in the Render dashboard. Check Render's current price before switching.

## Cloud Run smoke and rollback

Use the exact deploy command in the Cloud Run owner steps above. In particular,
`--no-cpu-throttling` keeps CPU available for the demo subprocess after the step
endpoint's immediate 202 response, including gaps between state polls. This uses
**instance-based billing**, not the request-based assumptions in the historical
pricing table. Keep min instances zero, max instances one, and the budget alert;
a budget alert is not a spending cap. No cloud cost or deployment is incurred by
these local checks. See Google's [billing settings](https://docs.cloud.google.com/run/docs/configuring/billing-settings)
and [`gcloud run deploy`](https://docs.cloud.google.com/sdk/gcloud/reference/run/deploy).

After the owner deploys:

```powershell
$demoUrl = gcloud run services describe countersign-demo --region us-central1 --format="value(status.url)"
python scripts/hosted_smoke.py $demoUrl
```

Before a later deploy, save the currently serving revision:

```powershell
$previousRevision = gcloud run services describe countersign-demo --region us-central1 --format="value(status.latestReadyRevisionName)"
# Deploy the new version, then run the smoke command above.
# If the new version fails, route all traffic to the saved known-good revision:
gcloud run services update-traffic countersign-demo --region us-central1 --to-revisions "$previousRevision=100"
python scripts/hosted_smoke.py $demoUrl
```

The saved revision must be the currently serving known-good revision; the simple
latestReadyRevisionName command assumes this service's usual 100%-to-latest
traffic policy. For a split rollout, select the known-good revision from
`status.traffic` instead. Rollback changes traffic, not data recovery: every
container boot intentionally creates a fresh mock and disposable database.
First deployment has no prior revision to roll back to. Google's [rollback guide](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration)
documents `--to-revisions`.

## Current limits

- M15 already bounds mock balance history at 256 snapshots; the earlier 813 MB
  result above is historical. The full M21 week fits within 512 MB locally.
- M15 database dashboard and M17 demo bar are implemented and exercised by the
  HTTP smoke. The hosted source is database, not contract fixtures.
- Seed `none` stays the default so visitors can play the week. Everything resets
  on restart; the shared presenter demo is not a production multi-user service.
- Cloud Run deploy, cloud cold start and deployed-URL smoke remain owner steps;
  local success is not claimed as a cloud deployment.
