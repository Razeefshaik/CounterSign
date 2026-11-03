import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createCommand} from "../command.mjs";

const makers = JSON.parse(await readFile(new URL("../fixtures/makers.json", import.meta.url))).items;
const client = {list: async name => name === "makers" ? makers : [], singleton: async () => null};
function dialogDocument() {
  const ids = ["kpis", "rail-slot", "stage", "scene-back", "scene-caption", "panel", "command",
    "folios", "maker-dialog", "md-title", "md-sub", "md-simulation", "md-stage", "md-panel"];
  const elements = new Map(ids.map(id => [id, {innerHTML:"", textContent:"", hidden:true,
    dataset:{}, classList:{add() {}, remove() {}, toggle() {}}, open:false,
    showModal() {this.open = true;}, close() {this.open = false; this.onclose?.();}}]));
  return {getElementById: id => elements.get(id) || null, querySelectorAll: () => []};
}

for (const [name, meta, expected] of [
  ["demo", {mode:"demo", simulated:true, simulation_label:"Simulated: mock PayPal"}, "Simulated: mock PayPal"],
  ["live", {mode:"live", simulated:false, simulation_label:"stale mock label"}, ""],
  ["unavailable metadata", {}, ""],
  ["literal server text", {mode:"demo", simulated:true, simulation_label:"<img src=x onerror=attack()>"}, "<img src=x onerror=attack()>"],
]) test(`maker dialog uses current server simulation metadata: ${name}`, async () => {
  const doc = dialogDocument(), command = createCommand(doc);
  await command.show({fixture:true, meta:{mode:"demo", simulated:true, simulation_label:"Old demo label"}}, client);
  doc.getElementById("folios").onclick({target:{closest: selector => selector === "[data-node]"
    ? {dataset:{node:"maker:" + makers[0].id}} : null}});
  assert.equal(doc.getElementById("maker-dialog").open, true);
  await command.show({fixture:true, meta}, client);
  const label = doc.getElementById("md-simulation");
  assert.equal(label.textContent, expected);
  assert.equal(label.hidden, !expected);
  assert.equal(label.innerHTML, "");
});
