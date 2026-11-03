import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createCommand} from "../command.mjs";

const original = JSON.parse(await readFile(new URL("../fixtures/makers.json", import.meta.url))).items[0];
const makers = Array.from({length:7}, (_, i) => ({...structuredClone(original), id:"maker-" + i, display_name:"Maker " + i}));
const graph = id => ({nodes:[{id:"maker:" + id, type:"maker", label:id, amount:null}], edges:[]});
function documentDouble() {
  const elements = new Map();
  for (const id of ["kpis", "rail-slot", "stage", "scene-back", "scene-caption", "panel", "command", "folios", "maker-dialog", "md-title", "md-sub", "md-simulation", "md-stage", "md-panel"]) {
    elements.set(id, {innerHTML:"", textContent:"", hidden:false, dataset:{}, style:{setProperty() {}},
      classList:{add() {}, remove() {}, toggle() {}}, open:false,
      showModal() {this.open = true;}, close() {this.open = false; this.onclose?.();}});
  }
  return {getElementById:id => elements.get(id) || null, querySelectorAll:() => [], querySelector:() => null};
}
const workspace = {fixture:true, meta:{mode:"demo", simulated:true, simulation_label:"Simulated: mock PayPal"}};
const open = (doc, id = "maker-6") => doc.getElementById("folios").onclick({target:{closest:selector => selector === "[data-node]" ? {dataset:{node:"maker:" + id}} : null}});

test("a maker outside the first six loads its own graph, rejects a wrong root and can retry", async () => {
  const doc = documentDouble(), reads = []; let resolveGraph;
  const client = {
    list:async resource => resource === "makers" ? makers : [],
    singleton:async (resource, params) => {
      if (resource !== "money-graph") return null;
      reads.push(params.maker_id);
      return params.maker_id === "maker-6" ? new Promise(resolve => {resolveGraph = resolve;}) : graph(params.maker_id);
    },
  };
  const command = createCommand(doc);
  await command.show(workspace, client);
  assert.equal(reads.length, 6);
  open(doc);
  assert.equal(doc.getElementById("maker-dialog").open, true);
  assert.equal(doc.getElementById("md-title").textContent, "Maker 6");
  assert.match(doc.getElementById("md-stage").innerHTML, /Loading maker records/);
  resolveGraph(graph("another-maker")); await new Promise(resolve => setImmediate(resolve));
  assert.match(doc.getElementById("md-stage").innerHTML, /could not be loaded/);
  assert.ok(!doc.getElementById("md-stage").innerHTML.includes("<svg"));
  doc.getElementById("maker-dialog").close(); open(doc);
  resolveGraph(graph("maker-6")); await new Promise(resolve => setImmediate(resolve));
  assert.match(doc.getElementById("md-stage").innerHTML, /<svg/);
  assert.equal(reads.filter(id => id === "maker-6").length, 2);
});

test("a failed initial graph read for one of the first six is retried when its dialog opens", async () => {
  const doc = documentDouble(), calls = new Map();
  const client = {
    list:async resource => resource === "makers" ? makers : [],
    singleton:async (resource, params) => {
      if (resource !== "money-graph") return null;
      const count = (calls.get(params.maker_id) || 0) + 1; calls.set(params.maker_id, count);
      return count === 1 ? null : graph(params.maker_id);
    },
  };
  await createCommand(doc).show(workspace, client);
  open(doc, "maker-0");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.get("maker-0"), 2);
  assert.match(doc.getElementById("md-stage").innerHTML, /<svg/);
});
