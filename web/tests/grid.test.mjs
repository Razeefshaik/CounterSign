import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {incidentRows, auditRows, runRows, runTotal, makerRows, makerFilter, columnDefs, gridOptions, stateTone, moneyText, loadVendor, mountGrid, auditToolbar} from "../grid.mjs";
import {sceneModel} from "../scene.mjs";
import {isStaged} from "../data.mjs";
const fixture = async name => JSON.parse(await readFile(new URL(`../fixtures/${name}.json`, import.meta.url)));
const incidents = (await fixture("incidents")).items, makers = (await fixture("makers")).items;
const disputes = (await fixture("disputes")).items, audit = (await fixture("audit-log")).items;
const run = (await fixture("run-details"))[0];

test("each grid exposes its task columns with Community sorting, filters and money alignment", () => {
  const fields = {
    incidents:["incident", "state", "amount_minor", "maker", "dispute", "updated"],
    audit:["at", "actor", "action", "rule", "verdict", "amount_minor", "proposal_id", "operation_id"],
    runs:["maker", "amount_minor", "state", "attempt", "item_status", "item_id"],
    makers:["maker", "state", "amount_minor", "held_minor", "note"],
  };
  for (const [name, expected] of Object.entries(fields)) {
    const defs = columnDefs(name);
    assert.deepEqual(defs.map(c => c.field), expected);
    assert.ok(defs.every(c => ["agTextColumnFilter", "agNumberColumnFilter", "agDateColumnFilter"].includes(c.filter)));
    for (const c of defs.filter(c => c.field.endsWith("_minor"))) assert.equal(c.cellClass, "cs-grid-money");
    const config = gridOptions(name, [], {reducedMotion:true});
    assert.equal(config.defaultColDef.sortable, true); assert.equal(config.animateRows, false);
    assert.equal(config.paginationPageSize, 10); assert.equal(config.pagination, true);
  }
  assert.throws(() => columnDefs("unknown"));
});

test("incident rows bind maker and dispute API records, staged markers and integer minor units", () => {
  const rows = incidentRows(incidents, {makers, disputes, staged:r => isStaged(r, disputes)});
  assert.equal(rows[0].amount_minor, incidents[0].amount_at_risk.minor);
  assert.equal(rows[0].amount_currency, "USD"); assert.equal(rows[0].maker, "Maker A");
  assert.equal(rows[0].dispute, incidents[0].subject.paypal_dispute_id);
  assert.equal(rows[0].updated, incidents[0].updated_at); assert.equal(rows[0].staged, true);
  const col = columnDefs("incidents").find(c => c.field === "amount_minor");
  assert.equal(col.valueFormatter({value:rows[0].amount_minor, data:rows[0]}), "$400.00");
  for (const value of [null, {minor:0.5, currency:"USD"}, {minor:"40000", currency:"USD"}, {minor:0, currency:""}]) {
    const mapped = incidentRows([{id:"unread", amount_at_risk:value}])[0];
    assert.equal(mapped.amount_minor, null); assert.equal(col.valueFormatter({value:mapped.amount_minor, data:mapped}), "—");
  }
  assert.equal(moneyText(0, "USD"), "$0.00"); assert.equal(moneyText(-101, "EUR"), "EUR -1.01");
});

test("pink stamp classes mean needs-owner only, never held or approved money", () => {
  for (const v of ["AWAITING_OWNER", "NEEDS_OWNER", "ask"]) assert.equal(stateTone(v), "owner");
  for (const v of ["ALLOW", "APPROVED", "PAID", "held", "BLOCK", "FAILED", "PENDING", "UNKNOWN"]) assert.notEqual(stateTone(v), "owner");
  const renderer = columnDefs("incidents")[1].cellRenderer;
  assert.match(renderer({value:"AWAITING_OWNER", data:{}, colDef:{field:"state"}}), /stamp owner/);
  assert.ok(!renderer({value:"RESOLVED", data:{}, colDef:{field:"state"}}).includes("owner"));
});

test("run grid includes resends once, latest attempt and an authoritative pinned total", () => {
  const rows = runRows(run);
  assert.equal(rows.length, 5); assert.equal(rows.filter(r => !r.held).reduce((n, row) => n + row.amount_minor, 0), run.total.minor);
  assert.equal(rows[0].attempt, 1); assert.equal(rows[0].item_status, null);
  const resend = rows.find(r => r.replacement);
  assert.equal(resend.attempt, 2); assert.equal(resend.item_status, null); assert.equal(resend.state, null);
  const total = runTotal(run);
  assert.equal(total.amount_minor, 108600); assert.equal(gridOptions("runs", rows, {total}).pinnedBottomRowData[0], total);
  const duplicated = {...run, replacements:[{...run.replacements[0], line_id:run.lines[0].id}]};
  assert.equal(runRows(duplicated).length, run.lines.length + run.reserved.length);
  const later = {...run, lines:[{...run.lines[0], attempts:[{attempt_no:2, state:"SUCCESS", paypal_item_id:"ITEM2"}, ...run.lines[0].attempts]}]};
  assert.equal(runRows(later)[0].item_id, "ITEM2");
  assert.equal(runRows(later)[0].item_status, "SUCCESS");
});

test("audit rows preserve absent money and bind operation links to actual proposal ids", () => {
  const rows = auditRows([...audit, {id:2, kind:"OPERATION_CONFIRMED", operation_id:"op1", proposal_id:"p1", details:{amount:{minor:33333, currency:"USD"}}}, {id:3, kind:"NOTE", operation_id:"op1", details:{}}, {id:4, kind:"NOTE", operation_id:"orphan"}]);
  assert.equal(rows[0].amount_minor, null); assert.equal(rows[1].amount_minor, 33333);
  assert.equal(rows[2].decision_id, "p1"); assert.equal(rows[3].decision_id, null);
  const link = columnDefs("audit").find(c => c.field === "operation_id").cellRenderer;
  assert.match(link({value:"op1", data:rows[2]}), /href="#decision\/p1"/);
  assert.ok(!link({value:"orphan", data:rows[3]}).includes("href"));
  assert.match(auditToolbar(audit), /data-audit-verdict/); assert.match(auditToolbar(audit), /value="BLOCK">Stopped/);
  assert.match(auditToolbar(audit), /data-audit-actor/);
});

test("makers use all API records, preserve state keys for filters, and never format row money early", () => {
  const model = sceneModel({makers, proposals:[{action:"SCHEDULE_PAYOUT_RUN", state:"NEEDS_OWNER"}]}), rows = makerRows(makers, model);
  assert.equal(rows.length, makers.length); assert.equal(rows[1].amount_minor, makers[1].scheduled.minor);
  assert.equal(rows[1].state, "ask"); assert.equal(rows[1].held_minor, makers[1].reserved.minor);
  const many = Array.from({length:22}, (_, i) => ({...makers[1], id:"m" + i}));
  assert.equal(makerRows(many, sceneModel({makers:many})).length, 22);
  assert.equal(makerRows([{...makers[0], reserved:null}], sceneModel({makers:[makers[0]]}))[0].held_minor, null);
  assert.equal(makerFilter(rows, "ask"), "ask");
  assert.equal(makerFilter(rows.map(row => ({...row, state:"paid"})), "ask"), "all");
  assert.equal(makerFilter(rows, "unknown-filter"), "all");
});

test("untrusted API text and link ids are escaped and emails masked by every custom renderer", () => {
  const evil = '<img src=x onerror="alert(1)"> private@example.test';
  for (const view of ["incidents", "audit", "runs", "makers"]) for (const def of columnDefs(view)) {
    const html = def.cellRenderer({value:evil, data:{incident:evil, kind:evil, state:evil, decision_id:evil}, colDef:def});
    assert.ok(!html.includes("<img"), `${view} ${def.field}`);
    assert.ok(!html.includes("private@example.test"), `${view} ${def.field}`);
  }
});

test("row click and Enter open the bound record, pinned totals and native links do not", () => {
  const opened = [], config = gridOptions("incidents", [], {onOpen:r => opened.push(r.id)}), data = {id:"i"};
  config.onRowClicked({data, node:{}, event:{target:{closest:() => null}}});
  let prevented = false;
  config.onCellKeyDown({data, node:{}, event:{key:"Enter", preventDefault:() => {prevented = true;}}});
  config.onRowClicked({data, node:{rowPinned:"bottom"}});
  config.onRowClicked({data, node:{}, event:{target:{closest:() => ({})}}});
  assert.deepEqual(opened, ["i", "i"]); assert.equal(prevented, true);
});

function fakeHost() {
  const make = () => ({hidden:false, dataset:{}, style:{}, children:[], setAttribute() {}, append(node) {this.children.push(node);}, prepend(node) {this.children.unshift(node);}, remove() {this.removed = true;}});
  const doc = {createElement:make, defaultView:{}, body:make()}, host = make(), original = make();
  host.ownerDocument = doc; host.isConnected = true; host.children = [original];
  return {host, original, doc};
}
test("missing vendor retains the original table and announces fallback without throwing", async () => {
  const {host, original} = fakeHost();
  const handle = await mountGrid(host, "incidents", [], {vendorLoader:async () => null});
  assert.equal(handle.api, null); assert.equal(original.hidden, false); assert.equal(host.dataset.gridState, "fallback");
  assert.match(host.children.at(-1).textContent, /Showing the standard table/);
  assert.equal(await loadVendor({}), null); assert.equal(await mountGrid(null, "audit", []), null);
});
test("successful mount hides fallback only after createGrid and destroys on removal", async () => {
  const {host, original, doc} = fakeHost(); let options, destroyed = false, observed;
  doc.defaultView.MutationObserver = class {constructor(fn) {observed = fn;} observe() {} disconnect() {}};
  const api = {destroy:() => {destroyed = true;}, setGridOption() {}};
  const handle = await mountGrid(host, "runs", [], {vendorLoader:async () => ({createGrid:(_, config) => {assert.equal(original.hidden, false); options = config; return api;}})});
  assert.equal(handle.api, api); assert.equal(original.hidden, true); assert.equal(options.paginationPageSize, 10);
  host.isConnected = false; observed(); assert.equal(destroyed, true); assert.equal(original.hidden, false);
});
test("a route removed during lazy loading never creates a grid", async () => {
  const {host} = fakeHost(); let release, called = false;
  const wait = new Promise(resolve => {release = resolve;});
  const mounting = mountGrid(host, "audit", [], {vendorLoader:() => wait});
  assert.equal(host.dataset.gridState, "loading");
  host.isConnected = false; release({createGrid:() => {called = true;}});
  assert.equal(await mounting, null); assert.equal(called, false);
});

test("a vendor initialization exception restores the original table", async () => {
  const {host, original} = fakeHost();
  const handle = await mountGrid(host, "incidents", [], {vendorLoader:async () => ({createGrid:() => {throw Error("Unavailable renderer");}})});
  assert.equal(handle, null); assert.equal(original.hidden, false); assert.equal(host.dataset.gridState, "fallback");
});

test("a still-connected incident host left during loading is not enhanced off route", async () => {
  const {host, original} = fakeHost(); let release, active = true, created = false;
  const pending = new Promise(resolve => {release = resolve;});
  const mounting = mountGrid(host, "incidents", [], {isActive:() => active, vendorLoader:() => pending});
  active = false; release({createGrid:() => {created = true;}});
  assert.equal(await mounting, null); assert.equal(created, false);
  assert.equal(original.hidden, false); assert.equal(host.dataset.gridState, "idle");
});

test("C69: a waiting decision is a Needs owner row and resolved money is not at risk", () => {
  const usd = minor => ({minor, currency:"USD"});
  const incidents = [
    {id:"a", kind:"DISPUTE", state:"WAITING_EXTERNAL", subject:{}, amount_at_risk:usd(6000), updated_at:"2026-11-05T09:00:00Z"},
    {id:"b", kind:"PAYOUT_ITEM", state:"RESOLVED", subject:{}, amount_at_risk:usd(30000), updated_at:"2026-11-05T09:00:00Z", latest_proposal:{id:"linked"}},
  ];
  const proposals = [{id:"run", action:"SCHEDULE_PAYOUT_RUN", state:"NEEDS_OWNER"}, {id:"linked", action:"CANCEL_UNCLAIMED_PAYOUT", state:"NEEDS_OWNER"}, {id:"done", action:"PROVIDE_EVIDENCE", state:"EXECUTED"}];
  const rows = incidentRows(incidents, {proposals});
  assert.deepEqual(rows.map(row => row.id), ["decision:run", "a", "b"]);
  assert.equal(rows[0].state, "AWAITING_OWNER"); assert.equal(rows[0].decision_id, "run"); assert.equal(rows[0].amount_minor, null);
  assert.equal(rows[1].amount_minor, 6000); assert.equal(rows[2].amount_minor, null);
});
