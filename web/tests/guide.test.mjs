import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {STEP_HINTS, guideModel, guideView, createGuide} from "../guide.mjs";
import {createDemoBar} from "../demo.mjs";

const state = (step = 0, extra = {}) => ({step, total:11, simulated:true, running:false, complete:step === 11,
  steps:Object.entries(STEP_HINTS).map(([index, hint]) => ({index:Number(index), name:hint.name, label:`API label ${index}`, done:Number(index) <= step})), ...extra});
const response = (body, status = 200) => ({ok:status < 400, status, json:async () => body});

function documentDouble(reducedMotion = false) {
  const elements = new Map(), pulses = [], scrolls = [];
  const doc = {activeElement:null, defaultView:{matchMedia:() => ({matches:reducedMotion})},
    getElementById:id => elements.get(id) || null};
  for (const id of ["week-guide", "demo-bar", "makers-title", "stands-title"]) {
    const el = {hidden:true, dataset:{}, classList:{add:value => pulses.push(value)},
      focus() {doc.activeElement = this;}, setAttribute() {}, scrollIntoView:options => scrolls.push(options),
      contains(node) {return node?.owner === this;}, querySelector(selector) {
        if (selector === "details") return this.details || null;
        if (selector === "#guide-title") return elements.get("guide-title") || null;
        const action = selector.match(/data-guide-action="([^"]+)"/);
        return action ? elements.get("guide-" + action[1]) || null : null;
      }};
    let html = "";
    Object.defineProperty(el, "innerHTML", {get:() => html, set:value => {
      html = value;
      const own = id === "week-guide" ? ["guide-step", "guide-reset", "guide-title", "guide-dismiss"] : ["demo-step", "demo-reset", "demo-status"];
      own.forEach(key => elements.delete(key));
      for (const match of value.matchAll(/<(?:button|p|h2)[^>]*(?:id="([^"]+)"|data-guide-action="([^"]+)")[^>]*>/g)) {
        const key = match[1] || "guide-" + match[2];
        elements.set(key, {owner:el, dataset:{guideAction:match[2]}, disabled:match[0].includes("disabled"),
          textContent:"", focus() {doc.activeElement = this;}});
      }
      el.details = value.includes("<details") ? {open:false} : null;
    }});
    elements.set(id, el);
  }
  const click = action => doc.getElementById("week-guide").onclick({target:{closest:selector => selector === "[data-guide-action]" ? {dataset:{guideAction:action}} : null}});
  return {doc, pulses, scrolls, click, host:elements.get("week-guide")};
}

test("start card appears only at step zero with a valid simulated demo API response", () => {
  assert.match(guideView(state()), /See CounterSign handle one week of disputes and payouts/);
  assert.match(guideView(state()), /11 steps; each one takes a few seconds/);
  assert.match(guideView(state()), /Start the week/);
  for (const value of [null, undefined, {}, state(0,{simulated:false}), state(-1), state(0,{step:"0"}), state(0,{total:0})]) assert.equal(guideView(value), "");
  assert.doesNotMatch(guideView(state(1)), /Start the week/);
});

test("hints use the confirmed API step label, never next_label or a local replacement", () => {
  const value = state(6, {next_label:"NEXT STEP, not what just happened"});
  value.steps[5].label = "Server-owned Friday label";
  const html = guideView(value);
  assert.match(html, /Server-owned Friday label/);
  assert.doesNotMatch(html, /NEXT STEP|API label 5/);
  assert.match(html, /Review and sign/);
  assert.match(html, /passkey/);
});

test("map covers every real API step 1–11 and separates reserve release from payment", async () => {
  assert.deepEqual(Object.keys(STEP_HINTS).map(Number), Array.from({length:11}, (_, i) => i + 1));
  const source = await readFile(new URL("../../src/countersign/api/dashboard.py", import.meta.url), "utf8");
  for (const [index, hint] of Object.entries(STEP_HINTS)) {
    assert.ok(source.includes(`"${hint.name}",`), hint.name);
    assert.ok(hint.links.length);
    assert.match(guideView(state(Number(index))), new RegExp(`API label ${index}`));
  }
  assert.match(guideView(state(10)), /Payment is the next step/);
  assert.match(guideView(state(11)), /payment after the release/);
});

test("an altered server step keeps its label with neutral guidance instead of incorrect claims", () => {
  const value = state(6); value.steps[5].name = "changed_server_step";
  assert.equal(guideModel(value).label, "API label 6");
  assert.match(guideView(value), /Execution history/);
  assert.doesNotMatch(guideView(value), /passkey|guide-owner/);
  value.steps[5].label = "";
  assert.equal(guideView(value), "");
});

test("complete week offers the shared restart and Sandbox proof, both reachable", () => {
  const html = guideView(state(11));
  assert.match(html, /data-guide-action="reset"[^>]*>Restart the week/);
  assert.match(html, /href="#proof">Sandbox proof/);
  assert.match(guideView(state(11, {running:true})), /data-guide-action="reset" disabled/);
});

test("only owner moments use the matte owner treatment", () => {
  for (let index = 1; index <= 11; index++) assert.equal(guideView(state(index)).includes("guide-owner"), [6,8].includes(index));
});

test("API labels and notices are escaped and email-masked", () => {
  const value = state(4); value.steps[3].label = '<img src=x onerror="attack()"> buyer@example.com';
  const html = guideView(value, {notice:'<script>attack()</script>'});
  assert.doesNotMatch(html, /<img|<script>|buyer@example.com/);
  assert.match(html, /&lt;img/); assert.match(html, /b\*\*\*@example.com/);
});

test("dismissals survive polling and navigation, advance on the next step and reset with the week", () => {
  const {doc, host, click} = documentDouble();
  const guide = createGuide(doc);
  guide.update(state()); click("dismiss"); guide.update(state());
  assert.equal(host.hidden, true);
  guide.update(state(1)); assert.equal(host.hidden, false);
  click("dismiss"); guide.update(state(1)); assert.equal(host.hidden, true);
  guide.update(state(2)); assert.equal(host.hidden, false);
  guide.update(state()); assert.equal(host.hidden, false);
  guide.update(null); assert.equal(host.hidden, true); assert.equal(host.innerHTML, "");
});

test("details stay open during busy feedback and unchanged polling preserves focused DOM", () => {
  const {doc, host} = documentDouble(); const guide = createGuide(doc);
  guide.update(state()); host.details.open = true;
  const button = doc.getElementById("guide-step"); button.focus();
  guide.update(state()); assert.equal(doc.activeElement, button);
  guide.update(state(0, {running:true})); assert.equal(host.details.open, true);
  assert.equal(doc.activeElement, doc.getElementById("guide-title"));
});

test("Next step pulses once after confirmed step one, never while running or under reduced motion", () => {
  const {doc, pulses} = documentDouble(), guide = createGuide(doc);
  guide.update(state()); guide.update(state(1,{running:true})); assert.deepEqual(pulses, []);
  guide.update(state(1)); guide.update(state(1)); guide.update(state()); guide.update(state(1));
  assert.deepEqual(pulses, ["c57-next-pulse"]);
  const quiet = documentDouble(true); createGuide(quiet.doc).update(state(1));
  assert.deepEqual(quiet.pulses, []);
});

test("section pointers scroll and focus the existing heading without changing the route", () => {
  const {doc, host, scrolls} = documentDouble(true); createGuide(doc).update(state(2));
  let prevented = false;
  host.onclick({target:{closest:selector => selector === "[data-guide-target]" ? {dataset:{guideTarget:"makers-title"}} : null}, preventDefault() {prevented = true;}});
  assert.ok(prevented); assert.deepEqual(scrolls, [{behavior:"auto", block:"start"}]);
  assert.equal(doc.activeElement, doc.getElementById("makers-title"));
});

test("Start and compact Next share one POST guard, busy feedback, completion and fixed errors", async () => {
  const {doc, host, click} = documentDouble(), calls = [];
  let value = state(), release;
  const gate = new Promise(resolve => {release = resolve;});
  const guide = createGuide(doc, {step:() => demo.step()});
  const demo = createDemoBar(doc, {onState:(s,n) => guide.update(s,n), wait:async () => {}, fetcher:async (url, options={}) => {
    if (options.method === "POST") {calls.push([url,options.body]); await gate; return response(state(0,{running:true}),202);}
    return response(value);
  }});
  await demo.load(); click("step"); const second = demo.step();
  assert.equal(doc.getElementById("guide-step").disabled, true);
  value = state(1); release(); await second;
  for (let i = 0; i < 8 && demo.state.step === 0; i++) await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(calls, [["/api/v1/demo/step",undefined]]);
  assert.match(host.innerHTML, /API label 1/);
  const broken = createDemoBar(doc,{onState:(s,n) => guide.update(s,n), fetcher:async (_url, options={}) => options.method === "POST" ? response({error:{code:"DEMO_BUSY",message:"private"}},409) : response(state())});
  await broken.load(); await broken.step();
  assert.match(host.innerHTML, /A staged step is already running/); assert.doesNotMatch(host.innerHTML, /private/);
});

test("guide restart reuses confirmation, and absent demo endpoints remove all guidance", async () => {
  const {doc, host, click} = documentDouble(); let allowed = false, asks = 0, posts = 0, available = true;
  const guide = createGuide(doc,{reset:() => demo.reset()});
  const demo = createDemoBar(doc,{onState:(s,n) => guide.update(s,n), confirm:() => {asks++; return allowed;},
    fetcher:async (_url, options={}) => {
      if (options.method === "POST") {posts++; return response({error:{code:"DEMO_BUSY"}},409);}
      return response(state(11), available ? 200 : 404);
    }});
  await demo.load(); click("reset"); assert.equal(asks,1); assert.equal(posts,0);
  allowed = true; await demo.reset(); assert.equal(asks,2); assert.equal(posts,1);
  available = false; await demo.load(); assert.equal(host.hidden,true); assert.equal(host.innerHTML,"");
});
