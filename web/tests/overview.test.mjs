import test from "node:test";
import assert from "node:assert/strict";
import {sceneModel} from "../scene.mjs";
import {makerList, kpiCards, stands, actionStrip, weekLabel, headline, finding, CARD_LIMIT, PAGE} from "../plot.mjs";

// C42 command center pieces. Shapes follow GET /api/v1/makers, /proposals and /cash
// (CONTRACTS 2); values are the staged Friday after planning.
const usd = minor => ({minor, currency: "USD"});
const maker = (id, name, f = {}) => ({id, display_name: name, payout_email_masked: "m***@example.test", payout_email_verified: true,
  owed_open: usd(f.owed || 0), reserved: usd(f.held || 0), scheduled: usd(f.scheduled || 0), in_flight: usd(f.inFlight || 0), settled_last_30d: usd(f.paid || 0)});
const friday = [maker("a", "Maker A", {held: 36000}), maker("b", "Maker B", {held: 5400, scheduled: 48600}), maker("c", "Maker C", {scheduled: 60000})];
const run = {id: "p1", action: "SCHEDULE_PAYOUT_RUN", state: "NEEDS_OWNER", verdict: "NEEDS_OWNER",
  rule_results: [{rule: "SETTLEMENT", outcome: "PASS"}, {rule: "FUNDING", outcome: "PASS"}, {rule: "APPROVAL_BINDING", outcome: "NEEDS_OWNER"}]};
const cash = {paypal: {available: usd(134000), stale: false}, unreserved: usd(0)};
const crowd = n => Array.from({length: n}, (_, i) => maker("m" + i, "Maker " + i, i % 5 === 0 ? {scheduled: 10000} : i % 5 === 1 ? {held: 5000} : {owed: 2500}));

test("a few makers read as cards; each card opens its records in the money map", () => {
  const html = makerList(sceneModel({makers: friday, proposals: [run]}));
  assert.equal((html.match(/class="mcard /g) || []).length, 3);
  assert.ok(!html.includes("<table"));
  assert.match(html, /data-node="maker:c"[^>]*aria-label="Maker C: \$600 to sign\. Waiting for your signature\./);
  assert.match(html, /Maker B[\s\S]*\$54 held for a dispute/);
  // Waiting for the owner comes first, then held money.
  assert.ok(html.indexOf("Maker C") < html.indexOf("Maker A") && html.indexOf("Maker B") < html.indexOf("Maker A"));
});

test("many makers become a filterable table, and only the ones waiting for the owner stay as cards", () => {
  const model = sceneModel({makers: crowd(24), proposals: [run]});
  assert.equal(model.makers.length, 6);
  assert.equal(model.all.length, 24);
  const html = makerList(model);
  assert.ok(24 > CARD_LIMIT);
  assert.equal((html.match(/class="mcard ask/g) || []).length, 3);
  assert.equal((html.match(/<tr><th scope="row">/g) || []).length, PAGE);
  assert.match(html, /data-filter="all" aria-pressed="true">All <span>24<\/span>/);
  assert.match(html, /data-filter="ask" aria-pressed="false">Needs you <span>5<\/span>/);
  assert.match(html, /Show all 24/);
  // Makers past the six the map can draw stay in the table without a dead link.
  assert.ok(!html.includes('data-node="maker:m23"'));
  assert.match(html, /<th scope="row">Maker 23<\/th>|<th scope="row">Maker 20<\/th>/);
  const held = makerList(model, {filter: "held"});
  assert.equal((held.match(/<tr><th scope="row">/g) || []).length, 5);
  assert.match(held, /Showing 5 of 5 held/);
  assert.equal((makerList(model, {expanded: true}).match(/<tr><th scope="row">/g) || []).length, 24);
  assert.match(makerList(model, {filter: "nonsense"}), /aria-pressed="true">All/);
});

test("totals count every maker, not only the six on the map", () => {
  const model = sceneModel({makers: crowd(24), proposals: [run]});
  assert.equal(model.totals.paying, 5 * 10000);
  assert.equal(model.held.total, 5 * 5000);
});

test("each figure carries the line that makes it actionable, and an unread balance stays a dash", () => {
  const html = kpiCards(sceneModel({makers: friday, proposals: [run], cash}));
  assert.match(html, /PayPal available<\/dt><dd data-count="134000">\$1,340<\/dd><dd class="kpi-sub">\$254 left after this run/);
  assert.match(html, /Scheduled or in flight<\/dt><dd data-count="108600">\$1,086<\/dd><dd class="kpi-sub">2 makers · needs your signature/);
  assert.match(html, /Held back<\/dt><dd data-count="41400">\$414<\/dd><dd class="kpi-sub">2 open disputes/);
  const unread = kpiCards(sceneModel({makers: friday, proposals: [run]}));
  assert.match(unread, /PayPal available<\/dt><dd data-count="">—<\/dd><dd class="kpi-sub">Not read from PayPal yet/);
  const short = kpiCards(sceneModel({makers: friday, proposals: [run], cash: {paypal: {available: usd(100000)}}}));
  assert.match(short, /\$86 short of this run/);
});

test("the money bar splits what is owed now and says whether the balance covers the run", () => {
  const html = stands(sceneModel({makers: friday, proposals: [run], cash}));
  assert.match(html, /Owed to makers now<\/span><b>\$1,500<\/b>/);
  assert.match(html, /aria-label="\$1,086 waiting for your signature, \$414 held for disputes"/);
  assert.match(html, /covers the \$1,086 waiting to be paid, leaving \$254\./);
  const planned = stands(sceneModel({makers: friday, proposals: [], cash}));
  assert.match(planned, /<i class="scheduled"/);
  assert.ok(!planned.includes('class="sign"'));
  assert.match(stands(sceneModel({makers: [maker("p", "Maker P", {paid: 9900})]})), /Nothing is owed right now\.[\s\S]*\$99 already reached makers/);
});

test("the action strip names the run, its payees and links to the exact decision", () => {
  const html = actionStrip(sceneModel({makers: friday, proposals: [run], cash}));
  assert.match(html, /Weekly payout run · \$1,086 to Maker B and Maker C/);
  assert.match(html, /href="#decision\/p1">Review and sign/);
  assert.match(html, /3 shield checks/);
  assert.match(html, /\$414 stays held until its disputes settle\./);
  const idle = actionStrip(sceneModel({makers: friday, proposals: []}));
  assert.match(idle, /nothing to propose yet/);
  assert.match(idle, /href="#decision">See decisions/);
  assert.equal(weekLabel({step: 4, total: 11, steps: [{}]}), "Staged week · step 4 of 11");
  assert.equal(weekLabel(null), "This week");
});

test("untrusted maker names stay inert in cards and the table", () => {
  const evil = maker("x", '<img src=x onerror=alert(1)>', {scheduled: 100});
  for (const makers of [[evil], [evil, ...crowd(6)]]) {
    const html = makerList(sceneModel({makers, proposals: [run]}));
    assert.ok(!html.includes("<img"));
    assert.match(html, /&lt;img src=x/);
  }
});

test("the headline counts every maker and matches the owed figure in where the money stands", () => {
  const model = sceneModel({makers: crowd(24), proposals: []});
  const owed = model.all.reduce((s, m) => s + m.inFlight + m.scheduled + m.held + m.owed, 0);
  const figure = "$" + (owed / 100).toLocaleString("en-US");
  assert.ok(headline(model).includes(`${figure}</em> is owed to 24 makers`));
  assert.ok(stands(model).includes(`Owed to makers now</span><b>${figure}<`));
  // Money sent to PayPal but not confirmed is still owed, and the finding says it is on its way.
  const sending = sceneModel({makers: [maker("a", "Maker A", {owed: 36000}), maker("c", "Maker C", {inFlight: 30000})], proposals: []});
  assert.match(headline(sending), /\$660<\/em> is owed to 2 makers/);
  assert.match(finding(sending), /\$300 is on its way through PayPal/);
});

test("the headline offers to pay only when a payout run waits for the owner", () => {
  assert.match(headline(sceneModel({makers: friday, proposals: [run]})), /You can pay <em>\$1,086<\/em> to 2 makers today/);
  const other = {...run, id: "p2", action: "ACCEPT_DISPUTE_CLAIM"};
  assert.match(headline(sceneModel({makers: friday, proposals: [other, {...other, id: "p3"}]})), /2 decisions wait for <em>your signature<\/em>/);
});
