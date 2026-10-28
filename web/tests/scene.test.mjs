import test from "node:test";
import assert from "node:assert/strict";
import {sceneModel, sceneSvg, focusModel, focusSvg, defaultCategory, nodePanel, makerPanel, itemPanel, storyPanel, kpis, rail, short} from "../scene.mjs";
import {weekDays} from "../words.mjs";

// Shapes follow GET /api/v1/makers, /money-graph?maker_id= and /demo/comparison
// (CONTRACTS 2 and 3), with values from the staged Friday after planning.
const usd = minor => ({minor, currency: "USD"});
const maker = (id, name, f = {}) => ({id, display_name: name, payout_email_masked: "m***@example.test", payout_email_verified: true,
  owed_open: usd(f.owed || 0), reserved: usd(f.held || 0), scheduled: usd(f.scheduled || 0), in_flight: usd(f.inFlight || 0), settled_last_30d: usd(f.paid || 0)});
const friday = [maker("a", "Maker A", {held: 36000}), maker("b", "Maker B", {held: 5400, scheduled: 48600}), maker("c", "Maker C", {scheduled: 60000})];
const graphA = {nodes: [
  {id: "dispute:d1", type: "dispute", label: "PP-D-0000000EA3F951436D1F", state: "ACTIVE", amount: usd(40000)},
  {id: "maker:a", type: "maker", label: "Maker A", state: null, amount: null},
  {id: "obligation:o1", type: "obligation", label: "Maker A share", state: "RESERVED", amount: usd(36000)},
  {id: "order:r1", type: "order", label: "01A183935F1B78B5B366AABAD5C81F25", state: null, amount: usd(40000)},
  {id: "reservation:h1", type: "reservation", label: "Dispute earmark", state: "ACTIVE", amount: usd(36000)},
  {id: "reservation:h0", type: "reservation", label: "Dispute earmark", state: "RELEASED", amount: usd(9900)}],
edges: [{from: "dispute:d1", to: "order:r1", type: "disputes"}, {from: "obligation:o1", to: "maker:a", type: "owed_to"},
  {from: "order:r1", to: "obligation:o1", type: "creates"}, {from: "reservation:h1", to: "obligation:o1", type: "holds"}]};
const waiting = {id: "p1", action: "SCHEDULE_PAYOUT_RUN", state: "NEEDS_OWNER", verdict: "NEEDS_OWNER",
  rule_results: [{rule: "SETTLEMENT", outcome: "PASS"}, {rule: "APPROVAL_BINDING", outcome: "NEEDS_OWNER"}]};
const blocked = {id: "p2", action: "ACCEPT_CLAIM", state: "BLOCKED", verdict: "BLOCK", rule_results: [{rule: "PAYEE_INTEGRITY", outcome: "BLOCK"}]};
const comparison = {available: true, naive: {payout: usd(150000), disputed_shares: usd(41400)}, countersign: {total: usd(108600)},
  moments: {lost_response: {payment_count: 1, exactly_one: true}, injection: {block_rules: ["INJECTION_GUARD", "PAYEE_INTEGRITY"]}, settled_retry: null}};
const sparksOn = (svg, id) => (svg.match(new RegExp(`<mpath href="#${id}"`, "g")) || []).length;

test("the scene sums only API amounts and shows a dash for a balance it has not read", () => {
  const model = sceneModel({makers: friday, proposals: [waiting], graphs: {a: graphA}});
  assert.equal(model.held.total, 41400);
  assert.equal(model.totals.paying, 108600);
  assert.equal(model.totals.inFlight, 0);
  assert.equal(model.paypal.available, null);
  assert.match(kpis(model), /PayPal available<\/dt><dd data-count="">—/);
  assert.match(kpis(model), /\$1,086/);
  assert.deepEqual(model.makers.map(m => m.status), ["held", "scheduled", "scheduled"]);
  assert.equal(short(33333), "$333.33");
  assert.equal(short(108600), "$1,086");
  assert.equal(short(undefined), "—");
});

test("only money sent to PayPal or already paid gets moving particles; a scheduled run waits", () => {
  const planned = sceneSvg(sceneModel({makers: friday, proposals: [waiting]}));
  assert.equal(sparksOn(planned, "e-m1"), 0);
  assert.equal(sparksOn(planned, "e-paypal"), 0);
  assert.match(planned, /\$486 scheduled/);
  assert.match(planned, /needs your signature/);
  const sent = sceneSvg(sceneModel({makers: [maker("c", "Maker C", {inFlight: 30000}), maker("b", "Maker B", {paid: 48600})]}));
  assert.equal(sparksOn(sent, "e-m0"), 4);
  assert.equal(sparksOn(sent, "e-m1"), 2);
  assert.match(sent, /\$300 in flight/);
  assert.match(sent, /\$486 paid/);
  assert.equal(sparksOn(sceneSvg(sceneModel({makers: friday}), {motion: false}), "e-held"), 0);
  assert.ok(!sceneSvg(sceneModel({makers: [maker("c", "C", {inFlight: 1})], proposals: [blocked]}), {motion: false}).includes("<animate"));
});

test("blocked proposals burst red at the shield and the ring shows the decision waiting for the owner", () => {
  const model = sceneModel({makers: friday, proposals: [blocked, waiting]});
  assert.equal(model.shield.focus.id, "p1");
  assert.equal(model.shield.ticks.length, 16);
  assert.equal(model.shield.ticks.find(t => t.code === "APPROVAL_BINDING").outcome, "NEEDS_OWNER");
  const svg = sceneSvg(model);
  assert.match(svg, /1 blocked/);
  assert.match(svg, /class="spark red"/);
  const panel = nodePanel(model, "shield");
  assert.equal((panel.match(/<li class=/g) || []).length, 16);
  assert.match(panel, /#decision\/p1/);
});

test("untrusted names and emails stay inert in the scene and panels", () => {
  const evil = maker("x", '<img src=x onerror=alert(1)>', {paid: 100});
  evil.payout_email_masked = "victim@example.com";
  const model = sceneModel({makers: [evil]});
  for (const html of [sceneSvg(model), makerPanel(model.makers[0])]) {
    assert.ok(!html.includes("<img"));
    assert.ok(!html.includes("victim@example.com"));
  }
});

test("the drill-down lists one maker's records, counts only active holds and explains links", () => {
  const model = sceneModel({makers: friday, graphs: {a: graphA}});
  assert.deepEqual(model.makers[0].orders.map(o => [o.ref, o.dispute?.phase]), [["…C81F25", "ACTIVE"]]);
  const fm = focusModel(graphA, model.makers[0]);
  assert.deepEqual(fm.categories.map(c => [c.type, c.items.length]), [["order", 1], ["obligation", 1], ["dispute", 1], ["reservation", 2]]);
  assert.equal(fm.categories.find(c => c.type === "reservation").total, 36000);
  assert.equal(defaultCategory(fm), "dispute");
  const svg = focusSvg(fm, {category: "dispute"});
  assert.match(svg, /Dispute PP-D …436D1F/);
  const panel = itemPanel(fm, "obligation:o1");
  assert.match(panel, /This is owed to/);
  assert.match(panel, /Linked: holds back/);
  // A graph that is not rooted at this maker is never attributed to it.
  const other = focusModel(graphA, model.makers[1]);
  assert.ok(other.categories.every(c => c.items.length === 0));
});

test("the week story shows the recorded comparison and only the safety moments that happened", () => {
  const model = sceneModel({makers: friday, proposals: [waiting]});
  const demo = {step: 6, total: 11, steps: [{label: "Friday: plan the run", done: true}, {label: "Friday: owner approves", done: false}]};
  const html = storyPanel({demo, comparison, model});
  for (const part of ["Step 6 of 11", "Plan the run", "$1,500", "$1,086", "$414", "Weekly payout run", "#decision/p1"]) assert.ok(html.includes(part), part);
  assert.match(html, /exactly 1 payment/);
  assert.equal((html.match(/<li class="done">/g) || []).length, 2);
  assert.match(html, /Not reached yet/);
  assert.ok(!storyPanel({demo, comparison: null, model}).includes("Naive agent"));
});

test("the timeline rail marks finished days and the day of the next step", () => {
  const steps = [["Monday: a", true], ["Wednesday: b", true], ["Friday: c", false], ["Friday: d", false]].map(([label, done], i) => ({index: i + 1, label, done}));
  const html = rail({steps}, weekDays);
  assert.match(html, /<li class="done"><span class="rail-day">Monday/);
  assert.match(html, /<li class="current"><span class="rail-day">Friday/);
  assert.equal((html.match(/<i class="next"/g) || []).length, 1);
  assert.equal(rail(null, weekDays), "");
});
