import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {filterIncidents, summary, money, atRisk, modeLabel, isStaged, loadWorkspace, bannerText} from "../data.mjs";
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
    if(url.startsWith("/api")) return {ok:false,status:404};
    return {ok:true,json:async () => JSON.parse(await readFile(new URL("../"+url, import.meta.url)))};
  };
  const data = await loadWorkspace(fetcher);
  assert.equal(data.fixture,true);
  assert.equal(data.meta.simulation_label,"Simulated: mock PayPal");
  assert.equal(data.incidents.length,4);
});
test("an incident without an amount at risk renders a dash, not an error", () => {
  assert.equal(atRisk(null), "—");
  assert.equal(atRisk(undefined), "—");
  assert.equal(atRisk({minor: 6000, currency: "USD"}), "$60.00");
  assert.throws(() => atRisk({minor: 1.5, currency: "USD"}));
});
test("staged labels also follow incidents that name their dispute by PayPal ID", () => {
  const goal = {kind:"RECONCILIATION", subject:{dispute_id:null, paypal_dispute_id:disputes[0].paypal_dispute_id}};
  assert.equal(isStaged(goal, disputes), disputes[0].staged === true);
  assert.equal(isStaged({subject:{paypal_dispute_id:"PP-D-OTHER"}}, disputes), false);
  assert.equal(isStaged({subject:null}, disputes), false);
});
test("the simulation banner text comes only from the server and never appears in live mode", () => {
  const demo = {mode:"demo", paypal_env:"mock", simulated:true, simulation_label:"Simulated: mock PayPal"};
  const live = {mode:"live", paypal_env:"sandbox", simulated:false, simulation_label:null};
  const replaying = {replaying:true, simulation_label:"Simulated: mock PayPal"};
  assert.equal(bannerText(demo), "Simulated: mock PayPal");
  assert.equal(bannerText(undefined, replaying), "Simulated: mock PayPal");
  assert.equal(bannerText(live), "");
  assert.equal(bannerText(live, replaying), "");
  assert.equal(bannerText({...demo, simulation_label:null}), "");
});

test("an unavailable API never switches to fixture data", async () => {
  const urls=[];
  await assert.rejects(loadWorkspace(async url=>{urls.push(url);return {ok:false,status:503};}));
  assert.deepEqual(urls,["/api/v1/meta"]);
  await assert.rejects(loadWorkspace(async ()=>{throw Error("offline");}));
});
