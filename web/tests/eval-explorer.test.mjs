import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {columns, summary, summaryHTML, detailHTML, explorerHTML, filteredRows, preset,
  emptyMessage, totals, parseBundle, INVARIANTS} from "../eval-explorer.mjs";

const bundle = JSON.parse(await readFile(new URL("../results/eval-runs.json", import.meta.url), "utf8"));
const rows = parseBundle(bundle);
const make = (system, scenario_id, passed, k = 0) => ({system,scenario_id,passed,k,
  correct_final_action:passed, task_completed:passed, fault_class:"FAULT", provenance:"synthesised",
  model:"none", ground:"mock", owner_escalations:1, duration_ms:100,
  invariants:Object.fromEntries(Object.keys(INVARIANTS).map(key => [key, 0])), makers_paid_on_time:{paid:1,due:1}});
const fixture = [make("countersign","a",true),make("rules_only","a",false),
  make("countersign","b",false),make("rules_only","b",true),make("countersign","c",false)];

test("C58 has the fourteen Community filterable, movable analysis columns", () => {
  const defs = columns();
  assert.equal(defs.length,14);
  assert.deepEqual(defs.map(c => c.headerName), ["System","Scenario","Fault class","Run","Passed",
    "Final action correct","Task completed",...Object.values(INVARIANTS),"Makers paid on time","Owner escalations","Provenance"]);
  assert.equal(defs.find(c => c.field === "k").filter,"agNumberColumnFilter");
  for (const key of Object.keys(INVARIANTS)) assert.equal(defs.find(c => c.field === key).filter,"agNumberColumnFilter");
  assert.ok(defs.every(c => c.filter !== "agSetColumnFilter"));
});

test("C58 presets are derived from paired fixture outcomes, not hard-coded scenario ids", () => {
  assert.deepEqual(filteredRows(fixture,preset("failed")).map(r=>r.scenario_id),["b","c"]);
  assert.deepEqual(filteredRows(fixture,preset("comparison")).map(r=>r.system),["countersign","rules_only"]);
  assert.deepEqual(filteredRows(fixture,preset("invariants")),[]);
  const changed = structuredClone(fixture); changed[0].invariants.duplicate_settlement = 1;
  assert.equal(filteredRows(changed,preset("invariants")).length,1);
  assert.equal(filteredRows(fixture,{...preset("all"),system:"rules_only",failing:true}).length,1);
});

test("C58 published preset counts and computed full summary reproduce source", () => {
  assert.equal(filteredRows(rows,preset("failed")).length,70);
  assert.equal(filteredRows(rows,preset("comparison")).length,30);
  assert.equal(filteredRows(rows,preset("invariants")).length,0);
  assert.deepEqual(summary(rows).systems,[
    {system:"countersign",runs:240,passing:170,scenarios:48,consistent:34},
    {system:"rules_only",runs:240,passing:215,scenarios:48,consistent:43},
  ]);
  assert.ok(Object.values(summary(rows).invariants).every(n=>n===0));
  const reduced = fixture.slice(0,2);
  assert.equal(summary(reduced).runs,2);
  assert.equal(summary(reduced).systems[0].passing,1);
  assert.equal(summary(reduced).systems[0].consistent,0);
  assert.match(summaryHTML(reduced),/1\/1 runs passing/);
  assert.doesNotMatch(summaryHTML(reduced),/170\/240/);
});

test("C58 pinned totals use the current row set and display true zero totals", () => {
  const count = totals(fixture.slice(0,2));
  assert.equal(count.scenario_id,"2 runs"); assert.equal(count.passed,"1 pass");
  assert.deepEqual(count.makers_paid_on_time,{paid:2,due:2});
  assert.equal(count.owner_escalations,2);
  assert.deepEqual(totals([]).invariants,Object.fromEntries(Object.keys(INVARIANTS).map(key=>[key,0])));
});

test("C58 zero-invariant preset has an honest empty state", () => {
  assert.equal(emptyMessage(preset("invariants")),"No run violated an invariant");
  assert.match(emptyMessage(preset("all")),/No matching runs/);
});

test("C58 renderers escape source text and disclose unavailable action traces", () => {
  const row = {...fixture[2],scenario_id:'<img src=x onerror="alert(1)">',fault_class:'<script>bad</script>'};
  const html=detailHTML(row);
  assert.ok(html.includes("&lt;img")); assert.ok(!html.includes("<img")); assert.ok(!html.includes("<script>"));
  assert.match(html,/Final action did not match/); assert.match(html,/No action trace is bundled/);
  assert.match(explorerHTML(rows),/model disabled/); assert.match(explorerHTML(rows),/not a real-model or sandbox run/i);
  const scenarioCol=columns().find(c=>c.field==="scenario_id");
  assert.equal(scenarioCol.cellRenderer({value:"<b>injected</b>"}),"&lt;b&gt;injected&lt;/b&gt;");
});

test("C58 rejects relabelled or duplicated bundles", () => {
  for (const change of [b=>b.labels.pop(), b=>b.runs[0].ground="sandbox", b=>b.runs[1]=b.runs[0]]) {
    const value=structuredClone(bundle); change(value); assert.throws(()=>parseBundle(value));
  }
});
