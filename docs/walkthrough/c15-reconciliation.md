# C15: check what PayPal actually did

## 1. What reconciliation is

A webhook is a delivery notification. It may arrive late, twice, or not at all.
The reconciliation poller asks PayPal for the current payout item, dispute and
balance instead. A fresh GET is what confirms a resource's state.

C15 supplies `Reconciler.poll_once()` and a scheduled `Reconciler.run(stop)` loop.
It uses the existing GET-only adapter. It never imports the write adapter, submits
a payout, cancels an item or resolves an UNKNOWN write. C19 owns execution and
identical replays. C20 can call this service; C15 does not change the existing API
process or its inbox worker to start a second background task implicitly.

## 2. Why it keeps money safe

The cycle first tries the same PostgreSQL advisory lock used by owner decisions
and the inbox. A concurrent cycle returns `busy: true` without making requests.
The cycle holds one READ COMMITTED transaction, including its GETs. This is a
deliberately coarse lock for the small vertical slice, not a high-throughput design.
Each resource has a savepoint: its failed update rolls back without losing other
successful observations. A process crash before commit rolls back the whole cycle.

```mermaid
flowchart TD
    A[Scheduled cycle] --> B{Financial lock available?}
    B -->|No| C[Return busy]
    B -->|Yes| D[Optional event-history hints]
    D --> E[GET disputes and payout items]
    E --> F{Confirmed identity and state?}
    F -->|No| G[Keep protection and open incident]
    F -->|Yes| H[Mirror state and apply legal transitions]
    H --> I[Post each confirmed journal once]
    G --> J[Read balances with freshness guards]
    I --> J
    J --> K[Commit observations and financial updates]
```

## 3. How payout attempts are reconciled

PREPARED and UNKNOWN attempts are counted and left untouched for C19. REJECTED
attempts require no resource lookup. SUBMITTED and PayPal states are polled,
including terminal states so later clawbacks and regressions can be noticed.

When an item ID is missing, the poller reads the known batch. It checks the one
item's batch identity, sender keys, receiver and exact Money amount, then GETs the
item itself. A mismatched resource never settles an obligation.

PENDING to SUCCESS moves the line to PAID and obligations to SETTLED, with one
balanced success journal. UNCLAIMED keeps obligations IN_FLIGHT. A confirmed
RETURNED or FAILED item makes its line UNPAID and obligations SCHEDULED. Those
changes and the journal commit together. The ledger's durable event history
prevents a SUCCESS/PENDING/SUCCESS regression from settling or posting twice.
C19 must commit `PAYOUT_SUBMITTED` with its SUBMITTED attempt; missing history
opens an incident rather than inventing the original outflow.

Unclaimed success credits the unclaimed account, where the previous journal put
the money. A confirmed settled clawback reopens obligations and releases their old
allocation. C10 has no PAID-to-UNPAID line event, so the line stays PAID and an
incident asks for review. Historical attempts never change a replacement's current
obligations. If an unexpected old status would violate the one-live-attempt index,
the exact observation stays in `paypal_raw`, with an incident; the index remains.

## 4. How disputes and cash are reconciled

The poller GETs known active and unknown-outcome disputes. It also lists disputes
to discover missing opening notifications, follows distinct next pages, and GETs
each new detail before adoption. `seller_transaction_id` is the capture field
confirmed by D8. Captures outside this marketplace are ignored. A dispute may
already be UNDER_REVIEW at CHARGEBACK; no inquiry-first assumption is made.

Active or unmapped outcomes protect the maker's share. Verified WON or CANCELLED
outcomes release their earmark and reopen the obligation only when no other
dispute still protects it. Full loss voids the original share. Partial loss reopens
it and creates a negative ADJUSTMENT for the refunded maker share, using Money's
half-up rounding. The adjustment has the original due date. A durable audit event
prevents duplicate adjustments after a retry. Conflicting adjustments, changed
resolutions and paid-before-dispute cases retain protection and open incidents.
Dispute amounts must match the linked full order; partial-order disputes remain
unknown rather than applying a guessed loss formula.

The owner-approved C18 fix nets positive and negative OPEN obligations per maker.
A $400 order at 90 percent share, with a $60 buyer refund, therefore plans $306:
$360 original share minus a $54 adjustment. Zero and negative totals stay OPEN.

Production `OUTCOME_MAP` remains empty until D10 recordings verify the codes.
Tests inject explicitly synthetic mappings. D4/D5 payout behavior and D10 refund
funding remain pending. No buyer-refund cash journal is guessed. Instead an
observed refund makes available cash unknown until both `as_of_time` and
`last_refresh_time` cover its first observation. Old balance responses cannot
restore pre-refund spending capacity. Reporting failures leave that protection in
place. Original reported observations are preserved, with safe guard metadata.

Balance rows retain available, withheld and total amounts independently: missing
fields stay NULL. Every currency is validated before any is written. Repeated
observations and older refreshes are skipped. Balances are cross-checks, not proof
that one particular payout or dispute completed.

## 5. Backfill and running the service

Backfill is off by default because D11 has not established sandbox support. When
explicitly enabled, event history supplies resource IDs only. Each hint triggers
a fresh GET; it never becomes a fabricated signature-SUCCESS webhook. Private
policy checkpoints are committed only after successful processing. Pagination
cycles or more than 100 history pages fail safely. Normal resource polling still
runs even when event history is unavailable.

Using the process environment described by C13, the owner can run:

```powershell
.venv\Scripts\python.exe -m countersign.reconcile --once
.venv\Scripts\python.exe -m countersign.reconcile --interval 60
```

`--backfill` is the explicit opt-in flag. The launcher selects a Windows-compatible
Selector loop. Imports read no environment or `.env`; runtime settings come from
the process. One-shot output contains counts only. Scheduled failures log fixed
safe messages. Keep the normal C13 sandbox/local-mock origin restrictions.

## 6. What the tests prove

Tests use isolated PostgreSQL 16 schemas and synthetic responses or the teammate's
in-process mock. One full-poller test deliberately delivers no webhooks: the real
read adapter learns the item ID from the batch, observes PENDING then SUCCESS,
settles once and posts exactly one success journal. Other tests cover UNCLAIMED
accounting, returns, unexpected moves, rollback, old replacement attempts, missing
submission journals, unknown outcomes, partial losses, discovery, pagination,
balance NULLs, stale refund cash, backfill checkpoints and concurrent cycles.

```powershell
$env:COUNTERSIGN_TEST_DATABASE_URL = 'postgresql://countersign:countersign-dev@127.0.0.1:55432/countersign'
.venv\Scripts\python.exe -m pytest tests/integration/reconcile tests/integration/approvals -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\lint-imports.exe
```

The example password is the existing local-development default, not a credential
for PayPal. Database cases skip when the test URL is unset. No test reads `.env`
or calls the real sandbox. The dashboard remains on fixtures until M15; C09's
recording contract and optional Toolkit checks retain their existing skips.
