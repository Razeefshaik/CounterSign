# C11: Local PostgreSQL and dashboard views

## 1. What this change proves

The dashboard can read the marketplace's database through a fixed set of views.
Each view builds the JSON document agreed in CONTRACTS section 2. A separate
database role can read those documents, but cannot read the tables containing
raw PayPal responses, payout addresses, or credentials.

The compose file starts PostgreSQL 16 on this computer's loopback interface.
Its named volume preserves data when the container restarts. Its fallback
password is for local development only.

## 2. What you (the owner) will do

Start Docker Desktop, start PostgreSQL, and run the migrations. These steps
create only local database resources; they make no PayPal requests. The
integration test database URL is supplied through the process environment.
Tests skip when `COUNTERSIGN_TEST_DATABASE_URL` is absent.

## 3. What the migration does, step by step

1. Revision `0001` creates the PLAN tables and database safety constraints.
2. Revision `0002` adds the ten `v_dash_*` views, including incident/run detail,
   proposals, makers, disputes, reservations, cash, and the audit log.
3. JSON money values keep integer minor units. Timestamps use UTC with a `Z`
   suffix. The views mask email addresses recursively and remove sensitive
   token, secret, password and authorization fields from JSON documents.
4. Cash follows the agreed formula: subtract submitted outflows newer than the
   snapshot's refresh time, then active cash reservations. Dispute earmarks
   appear separately and are never subtracted again. Unknown outflow amounts
   prevent the cash view from returning a misleading total.
5. `dash_money_graph(dispute_id, maker_id, run_id)` accepts exactly one root and
   traverses the connected order, obligation, maker, line, attempt and reservation
   relationships. Run roots include the prior lines scheduled as replacements.
6. `countersign_agent_ro` is a NOLOGIN role with schema usage, SELECT on the
   dashboard views, and EXECUTE on the graph function. It has no base-table
   grants. The graph function fixes its search path to its installation schema
   with `pg_temp` last; callers cannot substitute tables through their search
   path. Provisioning an actual login and assigning the role is a deployment
   step outside this card.

## 4. What gets saved, and where

- `docker-compose.yml`: PostgreSQL service, health check and named volume.
- `migrations/versions/0002_dashboard_views.py`: SQL views and graph function.
- The database: view definitions and the shared NOLOGIN role.

No new presentation columns were added to the agreed schema. Incident titles,
amounts and deadlines come from `incidents.subject`; proposal flags and pending
approval requests come from `proposals.raw_output`; observed/staged dispute
metadata comes from `disputes.raw`. Order display references use
`orders.tracking.order_ref`, falling back to PayPal's order ID and then the local
ID. Run creation time comes from its latest schedule proposal. Missing
observations remain null, including an unobserved dispute hold.

The current cash singleton and maker/run summary totals cover the USD vertical
slice. Non-USD obligations never get added to USD totals, and runs containing
non-USD lines or replacements are omitted. With no complete USD balance
snapshot, the cash view returns no row. This represents unavailable data rather
than inventing an available balance.

## 5. How completion is checked

Integration tests migrate an isolated schema, seed related records, and validate
each view's documented example using the mate-owned JSON schemas. They also verify the
graph's relationships, cash arithmetic, email masking and the role's refusal to
read or write base tables. Schema tests separately prove that a second live
attempt fails and an unbalanced journal cannot commit. Each test removes its
own schema. Downgrading leaves the shared NOLOGIN role in place so independent
schemas can continue using it.

Those fixture schemas currently describe some examples too narrowly. The
reservation schema requires non-null dispute and obligation IDs even for cash
reservations. The schedule-proposal schema requires a pending approval request
even after execution, when CONTRACTS says to omit it. Tests validate the matching
example states, then check these legitimate null associations and executed
proposal fields separately. The views preserve the contract's meaning; no IDs
or approval requests are invented to satisfy a fixture checker. A run without
a schedule proposal has an unknown creation time, rather than an invented one.

## 6. How to run it

From the project root, with Docker Desktop running:

```powershell
docker compose --env-file NUL up -d postgres
```

`--env-file NUL` prevents compose from automatically reading the project's
`.env`. Provide the local database URL through `DATABASE_URL` at runtime, then:

The default published port is 5432. Set `POSTGRES_PORT` in the process environment
to use another local port, for example 55432 when Windows reserves 5432. Match
that port in both database URL environment variables.

```powershell
.venv\Scripts\python.exe -m alembic -c migrations/alembic.ini upgrade head
.venv\Scripts\python.exe -m pytest -q tests/integration/test_schema.py
```

The second command runs against PostgreSQL only when
`COUNTERSIGN_TEST_DATABASE_URL` is set; otherwise the database tests are skipped.
