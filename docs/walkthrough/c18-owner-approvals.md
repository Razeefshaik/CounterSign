# C18: planning a run and approving exactly that work

## 1. What this adds

The owner can plan a weekly payout run, approve or reject a proposal, and correct a
maker's payout email. Every write uses a PostgreSQL transaction. These endpoints
make no PayPal call and never consume an approval; C19 owns execution and consumption.

## 2. What you (the owner) need

Use PostgreSQL 16 with migrations through 0003. `main.py` mounts both routers under
`/api/v1`. The database and settings are supplied to each router's
`build_router(database, settings)` function. Live mode means the PayPal sandbox and
requires `X-Owner-Token`; only demo mode using the validated local mock skips auth.
Settings come from the running process. This code never opens `.env`.

## 3. What the code does, step by step

1. `POST /payout-runs` with `week_start` selects positive OPEN obligations due by
   that week's Sunday, excluding active allocations, dispute earmarks and orders
   with ACTIVE or UNKNOWN_OUTCOME disputes. It groups them by maker and refuses a
   mixed-currency run. Dates are calendar dates, amounts integer minor units.
2. The PLAN schema has no draft-line state. Planning creates APPROVED lines,
   schedules their obligations and creates a NEEDS_OWNER proposal. The run stays
   AWAITING_OWNER. These line names grant no permission to execute the proposal.
3. The shield checks the complete proposed run. Any BLOCK rolls back everything.
   Success reserves PROPOSAL_CASH; the owner still must approve. Missing cash
   observations also roll back instead of inventing an available balance.
4. Approval checks the displayed hash, the saved versions, expiry and all current
   shield rules under locks. A changed record or contents produces HTTP 409
   STALE_PROPOSAL; expiry produces HTTP 409 APPROVAL_EXPIRED. A new blocking fact
   produces SHIELD_BLOCKED. The proposal becomes APPROVED, the run becomes APPROVED,
   and its cash reservation changes to RUN_CASH in the same transaction.
5. The saved approval binds `Snapshot.record_versions()` after approval-time
   transitions, with a 30-minute default lifetime measured from planning. Both
   `used_at` and `used_by_operation_id` stay null. C19 must check and consume these
   atomically with the operation's execution permission.
6. Rejection releases pending cash, allocations and scheduled obligations. Email
   correction requires `confirm: true`, validates the address, records verification
   time and bumps the maker version; existing approvals consequently become stale.

## 4. What gets saved, and where

`proposals.raw_output.approval_request` holds `payload_hash`, `record_versions` and
`expires_at` for the dashboard. The same binding is in
`verdict_details.approval_request`; `verdict_details.rule_results` contains every
shield result. `raw_output.operation_payload` is the exact reproducible manifest:

```json
{
  "action": "SCHEDULE_PAYOUT_RUN",
  "params": {"run_id": "<uuid>", "lines": [{"line_id": "<uuid>",
    "maker_id": "<maker uuid>", "amount": {"minor": 900, "currency": "USD"}}],
    "replacements": []},
  "payouts": [{
    "line_id": "<uuid>",
    "request_id": "cs-<line uuid>-a1",
    "body": {
      "sender_batch_header": {"sender_batch_id": "cs-<line uuid>-a1"},
      "items": [{"recipient_type": "EMAIL", "receiver": "<verified maker email>",
        "amount": {"value": "9.00", "currency": "USD"},
        "sender_item_id": "cs-<line uuid>-a1-i1"}]
    }
  }]
}
```

Recipients come only from locked maker records, never proposal params. Each line
uses `shield.rules.idempotency.derive_keys`. The hash uses the shared
`paypal.http.canonical_payload_hash`: sorted keys, compact UTF-8 JSON, Unicode kept
as Unicode and NaN refused. The API uses `sha256:<hex>`; approvals store its 32 bytes.
The dashboard's existing SQL masks private addresses. This service prints nothing.
The write endpoints also return these masked contract documents, rather than raw
storage rows. Run params include line amounts and the expected cash change, so the
owner can review what is being approved. Audit kinds are `OWNER_APPROVED` and
`OWNER_REJECTED`; rejection audit details record only whether a reason was supplied,
so free text cannot copy an email or credential into the audit log.
Storage errors return a fixed `OWNER_STORAGE_UNAVAILABLE` response with status
503; private database exception values never become HTTP error details.
All owner errors use the CONTRACTS `error.code` and `error.message` envelope;
validation errors do not echo the submitted request values.

## 5. How correctness is decided

Integration tests use isolated migrated schemas and fake records. They cover exact
binding, byte conversion, changed obligations and makers, wrong hashes, expiration,
duplicate and concurrent approval, owner auth, confirmed email changes, rejection
cleanup, excluded disputed shares and full rollback on missing cash, insufficient
funding or an unverified recipient. Without COUNTERSIGN_TEST_DATABASE_URL they skip.

## 6. Decisions and the executor handoff

`ApprovalService` exposes `plan_run(date)`, `approve(UUID, hash, approved_by)`,
`reject(UUID, reason)` and `set_payout_email(UUID, email)`. `load_context(conn, row,
now=...)` returns the shield proposal, frozen snapshot and current canonical
manifest. It locks existing money rows in a fixed order; C18 writers also serialize
with transaction advisory key `countersign:owner-decisions`. Planning is deliberately
conservative and locks all existing money rows for this slice. Other financial
writers must follow compatible locking and shield re-checks; this is not a claim
that an advisory lock constrains unrelated SQL clients.

Payout runs have no C10 machine. Their owner transitions use locked state checks
plus version-guarded repository updates. Obligation, line and proposal transitions
use their C10 machines through database CAS.
Public PayPal dispute and payout-item IDs resolve to locked internal rows before
building the snapshot. The approval therefore binds the internal dispute or
attempt version, and conflicting or missing identities fail closed.

**C19 integration decision still required:** one owner approval binds the complete
run manifest, while each PayPal call has one line's payload. Existing shield
WRITE_AHEAD compares operation and proposal hashes, and approval binding checks
unused status. Claude must align the full-run approval with per-line operations
and its atomic consumption protocol. C18 does not invent child approvals or mark
the full-run approval used. The single-use columns alone are not enforcement.

The dashboard continues using fixtures until M15. These tests establish database
owner actions; they do not establish real PayPal behavior or C09 recording parity.
