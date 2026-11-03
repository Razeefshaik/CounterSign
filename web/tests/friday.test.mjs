import test from "node:test";
import assert from "node:assert/strict";
import {guideView, STEP_HINTS} from "../guide.mjs";
import {createDemoBar} from "../demo.mjs";

const state = (step, extra={}) => ({simulated:true, step, total:11, complete:step===11, running:false,
  steps:Object.entries(STEP_HINTS).map(([i,h])=>({index:+i,name:h.name,label:`Label ${i}`,done:+i<=step})), ...extra});

test("Friday story requires the API checkpoint and the exact step", () => {
  assert.match(guideView(state(6,{checkpoint:"friday"})), /It's Friday\. The weekly payout run is waiting for you\./);
  assert.match(guideView(state(6,{checkpoint:"friday"})), /AI cancelled Maker C/);
  assert.match(guideView(state(6,{checkpoint:"friday"})), /Watch from Monday/);
  for (const s of [state(6),state(6,{checkpoint:"other"}),state(5,{checkpoint:"friday"}),state(6,{checkpoint:"friday",simulated:false})]) assert.doesNotMatch(guideView(s), /It's Friday/);
  const html=guideView(state(6,{checkpoint:"friday"}),{notice:"<img onerror=attack()>"});
  assert.doesNotMatch(html, /<img/); assert.match(html,/&lt;img/);
});

test("Back to Friday appears only at the end with a server-provided artifact", () => {
  assert.match(guideView(state(11,{friday_available:true})), /data-guide-action="friday"/);
  assert.doesNotMatch(guideView(state(11)), /Back to Friday/);
  assert.doesNotMatch(guideView(state(6,{friday_available:true})), /Back to Friday/);
  assert.match(guideView(state(11,{friday_available:true,running:true})), /data-guide-action="friday" disabled/);
});

test("Friday uses the same one-request guard as step and restart", async () => {
  const elements=new Map(["demo-bar","demo-step","demo-reset","demo-status"].map(id=>[id,{hidden:false,innerHTML:"",textContent:""}]));
  const doc={getElementById:id=>elements.get(id)};
  let resolvePost, posts=0;
  const before=state(11,{friday_available:true});
  const fetcher=async (_url,options)=>{
    if(options?.method==="POST") {posts++; await new Promise(resolve=>{resolvePost=resolve;});}
    return {ok:true,status:200,json:async()=>before};
  };
  const bar=createDemoBar(doc,{fetcher});
  await bar.load();
  const first=bar.friday(); bar.friday(); bar.step();
  assert.equal(posts,1); resolvePost(); await first;
});
