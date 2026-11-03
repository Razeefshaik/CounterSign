import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {fromBase64url, toBase64url, decodeOptions, encodeCredential, passkeySupported, countersign} from "../passkey.mjs";
import {decisionView} from "../views.mjs";

const scope = {PublicKeyCredential: class {}, navigator: {credentials: {create: async () => credential, get: async () => credential}}};
const credential = {id: "AQID", rawId: new Uint8Array([1, 2, 3]), type: "public-key",
  response: {clientDataJSON: new Uint8Array([4]), authenticatorData: new Uint8Array([5]), signature: new Uint8Array([6])}};
const proposal = {id: "p", state: "NEEDS_OWNER", verdict: "NEEDS_OWNER", tier: "OWNER",
  approval_request: {payload_hash: "sha256:" + "a".repeat(64), expires_at: "2030-01-01T00:00:00Z"}};
const options = {challenge: "AQID", rpId: "localhost", allowCredentials: [{id: "AQID", type: "public-key"}]};

test("base64url round trips binary, including URL alphabet and padding", () => {
  for (const data of [[], [0], [255, 254], Array.from({length: 256}, (_, i) => i)]) {
    const encoded = toBase64url(new Uint8Array(data));
    assert.doesNotMatch(encoded, /[+/=]/);
    assert.deepEqual([...fromBase64url(encoded)], data);
  }
  const decoded = decodeOptions({...options, user: {id: "AQID"}, excludeCredentials: [{id: "AQID"}]});
  assert.deepEqual([...decoded.challenge], [1, 2, 3]);
  assert.deepEqual([...decoded.user.id], [1, 2, 3]);
  assert.deepEqual([...decoded.excludeCredentials[0].id], [1, 2, 3]);
  assert.equal(encodeCredential(credential).response.signature, "Bg");
});

test("unsupported browser shows only the existing plain approval action", () => {
  assert.equal(passkeySupported({}), false);
  const fallback = decisionView(proposal, {mode: "demo", clock: "2026-11-03T00:00:00Z"}, false, {passkeyScope: {}});
  assert.doesNotMatch(fallback, /id="passkey-approve"/);
  assert.match(fallback, /id="approve"/);
  const supported = decisionView(proposal, {mode: "demo", clock: "2026-11-03T00:00:00Z"}, false, {passkeyScope: scope});
  assert.ok(supported.indexOf('id="passkey-approve"') < supported.indexOf('id="approve"'));
  assert.match(supported, /Countersign with passkey/);
  assert.ok(supported.includes(proposal.approval_request.payload_hash));
});

test("signed timeline comes only from a recorded passkey approval", () => {
  const html = decisionView({...proposal, approval: {approved_by: "owner:passkey:1234abcd"}}, {clock: "2026-11-03"});
  assert.match(html, /Countersigned with passkey · …abcd/);
  assert.doesNotMatch(decisionView(proposal, {clock: "2026-11-03"}), /Countersigned with passkey/);
});

test("first use registers then signs the same displayed hash with same-origin requests", async () => {
  const calls = [], replies = [{registered: false}, {challenge_id: "register", publicKey: {...options, user: {id: "AQID"}}},
    {registered: true}, {registered: true, challenge_id: "approve", publicKey: options}, {approval: {}}];
  const result = await countersign(proposal, {scope, token: "test-only", fetcher: async (url, init) => {
    calls.push([url, JSON.parse(init.body), init.headers]);
    return {ok: true, json: async () => replies.shift()};
  }});
  assert.deepEqual(result, {approval: {}});
  assert.equal(calls.length, 5);
  assert.equal(calls[4][1].payload_hash, proposal.approval_request.payload_hash);
  assert.equal(calls[4][1].challenge_id, "approve");
  assert.ok(calls.every(([url, , headers]) => url.startsWith("/api/v1/owner/") && headers["X-Owner-Token"] === "test-only"));
});

test("C61: a new device registers its own passkey even when another is registered", async () => {
  const urls = [], replies = [{registered: true, challenge_id: "stale", publicKey: options},
    {challenge_id: "register", publicKey: {...options, user: {id: "AQID"}}}, {registered: true},
    {registered: true, challenge_id: "approve", publicKey: options}, {approval: {}}];
  const result = await countersign(proposal, {scope, newDevice: true, fetcher: async url => {
    urls.push(url); return {ok: true, json: async () => replies.shift()};
  }});
  assert.deepEqual(result, {approval: {}});
  assert.deepEqual(urls.map(url => url.split("/").slice(-2).join("/")),
    ["passkey/options", "register/options", "register/verify", "passkey/options", "passkey/approve"]);
});

test("C61: a device that already holds a registered passkey skips setup and signs", async () => {
  const held = {...scope, navigator: {credentials: {get: scope.navigator.credentials.get,
    create: async () => {throw Object.assign(new Error("excluded"), {name: "InvalidStateError"});}}}};
  const urls = [], replies = [{registered: true}, {challenge_id: "register", publicKey: {...options, user: {id: "AQID"}}},
    {registered: true, challenge_id: "approve", publicKey: options}, {approval: {}}];
  await countersign(proposal, {scope: held, newDevice: true, fetcher: async url => {
    urls.push(url); return {ok: true, json: async () => replies.shift()};
  }});
  assert.ok(!urls.some(url => url.endsWith("register/verify")));
  assert.ok(urls.at(-1).endsWith("passkey/approve"));
  // Without newDevice, a refused registration is still an error.
  await assert.rejects(countersign(proposal, {scope: held, fetcher: async () =>
    ({ok: true, json: async () => ({registered: false, challenge_id: "r", publicKey: {...options, user: {id: "AQID"}}})})}), /excluded/);
});

test("cancellation, server refusal, and navigation never retry or call plain approve", async () => {
  let posts = 0;
  const cancel = {...scope, navigator: {credentials: {create: scope.navigator.credentials.create, get: async () => {throw new Error("cancelled");}}}};
  const fetcher = async () => {posts++; return {ok: true, json: async () => ({registered: true, publicKey: options, challenge_id: "one"})};};
  await assert.rejects(countersign(proposal, {scope: cancel, fetcher}));
  assert.equal(posts, 1);
  await assert.rejects(countersign(proposal, {scope, fetcher, isCurrent: () => false}), /VIEW_CHANGED/);
  assert.equal(posts, 1);
  await assert.rejects(countersign(proposal, {scope, fetcher: async () => ({ok: false, status: 409})}), {status: 409});
});

test("passkey styles are separately linked and use accent, never highlight", async () => {
  const css = await readFile(new URL("../passkey.css", import.meta.url), "utf8");
  assert.match(css, /var\(--accent\)/); assert.doesNotMatch(css, /--highlight/);
  assert.match(await readFile(new URL("../index.html", import.meta.url), "utf8"), /href="passkey.css"/);
});
