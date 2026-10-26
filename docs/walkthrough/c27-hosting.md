# C27: hosting the demo

**Status:** prepared, not deployed. The deploy files are shared, so they need the human owner's approval. The owner chooses the host and deploys. Nothing in this branch creates an account, pushes or deploys.

## What gets hosted

One Docker container runs the whole demo:

- the CounterSign app and dashboard (`web/`) on the platform's `$PORT`;
- the bundled PayPal mock (`mock_paypal`) on loopback port 8099, never exposed;
- a fresh PostgreSQL 16 on loopback port 55433, rebuilt from an empty, migrated template on every boot.

The mock is in-memory, so the database is disposable too. After every restart or wake-up, both start clean together and always match.

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

**Render, free Docker web service, with PostgreSQL embedded in the container.** Runner-up: **Google Cloud Run** with the same image.

Why Render:

- It is free with no card, and it builds the repository's `Dockerfile` from `render.yaml`, which takes a few clicks.
- 750 free instance hours a month cover one service running all month, so judges can open the URL days after Nov 13.
- Embedding PostgreSQL avoids the free Render database's 30-day expiry. A database made on Nov 10 would be deleted soon after judging.
- The container fits in 512 MB with room to spare (about 115 MB measured).

The cost is speed. At 0.1 CPU, the first visit after 15 idle minutes waits for Render's wake (about 1 minute) plus our boot (about 85 seconds measured with `docker run --cpus=0.1 --memory=512m`). Warm requests are quick: 6 to 25 ms for API calls and 0.3 s for the page, measured locally. To avoid the wait during judging, the owner may point a free uptime pinger (for example cron-job.org) at `/api/v1/meta` every 10 minutes. Render's community forum suggests this, and one service running all month stays within the 750 hours. Check Render's current terms before relying on it.

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

1. Approve and merge this branch to `main` (the deploy files are shared).
2. Create a free account at https://render.com. Sign in with GitHub and give Render access to the CounterSign repository only.
3. In the Render dashboard, choose **New > Blueprint**, select the repository and branch `main`, and confirm. Render reads `render.yaml` and creates `countersign-demo` on the **Free** plan.
4. Environment variables. The Blueprint sets the only one:
   - `COUNTERSIGN_HOSTED_SEED` = `none` (default).

   Do **not** add `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`, `GOOGLE_API_KEY`, `OWNER_TOKEN` or `DATABASE_URL`. The container sets or refuses them itself. Render sets `PORT`.
5. Wait for the first build (about 5 minutes) and the first boot. The log shows `[hosted] dashboard ready`.
6. Optional: add an uptime pinger for `https://<service>.onrender.com/api/v1/meta` every 10 minutes during judging.
7. Auto deploy is off. After later merges, use **Manual Deploy > Deploy latest commit**.

For Cloud Run instead: build the same `Dockerfile` and deploy it with `--port 8080 --memory 1Gi --max-instances 1`, allowing unauthenticated access. Set no environment variables.

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

## What was measured locally (Oct 27)

| Run | Result |
| --- | --- |
| `--cpus=0.1 --memory=512m`, seed `none` | Dashboard ready 85 s after start, 115 MB memory. `/` 200, `/api/v1/meta` shows demo/mock, and incidents, disputes and payout runs all return 200 |
| `--cpus=2`, seed `week` | Ready in 8 s, and the scripted demo week completed in 56 s, but the mock grew to 813 MB |
| `--cpus=0.1 --memory=512m`, seed `week` | The mock reached the 512 MB limit and the week stopped (`DEMO_STOPPED_REVIEW_CHECKPOINT`) |
| `PAYPAL_CLIENT_ID` set | Refused, exit 64 |
| `COUNTERSIGN_MODE=live` plus the sandbox URL set | Still served demo mode on the mock (forced) |

## Known limits and follow-ups

- **Demo week seed is off by default.** `scripts.demo_week` seeds the mock with `balance_refresh_seconds: 1`, and the mock appends one balance snapshot per mock second (`mock_paypal/resources.py`, `snapshots.append`). Over one staged week that is about 600,000 snapshots: about 800 MB, and too much CPU for a 0.1 CPU host. Either fix allows `COUNTERSIGN_HOSTED_SEED=week` on 512 MB:
  - mock owner: keep only the latest snapshot, or a small bounded number (only the latest is read);
  - demo week owner: seed a coarser refresh, if the demo does not depend on one-second refreshes.
- **Dashboard data are contract fixtures until M15** (`DbSource`). The app already serves the dashboard's API on the same origin, so the page shows "Connected to dashboard API". When M15 and the M17 demo bar land, the hosted instance picks them up with no deploy change.
- **Everything is reset on every wake-up.** This is deliberate: the mock is in-memory, and a clean start always matches it.
