import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {rule, action, state, tone, when, shortId, summarize, weekDays, RULES} from "../words.mjs";
import {incidentTitle, incidentTable, ownerCallout, decisionView, checksList} from "../views.mjs";
import {trickView} from "../trick.mjs";
import {summary} from "../data.mjs";
import {demoBarView} from "../demo.mjs";

const fixture = async name => JSON.parse(await readFile(new URL(`../fixtures/${name}.json`, import.meta.url)));
const run = (await fixture("run-details"))[0];
const proposals = (await fixture("proposals")).items;
const owner = proposals.find(p => p.state === "NEEDS_OWNER");
const meta = await fixture("meta");

test("every shield rule has a plain name and unknown codes stay readable", () => {
  for (const code of ["SETTLEMENT", "IDEMPOTENCY", "FUNDING", "DISPUTE_RESERVE", "PAYEE_INTEGRITY", "INJECTION_GUARD", "APPROVAL_BINDING"]) assert.notEqual(rule(code), code);
  assert.equal(Object.keys(RULES).length, 16);
  assert.equal(rule("NEW_RULE_X"), "New Rule X");
  assert.equal(state("WAITING_EXTERNAL"), "Waiting on PayPal");
  assert.equal(state("NEEDS_OWNER"), "Needs your approval");
  assert.equal(action("SCHEDULE_PAYOUT_RUN"), "Weekly payout run");
  assert.equal(tone("BLOCK"), "bad"); assert.equal(tone("PASS"), "good"); assert.equal(tone("NEEDS_OWNER"), "owner");
});

test("times drop microseconds and identifiers are shortened, never invented", () => {
  assert.equal(when("2026-11-05T09:02:00.000000Z"), "Thu 05 Nov, 09:02 UTC");
  assert.equal(when("not a time"), "—");
  assert.equal(shortId("#1042"), "#1042");
  assert.equal(shortId("01A17C48CAA67971BD81E49299328468"), "…328468");
});

test("the payout-run sentence names who is paid, who is held back and the total, from the run", () => {
  const text = summarize(owner, {run});
  for (const part of ["Maker B $486.00", "Maker C $300.00", "resend Maker C's returned $300.00 payout", "Total $1,086.00", "Held back", "disputed"]) assert.ok(text.includes(part), part);
  assert.match(summarize(owner), /^Pay 3 payout lines for \$1,086\.00\.$/);
  assert.match(summarize({action: "ACCEPT_CLAIM", params: {paypal_dispute_id: "PP-D-1", refund_to: "x@y.test"}}), /account the buyer named/);
  assert.equal(summarize({action: "SOMETHING_NEW", params: {}}), "Something New.");
});

test("decision page leads with the sentence and keeps the full hash in technical details", () => {
  const html = decisionView(owner, meta, false, {run});
  assert.ok(html.indexOf("Maker B $486.00") < html.indexOf("checks before any money moves"));
  assert.ok(html.includes(owner.approval_request.payload_hash));
  assert.match(html, /Technical details: exactly what the owner signs/);
  assert.match(html, /How this was decided/);
  assert.match(html, /Deterministic planner|Cited records|Read/);
});

test("incident titles hide internal prefixes and long PayPal ids", () => {
  assert.deepEqual(incidentTitle({title: "Staged goal: cancel the unclaimed payout"}), {text: "Cancel the unclaimed payout", goal: true});
  assert.equal(incidentTitle({title: "Dispute PP-D-0000000EA3F951436D1F · Maker A · $400.00"}).text, "Dispute PP-D …436D1F · Maker A · $400.00");
  const html = incidentTable([{id: "i1", kind: "DISPUTE", state: "OPEN", title: '<img src=x> a@b.test', amount_at_risk: null}]);
  assert.ok(!html.includes("<img") && !html.includes("a@b.test"));
  assert.match(html, /class="row-title"/);
  assert.equal(incidentTable([]), "");
});

test("decisions waiting count payout runs, not only incidents", () => {
  assert.equal(summary([], [{state: "NEEDS_OWNER"}, {state: "EXECUTED"}]).owner, 1);
  assert.equal(summary([{state: "AWAITING_OWNER"}]).owner, 1);
  assert.match(ownerCallout(owner, {run}), /Waiting for your approval: Weekly payout run/);
  assert.match(ownerCallout(owner, {run}), new RegExp(`#decision/${owner.id}`));
  assert.equal(ownerCallout(null), "");
});

test("the week stepper groups steps by day and marks done and next", () => {
  const steps = [["Monday: a", true], ["Monday: b", true], ["Friday: c", false], ["Following week: d", false]]
    .map(([label, done], i) => ({index: i + 1, label, staged: true, done}));
  assert.deepEqual(weekDays(steps).map(d => [d.name, d.steps.length]), [["Monday", 2], ["Friday", 1], ["Following week", 1]]);
  const html = demoBarView({step: 2, total: 4, steps, clock: null, model: "scripted"});
  assert.equal((html.match(/class="tick done"/g) || []).length, 2);
  assert.equal((html.match(/class="tick next"/g) || []).length, 1);
  assert.match(html, /<li class="current"[^>]*><span class="day">Friday/);
});

test("the trick page reports the verdict honestly and never claims money moved", () => {
  const attacks = [{id: "custom", title: "Write your own", message: null, available: true, needs: "x"},
    {id: "retry_settled_payout", title: "Again", message: "m", available: false, needs: "a paid payout line"}];
  const result = {verdict: "BLOCK", message_flags: [], proposal: {action: "ACCEPT_CLAIM", params: {refund_to: "s***@x.test"}},
    rule_results: [{rule: "PAYEE_INTEGRITY", outcome: "BLOCK", detail: "not the verified account"}], written: false};
  const html = trickView({attacks, chosen: "custom", message: '<img src=x> secret@example.com', result});
  assert.match(html, /Blocked\. No money would move\./);
  assert.match(html, /Nothing was written/);
  assert.match(html, /did not flag this message/);
  assert.match(html, /PAYEE_INTEGRITY/);
  assert.match(html, /Available after a paid payout line/);
  assert.ok(!html.includes("<img") && !html.includes("secret@example.com"));
  assert.match(trickView({attacks, chosen: "custom", message: ""}), /id="trick-run" class="primary" disabled/);
  assert.match(checksList([]), /No rule results recorded/);
});
