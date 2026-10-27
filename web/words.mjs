// Plain-English names for contract codes. Codes stay visible next to these names;
// unknown codes fall back to title case so a new rule or state is never hidden.
import {label, money} from "./data.mjs";

export const RULES = {
  SETTLEMENT: "Not already paid",
  PAYOUT_STATE_MACHINE: "Legal payout state change",
  CANCEL_BEFORE_REPLACE: "Old payout returned before a resend",
  IDEMPOTENCY: "Same keys on every retry",
  WRITE_AHEAD: "Recorded before it is sent",
  DISPUTE_RESERVE: "Disputed shares held back",
  FUNDING: "Enough unreserved cash",
  PAYEE_INTEGRITY: "Paying the verified account",
  REFUND_POLICY: "Within the refund policy",
  EVIDENCE_INTEGRITY: "Evidence matches the order",
  DEADLINE_GUARD: "Inside the response deadline",
  UNKNOWN_SCOPED: "No unresolved payment in the way",
  APPROVAL_BINDING: "Owner approves this exact payload",
  RECHECK_AT_EXECUTION: "Checked again just before sending",
  WEBHOOK_AUTHENTICITY: "Webhook signature verified",
  INJECTION_GUARD: "No instructions taken from untrusted text",
};

export const ACTIONS = {
  WAIT: "Wait for PayPal",
  DRAFT_MESSAGE: "Draft a note",
  PROVIDE_EVIDENCE: "Send dispute evidence",
  CANCEL_UNCLAIMED_PAYOUT: "Cancel an unclaimed payout",
  RESERVE_EARNINGS: "Hold back earnings",
  RELEASE_RESERVE: "Release held earnings",
  SCHEDULE_PAYOUT_RUN: "Weekly payout run",
  CREATE_REPLACEMENT_PAYOUT: "Resend a payout",
  DEFER_SCHEDULED_PAYOUT: "Defer a scheduled payout",
  ACCEPT_CLAIM: "Refund the buyer",
  REQUEST_OWNER: "Ask the owner",
};

export const STATES = {
  OPEN: "Open",
  INVESTIGATING: "Investigating",
  AWAITING_OWNER: "Awaiting owner",
  NEEDS_OWNER: "Needs your approval",
  EXECUTING: "Sending",
  WAITING_EXTERNAL: "Waiting on PayPal",
  RESOLVED: "Resolved",
  ALLOW: "Allowed",
  ALLOWED: "Allowed",
  BLOCK: "Blocked",
  BLOCKED: "Blocked",
  BLOCKED_AT_EXECUTION: "Blocked at sending",
  PASS: "Pass",
  APPROVED: "Approved",
  REJECTED: "Rejected",
  EXPIRED: "Expired",
  EXECUTED: "Done",
  SUCCEEDED: "Succeeded",
  FAILED: "Failed",
  SUCCESS: "Paid",
  PENDING: "Pending",
  UNCLAIMED: "Unclaimed",
  RETURNED: "Returned",
  ONHOLD: "On hold",
  SCHEDULED: "Scheduled",
  RESERVED: "Held back",
  SETTLED: "Settled",
  IN_FLIGHT: "In flight",
  PAID: "Paid",
  PAYING: "Paying",
  UNPAID: "Unpaid",
  ACTIVE: "Active",
  CONSUMED: "Used",
  RELEASED: "Released",
  DONE: "Done",
  CANCELLED: "Cancelled",
  UNKNOWN: "Unknown",
  FRESH: "Fresh",
  STALE: "Stale",
};

export const KINDS = {
  DISPUTE: "Dispute",
  PAYOUT_ITEM: "Payout",
  RUN_AT_RISK: "Run at risk",
  RECONCILIATION: "Reconciliation",
};

export const rule = code => RULES[code] || label(code);
export const action = code => ACTIONS[code] || label(code);
export const state = code => STATES[code] || label(code);
export const kind = code => KINDS[code] || label(code);

// Tone drives colour only together with the visible word, never alone.
export function tone(code) {
  if (["PASS", "ALLOW", "ALLOWED", "SUCCESS", "SUCCEEDED", "EXECUTED", "PAID", "SETTLED", "RESOLVED", "FRESH", "DONE", "APPROVED"].includes(code)) return "good";
  if (["BLOCK", "BLOCKED", "BLOCKED_AT_EXECUTION", "FAILED", "REJECTED", "STALE", "UNKNOWN"].includes(code)) return "bad";
  if (["NEEDS_OWNER", "AWAITING_OWNER", "RESERVED", "UNCLAIMED", "ONHOLD", "EXPIRED"].includes(code)) return "owner";
  return "quiet";
}

// "Fri 06 Nov, 09:02 UTC". Microseconds and offsets stay in the ISO title attribute.
export function when(value, {year = false} = {}) {
  const time = Date.parse(value);
  if (!Number.isFinite(time)) return "—";
  const date = new Date(time);
  const parts = date.toLocaleString("en-GB", {weekday: "short", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC", ...(year ? {year: "numeric"} : {})});
  return parts.replace(",", "").replace(/ (\d{2}:\d{2})$/, ", $1") + " UTC";
}

// Long identifiers are shown short; the full value goes in a title or technical details.
export function shortId(value) {
  const text = String(value ?? "");
  if (/^#\d+$/.test(text) || text.length <= 14) return text;
  return "…" + text.replace(/-/g, "").slice(-6).toUpperCase();
}

const list = items => items.length < 2 ? items.join("") : items.slice(0, -1).join(", ") + " and " + items.at(-1);
const maker = (makers, id) => makers?.[id] || "a maker";

// One sentence a first-time reader understands. Amounts come only from the records.
export function summarize(proposal, {run = null, makers = {}} = {}) {
  if (!proposal) return "";
  const p = proposal.params || {};
  const change = proposal.expected_effect?.available_change;
  switch (proposal.action) {
    case "SCHEDULE_PAYOUT_RUN": {
      if (run?.lines) {
        const pays = run.lines.map(line => `${line.maker.display_name} ${money(line.amount)}`);
        const resends = (run.replacements || []).map(r => `${r.maker.display_name}'s returned ${money(r.amount)} payout`);
        let text = `Pay ${list(pays)}${resends.length ? `, and resend ${list(resends)}` : ""}. Total ${money(run.total)}.`;
        const held = (run.reserved || []).map(r => `${r.maker.display_name} ${money(r.amount)}`);
        if (held.length) text += ` Held back: ${list(held)}, because their orders are disputed.`;
        return text;
      }
      const count = (p.lines || []).length + (p.replacements || []).length;
      return `Pay ${count} payout line${count === 1 ? "" : "s"}${change ? ` for ${money({...change, minor: Math.abs(change.minor)})}` : ""}.`;
    }
    case "CANCEL_UNCLAIMED_PAYOUT":
      return "Cancel a payout nobody claimed, so the money returns before anything is resent.";
    case "PROVIDE_EVIDENCE":
      return `Send proof of fulfilment to PayPal for dispute ${p.paypal_dispute_id || ""}.`.replace(" .", ".");
    case "ACCEPT_CLAIM":
      return p.refund_to
        ? "Refund the buyer to an account the buyer named in a message. Refunds only go back through PayPal's original route."
        : `Refund the buyer by accepting dispute ${p.paypal_dispute_id || ""}. Refunds need your approval.`.replace(" .", ".");
    case "DRAFT_MESSAGE":
      return "Draft a note for the owner. Nothing is sent and no money moves.";
    case "WAIT":
      return "Wait. Nothing changes until PayPal reports something new.";
    case "REQUEST_OWNER":
      return "Ask the owner a question before doing anything.";
    case "CREATE_REPLACEMENT_PAYOUT":
      return `Resend an earlier payout${p.line_id ? ` for ${maker(makers, p.maker_id)}` : ""}.`;
    case "RESERVE_EARNINGS":
      return "Hold back a maker's earnings until a dispute is settled.";
    case "RELEASE_RESERVE":
      return "Release earnings that were held back for a dispute.";
    case "DEFER_SCHEDULED_PAYOUT":
      return "Move a scheduled payout to a later run.";
    default:
      return action(proposal.action) + ".";
  }
}

// Days of the staged week, taken from step labels such as "Friday: plan the run".
export function weekDays(steps = []) {
  const days = [];
  for (const step of steps) {
    const name = /^([^:]{3,20}):/.exec(step.label || "")?.[1]?.trim() || "Week";
    let day = days.at(-1);
    if (!day || day.name !== name) days.push(day = {name, steps: []});
    day.steps.push(step);
  }
  return days;
}
