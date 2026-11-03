import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {proofView, sourceURL, sourceLink, createProof} from "../proof.mjs";
import {route} from "../workspace.mjs";

const bundle = JSON.parse(await readFile(new URL("../results/sandbox-proof.json", import.meta.url)));
const fact = value => ({value, source: "recordings/sandbox/2026-10-23/D1/01_oauth_token.json"});

test("proof renders all recorded checks and separates sandbox from actual model on mock", () => {
  const html = proofView(bundle);
  assert.equal((html.match(/class="proof-check view-card"/g) || []).length, 13);
  assert.match(html, /1,041/); assert.match(html, /1,007/);
  assert.match(html, /34 transport errors/); assert.match(html, /simulator event/);
  assert.match(html, /What we changed/); assert.match(html, /UNCLAIMED/);
  assert.match(html, /RETURNED/); assert.match(html, /RESOLVED_BUYER_FAVOUR/);
  assert.match(html, /gemini-3.5-flash-lite/); assert.match(html, /COMPLETE_WITH_GAPS/);
  assert.match(html, /local mock, not the sandbox/); assert.match(html, /Chose WAIT/);
  assert.match(html, /not live calls/); assert.match(html, /not demonstrated/);
  assert.deepEqual(route("#proof/D6"), {name: "proof", id: "D6"});
});

test("source links use fixed GitHub origin and reject unsafe or traversing paths", () => {
  assert.equal(sourceURL("docs/SANDBOX_FINDINGS.md", "line:17"),
    "https://github.com/Razeefshaik/CounterSign/blob/main/docs/SANDBOX_FINDINGS.md#L17");
  assert.match(sourceLink(fact("test")), /target="_blank" rel="noopener noreferrer"/);
  for (const path of ["javascript:alert(1)", "https://evil.test/file", "docs/../private", "docs/a\".md", "docs//a", "/docs/a.md"])
    assert.equal(sourceURL(path), null);
});

test("missing proof fields stay unavailable, never fabricated zeros or outcomes", () => {
  const html = proofView({checks: [{id: "D1", topic: fact("Auth")} ]});
  assert.match(html, /—/);
  assert.doesNotMatch(html, />0<|gemini|CONFIRMED|Chose WAIT|>BLOCK</);
  assert.match(proofView(), /No recorded sandbox checks/);
});

test("every dynamic text field is escaped, including outcomes and identifiers", () => {
  const planted = fact('<img src=x onerror="alert(1)">');
  const html = proofView({checks: [{id: 'D1" onclick="bad', topic: planted, verdict: planted,
    finding: planted, debug_ids: [planted], resources: [{kind: "<script>", id: planted,
      status: planted, outcome: planted}]}], real_model: {model: planted, decisions: [
      {decision: planted, outcome: planted, observed_action: planted} ]}});
  assert.doesNotMatch(html, /<img |<script>|id="proof-D1" onclick=/);
  assert.match(html, /&lt;img/);
});

test("proof loader reads only the static bundle, retries failures and caches success", async () => {
  let requests = 0;
  const element = {innerHTML: ""};
  const controller = createProof({getElementById: () => element}, async path => {
    assert.equal(path, "results/sandbox-proof.json");
    return {ok: ++requests > 1, json: async () => bundle};
  });
  await assert.rejects(controller.load(), /PROOF_UNAVAILABLE/);
  await controller.load(); await controller.load(); controller.draw();
  assert.equal(requests, 2);
  assert.match(element.innerHTML, /Verified on the real PayPal sandbox/);
});

test("proof navigation follows Evaluation replay and new files are packaged as static assets", async () => {
  const index = await readFile(new URL("../index.html", import.meta.url), "utf8");
  assert.ok(index.indexOf('data-view="proof"') > index.indexOf('data-view="replay"'));
  assert.match(index, /href="proof.css"/);
  assert.match(index, /class="brand-proof" href="#proof"/);
  const css = await readFile(new URL("../proof.css", import.meta.url), "utf8");
  assert.match(css, /prefers-reduced-motion/);
  assert.doesNotMatch(css, /--highlight|--owner|#ec4899/);
});
