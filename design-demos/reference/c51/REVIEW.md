# C51 read-only review of 93f0e35

Reviewed on November 3, 2026 after restoring the pushed cloud branch locally.
No runtime code or test assertions were changed during this review/validation.

## P2: plain approval can impersonate the signed-timeline label

`web/views.mjs:79` treats an `approval.approved_by` starting with
`owner:passkey:` as proof of a countersignature. The existing plain endpoint
accepts arbitrary `approved_by` (`src/countersign/api/owner.py:26`) and forwards
it unchanged to the approval service (`:119`). Consequently an authenticated
owner, or anyone using the intentionally unauthenticated mock demo, can post a
plain approval with that prefix and obtain the signed timeline without WebAuthn.

Reproduced against the real owner HTTP endpoint, unchanged ApprovalService and
an isolated PostgreSQL schema containing an eligible payout proposal:

```json
{
  "plain_approval_http_status": 201,
  "recorded_passkey_prefix": true,
  "registered_keys": 0,
  "verified_countersign_audits": 0
}
```

Rendering that approval through the actual `decisionView` returned the
"Countersigned with passkey" label. No passkey was registered, no assertion was
submitted and no `OWNER_COUNTERSIGNED` event existed. No provider call was made;
the disposable test schema was removed after reproduction.

The ordinary state/hash/expiry/version/shield checks still apply; this finding
concerns the provenance of the new cryptographic-signature claim. The signed
label should be derived from authoritative verified-countersignature metadata,
or the plain approval endpoint should reserve/refuse the passkey identity prefix.
Add a regression covering a plain approval attempting to use that prefix.

The issue is left for owner review because this continuation requested full
validation of the pushed implementation and preservation of existing work.
No fix, merge, deployment or environment configuration change was made.
