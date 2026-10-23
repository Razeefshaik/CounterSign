import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {filterIncidents, summary, money, modeLabel, isStaged, loadWorkspace} from "../data.mjs";
const incidents = JSON.parse(await readFile(new URL("../fixtures/incidents.json", import.meta.url))).items;
const disputes = JSON.parse(await readFile(new URL("../fixtures/disputes.json", import.meta.url))).items;
test("state and kind filters combine", () => {
  assert.equal(filterIncidents(incidents, {state:"AWAITING_OWNER",kind:"DISPUTE"}).length,2);
  assert.equal(filterIncidents(incidents, {kind:"PAYOUT_ITEM"}).length,1);
  assert.equal(filterIncidents(incidents, {query:"maker c"}).length,1);
  assert.equal(filterIncidents(incidents, {query:"nothing matches"}).length,0);
});
test("money and summary retain minor units", () => {
  assert.equal(money({minor:108600,currency:"USD"}),"$1,086.00");
  assert.throws(() => money({minor:0.2,currency:"USD"}));
  assert.deepEqual(summary(incidents),{open:4,owner:3});
});
test("staged labels follow linked dispute fixtures", () => {
  assert.equal(isStaged(incidents[0],disputes),true);
  assert.equal(isStaged(incidents[1],disputes),false);
});
test("mode never silently claims a live mock", () => {
  assert.equal(modeLabel({mode:"live",paypal_env:"sandbox",simulated:false}),"Live · PayPal sandbox");
  assert.throws(() => modeLabel({mode:"live",paypal_env:"mock",simulated:true}));
});
test("static preview uses visibly simulated contract fixtures", async () => {
  const fetcher = async url => {
    if(url.startsWith("/api")) return {ok:false};
    return {ok:true,json:async () => JSON.parse(await readFile(new URL("../"+url, import.meta.url)))};
  };
  const data = await loadWorkspace(fetcher);
  assert.equal(data.fixture,true);
  assert.equal(data.meta.simulation_label,"Simulated: mock PayPal");
  assert.equal(data.incidents.length,4);
});
