import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {createClient, submitDecision} from "../api.mjs";
import {decisionView, runView, cashView, auditView, graphView, canDecide, esc} from "../views.mjs";
import {route, createWorkspaceViews} from "../workspace.mjs";
import {demoBarView, createDemoBar, resultText} from "../demo.mjs";
import {parseRuns, parseEvidence, pairRuns, replayEvents, replayView, scenarios} from "../replay.mjs";

const fixture = async name => JSON.parse(await readFile(new URL(`../fixtures/${name}.json`, import.meta.url)));
const proposals = (await fixture("proposals")).items;
const owner = proposals.find(p => p.state === "NEEDS_OWNER");
const meta = await fixture("meta");
const response = (body, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => body});

const replayRows = parseRuns(await readFile(new URL("../replays/runs.jsonl",import.meta.url),"utf8"));
const replayProof = parseEvidence(await readFile(new URL("../replays/evidence.json",import.meta.url),"utf8"));
test("bundled mock replay preserves actual model-none outcomes and ground truth for both scenarios", () => {
  assert.equal(replayRows.length,4);
  for (const scenario of Object.keys(scenarios)) {
    const pair=pairRuns(replayRows,scenario,"rules_only");
    assert.ok(pair.left && pair.right);
    const html=replayView({rows:replayRows,evidence:replayProof,scenario,position:1000});
    assert.match(html,/Rules-only comparison/); assert.match(html,/Model: <strong>none/);
    assert.match(html,/\$486.00/); assert.match(html,/Final mock ground-truth payouts/);
    assert.match(html,/do not establish real-model performance/);
  }
  const failed=replayRows.find(row=>row.error);
  assert.ok(failed);
  assert.match(replayView({rows:replayRows,evidence:replayProof,scenario:failed.scenario_id}), /EVALUATION_FAILED/);
});
test("replay never pairs repetitions, seeds or runs that differ and exposes missing baselines", () => {
  const row=replayRows[0], altered={...replayRows[1],k:9};
  const pair=pairRuns([row,altered],row.scenario_id,"rules_only");
  assert.ok(pair.left); assert.equal(pair.right,undefined);
  assert.match(replayView({rows:replayRows,baseline:"baseline_llm"}),/No matching mock run/);
  assert.match(replayView({rows:replayRows}),/Action trace unavailable/);
  assert.match(replayView({rows:replayRows}),/Ground-truth payouts unavailable/);
  assert.match(replayView(),/0 of 0 recorded actions/);
});
test("replay imports reject sandbox, duplicate and malformed metrics or evidence", () => {
  for (const text of ["invalid", "", JSON.stringify({...replayRows[0],ground:"sandbox"}),
    JSON.stringify({...replayRows[0],invariants:{duplicate_settlement:-1}}),
    [replayRows[0],replayRows[0]].map(JSON.stringify).join("\n")]) assert.throws(()=>parseRuns(text));
  for (const value of [{},[{...replayProof[0],ground:"sandbox"}],[{...replayProof[0],actions:{}}],
    [{...replayProof[0],payout_items:[{...replayProof[0].payout_items[0],amount:{value:486,currency:"USD"}}]}]]) assert.throws(()=>parseEvidence(JSON.stringify(value)));
});
test("replay steps show recorded action order, escape imported text and never invent payouts", () => {
  const pair=pairRuns(replayRows,Object.keys(scenarios)[0],"rules_only");
  const events=replayEvents(pair.left,pair.right,replayProof);
  assert.ok(events.length>0);
  assert.ok(events.every((event,i)=>i===0 || Date.parse(event.at)>=Date.parse(events[i-1].at)));
  assert.match(replayView({rows:replayRows,evidence:replayProof,position:0}),/No actions at this replay position/);
  const attack=replayProof.map(row=>({...row,actions:row.actions.map(a=>({...a,action:'<img src=x> secret@example.com'}))}));
  const html=replayView({rows:replayRows,evidence:attack,position:100});
  assert.ok(!html.includes('<img') && !html.includes('secret@example.com'));
  assert.match(html,/&lt;img/i);
});

test("contract 1.0.3 injection guard renders as a blocked rule without renaming the flag", () => {
  assert.equal(meta.contract_version, "1.0.3");
  const blocked = proposals.find(p => p.flags.some(f => f.type === "PROMPT_INJECTION"));
  const guard = blocked.rule_results.find(r => r.rule === "INJECTION_GUARD");
  assert.equal(guard.outcome, "BLOCK");
  const html = decisionView(blocked, meta);
  assert.match(html, /INJECTION_GUARD/);
  assert.match(html, /PROMPT_INJECTION/);
  assert.match(html, /id="approve"[^>]*disabled/);
});

test("decision renders every supplied rule and flags, including blocked owner-tier proposals", () => {
  const blocked = proposals.find(p => p.verdict === "BLOCK");
  const html = decisionView(blocked, meta, true);
  for (const rule of blocked.rule_results) {
    assert.ok(html.includes(rule.rule)); assert.ok(html.includes(rule.outcome));
  }
  assert.ok(html.includes("PROMPT_INJECTION"));
  assert.match(html, /class="staged">staged/);
  assert.match(html, /id="approve"[^>]*disabled/);
  const pending = decisionView(owner, meta);
  assert.ok(pending.includes(owner.approval_request.payload_hash));
  assert.match(pending, /id="approve" class="primary" >/);
});

test("approval controls fail closed for expired, terminal, blocked or incomplete proposals", () => {
  assert.equal(canDecide(owner, meta.clock), true);
  for (const patch of [{state:"APPROVED"}, {verdict:"BLOCK"}, {approval_request:null},
    {approval_request:{...owner.approval_request, expires_at:meta.clock}}]) {
    assert.equal(canDecide({...owner, ...patch}, meta.clock), false);
  }
  assert.equal(canDecide(owner, "not a clock"), false);
});

test("decision expiry follows staged meta clock independently of browser wall time", () => {
  const original = Date.now;
  try {
    Date.now = () => Date.parse("2020-01-01T00:00:00Z");
    const expired = {...meta, clock:owner.approval_request.expires_at};
    assert.match(decisionView(owner, expired), /id="approve"[^>]*disabled/);
    Date.now = () => Date.parse("2040-01-01T00:00:00Z");
    assert.doesNotMatch(decisionView(owner, meta), /id="approve"[^>]*disabled/);
    assert.match(decisionView(owner, {...meta, clock:null}), /id="approve"[^>]*disabled/);
  } finally {Date.now = original;}
});

test("untrusted text is inert in proposals, flags, audit and SVG; full emails are masked", () => {
  const attack = '<img src=x onerror="alert(1)"> secret@example.com';
  const changed = {...owner, rationale:attack, params:{note:attack}, flags:[{type:"PROMPT_INJECTION", excerpt:attack, source:attack}]};
  const html = decisionView(changed, meta) + auditView([{at:meta.clock, actor:attack, kind:attack, details:{note:attack}}]) + graphView({nodes:[{id:"a",label:attack,type:attack}],edges:[]});
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes("secret@example.com"));
  assert.ok(html.includes("s***@example.com"));
  assert.ok(html.includes("&lt;img"));
  assert.equal(esc('<script>"&'), "&lt;script&gt;&quot;&amp;");
});

test("payout fixture lines and replacements total the displayed amount; attempts retain their refs", async () => {
  const run = (await fixture("run-details"))[0];
  assert.equal(run.lines.length, run.line_count);
  assert.equal([...run.lines, ...run.replacements].reduce((n, r) => n + r.amount.minor, 0), run.total.minor);
  assert.equal(owner.params.lines.length, run.line_count);
  const html = runView(run);
  for (const text of ["Maker B", "Maker C", "$1,086.00", "$414.00", "RETURNED", "PENDING", "5UXD2E8A7EBQJ", run.lines[0].attempts[0].request_id]) assert.ok(html.includes(text), text);
});

test("cash separates earmarks and withheld from the funding formula and shows freshness", async () => {
  const cash = await fixture("cash");
  const html = cashView(cash);
  for (const text of ["$1,340.00", "$1,086.00", "$254.00", "$414.00", "not subtracted", "10500", "FRESH", cash.formula]) assert.ok(html.includes(text), text);
  assert.equal(cash.paypal.available.minor - cash.outflows_after_refresh.total.minor - cash.cash_reservations.total.minor, cash.unreserved.minor);
  assert.match(cashView({...cash, paypal:{...cash.paypal, stale:true}}), /STALE/);
});

test("empty financial views explain what is missing and never fabricate money", () => {
  assert.match(cashView(null), /Cash is unavailable/);
  assert.match(runView(null), /No payout runs yet/);
  assert.match(decisionView(null,meta), /Choose an incident or proposal/);
  assert.ok(!cashView(null).includes("$0.00"));
  assert.throws(()=>parseRuns(JSON.stringify({...replayRows[0],invariants:{}})));
});

test("audit and money graph retain relationships and have explicit empty states", async () => {
  assert.match(auditView((await fixture("audit-log")).items), /Shield Verdict/);
  const graph = await fixture("money-graph");
  const html = graphView(graph);
  assert.match(html, /<svg[^>]*role="img"/);
  for (const edge of graph.edges) for (const value of Object.values(edge)) assert.ok(html.includes(value));
  assert.match(graphView({nodes:[],edges:[]}), /No linked money records/);
  assert.match(auditView([]), /No records yet/);
});

test("approval POST carries exact hash and owner header, rejection carries reason only", async () => {
  const requests = [];
  const client = createClient({fetcher:async (url, options) => {requests.push({url,...options}); return response({approval:{id:"test"}},201);}});
  await client.decide(owner,"approve",{token:"fake-test-only"});
  await client.decide(owner,"reject",{reason:"  Need updated evidence  "});
  assert.equal(requests[0].url, `/api/v1/proposals/${owner.id}/approve`);
  assert.deepEqual(JSON.parse(requests[0].body), {payload_hash:owner.approval_request.payload_hash, approved_by:"owner"});
  assert.equal(requests[0].headers["X-Owner-Token"],"fake-test-only");
  assert.deepEqual(JSON.parse(requests[1].body), {reason:"Need updated evidence"});
  assert.equal(requests[1].headers["X-Owner-Token"],undefined);
  await assert.rejects(client.decide(owner,"reject",{}), /reason/);
  await assert.rejects(client.decide({...owner,approval_request:null},"approve"), /Reload/);
});

test("409 invalidates and GETs the proposal once, without silently retrying the POST", async () => {
  const calls = [];
  const client = createClient({fetcher:async (url, options) => {
    calls.push({url, method: options.method || "GET"});
    return options.method ? response({},409) : response({...owner, state:"EXPIRED"});
  }});
  let valid = true, replacement;
  const result = await submitDecision(client,owner,"approve",{},() => {valid=false;},async () => {
    assert.equal(valid,false); replacement = await client.get("proposals",owner.id);
  });
  assert.equal(result.stale,true);
  assert.deepEqual(calls.map(c => c.method),["POST","GET"]);
  assert.equal(replacement.state,"EXPIRED");
});

test("failed stale reload keeps invalidated data and unavailable endpoints never report success", async () => {
  let invalid = false;
  const client = createClient({fetcher:async () => response({},409)});
  await assert.rejects(submitDecision(client,owner,"approve",{},() => {invalid=true;},async () => {throw Error("offline");}), /offline/);
  assert.equal(invalid,true);
  for (const code of [401,403,404,405,500]) {
    const unavailable = createClient({fetcher:async () => response({error:{message:"private server data"}},code)});
    await assert.rejects(unavailable.decide(owner,"approve"), error => error.status === code && !error.message.includes("private"));
  }
});

test("API pagination is complete and fixture graph does not show a different dispute", async () => {
  const urls=[];
  const client = createClient({fetcher:async url => {urls.push(url); return response(url.includes("?") ? {items:[{id:2}],next_cursor:null} : {items:[{id:1}],next_cursor:"a/b ?"});}});
  assert.deepEqual(await client.list("audit-log"),[{id:1},{id:2}]);
  assert.ok(urls[1].includes("cursor=a%2Fb%20%3F"));
  const staticClient = createClient({fixture:true,fetcher:async url => response(await fixture(url.replace("fixtures/", "").replace(".json", "")))});
  assert.deepEqual(await staticClient.singleton("money-graph",{dispute_id:"missing"}),{nodes:[],edges:[]});
  assert.equal((await staticClient.get("payout-runs", (await fixture("run-details"))[0].id)).lines.length,2);
});

// Small DOM boundary double exercises controller ordering without a new frontend dependency.
function documentDouble() {
  const elements = new Map();
  for (const id of ["breadcrumb","intro","incident-stats","incidents","incident-detail","workspace-view","view-title","view-picker","view-body","view-notice","record-picker","view-refresh"]) elements.set(id,{id,hidden:false,innerHTML:"",textContent:""});
  let body = "";
  Object.defineProperty(elements.get("view-body"), "innerHTML", {
    get: () => body,
    set: html => {
      body = html;
      for (const id of ["approve","reject","reject-reason","owner-token"]) elements.delete(id);
      for (const match of html.matchAll(/<(?:button|input|textarea)[^>]*id="([^"]+)"[^>]*>/g)) {
        elements.set(match[1], {value:"", disabled:match[0].includes("disabled")});
      }
    },
  });
  return {elements, getElementById:id => elements.get(id) || null, querySelectorAll:() => []};
}
test("refreshing a decision reloads staged metadata and disables an expired approval", async () => {
  const doc = documentDouble();
  let clock = meta.clock;
  const views = createWorkspaceViews(doc, {hash:`#decision/${owner.id}`}, async url =>
    response(url.endsWith("/meta") ? {...meta, clock} :
      url.endsWith("/proposals") ? {items:[owner],next_cursor:null} : owner));
  await views.setWorkspace({meta:{...meta},fixture:false,incidents:[],disputes:[]});
  assert.equal(doc.getElementById("approve").disabled, false);
  clock = owner.approval_request.expires_at;
  await doc.getElementById("view-refresh").onclick();
  assert.equal(doc.getElementById("approve").disabled, true);
});
test("controller prevents duplicate decisions, clears live owner token and reloads after success", async () => {
  const doc = documentDouble(), location={hash:`#decision/${owner.id}`};
  let postCount=0, finish, state=owner;
  const post = new Promise(resolve => {finish=resolve;});
  const views = createWorkspaceViews(doc,location,async (url, options) => {
    if (url.endsWith("/meta")) return response({...meta,mode:"live",simulated:false,paypal_env:"sandbox"});
    if (options.method === "POST") {postCount++; assert.equal(options.headers["X-Owner-Token"],"fake-test-only"); return post;}
    if (url.endsWith("/meta")) return response(meta);
    return response(url.endsWith("/proposals") ? {items:[state],next_cursor:null} : state);
  });
  await views.setWorkspace({meta:{...meta,mode:"live",simulated:false,paypal_env:"sandbox"},fixture:false,incidents:[],disputes:[]});
  doc.getElementById("owner-token").value="fake-test-only";
  const click=doc.getElementById("approve").onclick;
  const pending=click(); await click();
  assert.equal(postCount,1);
  assert.equal(doc.getElementById("owner-token").value,"");
  assert.equal(doc.getElementById("approve").disabled,true);
  state={...owner,state:"APPROVED"}; finish(response({approval:{id:"test"}},201)); await pending;
  assert.equal(doc.getElementById("approve").disabled,true);
  assert.match(doc.getElementById("view-notice").textContent,/decision was recorded/);
});

test("controller disables expired approval after 409 and reports unavailable preview writes", async () => {
  for (const code of [409,405]) {
    const doc = documentDouble(), location={hash:`#decision/${owner.id}`};
    let changed = false;
    const views = createWorkspaceViews(doc,location,async (url, options) => {
      if (url.endsWith("/meta")) return response(meta);
      if (options.method === "POST") {changed=true; return response({},code);}
      const data=changed && code === 409 ? {...owner,state:"EXPIRED"} : owner;
      if (url.endsWith("/meta")) return response(meta);
    return response(url.endsWith("/proposals") ? {items:[data],next_cursor:null} : data);
    });
    await views.setWorkspace({meta,fixture:false,incidents:[],disputes:[]});
    await doc.getElementById("approve").onclick();
    assert.equal(doc.getElementById("approve").disabled,true);
    assert.match(doc.getElementById("view-notice").textContent,code === 409 ? /changed or expired/ : /unavailable in this preview/);
  }
});
test("navigation ignores late data from the previous view", async () => {
  const doc = documentDouble(), location={hash:"#cash"};
  let resolveCash;
  const delayed = new Promise(resolve => {resolveCash=resolve;});
  const views = createWorkspaceViews(doc,location,async url => url.endsWith("cash") ? delayed : response({items:[],next_cursor:null}));
  const pending=views.setWorkspace({meta,fixture:false,incidents:[],disputes:[]});
  location.hash="#audit"; await views.render();
  resolveCash(response(await fixture("cash"))); await pending;
  assert.match(doc.getElementById("view-body").innerHTML,/Execution history/);
  assert.ok(!doc.getElementById("view-body").innerHTML.includes("Funding formula"));
  assert.deepEqual(route("#runs/run-123"),{name:"runs",id:"run-123"});
  assert.deepEqual(route("#unknown"),{name:"incidents",id:""});
});

test("simulation banner sits outside all routed panels and navigation stays available on phones", async () => {
  const html = await readFile(new URL("../index.html",import.meta.url),"utf8");
  const css = await readFile(new URL("../styles.css",import.meta.url),"utf8");
  for (const name of ["incidents","decision","runs","audit","cash","graph"]) assert.ok(html.includes(`data-view="${name}"`));
  assert.ok(html.indexOf('id="simulation"') < html.indexOf('class="layout"'));
  assert.ok(!html.includes("dismiss"));
  assert.match(css,/\.simulation\{position:sticky;top:0/);
  assert.match(css,/\.sidebar nav\{display:flex;order:3;width:100%;overflow-x:auto/);
});

// M17 demo bar. State shape follows GET /api/v1/demo/state (CONTRACTS section 3).
const demoSteps = [["setup","Monday: stage orders",true],["friday_plan","Friday: plan the run",false],["pay","Following week: pay A",true]];
const demoState = (done = 0, extra = {}) => ({scenario:"demo_week", step:done, total:demoSteps.length, complete:done === demoSteps.length,
  replaying:true, running:false, simulated:true, simulation_label:"Simulated: mock PayPal", model:"scripted",
  staged_note:"Staged: simulated on the mock.", clock:"2026-11-02T09:00:00+00:00", last_result:null,
  steps:demoSteps.map(([name,label,staged], i) => ({index:i + 1, name, label, staged, done:i < done, interrupted:false})),
  next_label:demoSteps[done]?.[1] ?? null, ...extra});
function barDouble() {
  const elements = new Map(), bar = {hidden:true};
  let html = "";
  Object.defineProperty(bar, "innerHTML", {get: () => html, set: value => {
    html = value; elements.clear();
    for (const match of value.matchAll(/<(button|p)[^>]*id="([^"]+)"[^>]*>/g)) elements.set(match[2], {disabled:match[0].includes("disabled"), textContent:""});
  }});
  return {bar, getElementById: id => id === "demo-bar" ? bar : elements.get(id) || null};
}

test("demo bar is never empty on first load and labels every staged step", () => {
  const html = demoBarView(demoState(0));
  assert.match(html, /Step <strong>0<\/strong> of 3/);
  assert.match(html, /Next<\/span> Monday: stage orders <span class="staged">staged<\/span>/);
  assert.match(html, /Nothing yet/);
  assert.equal((html.match(/class="staged">staged/g) || []).length, 1 + 1 + 2);
  assert.match(html, /Scripted model · mock PayPal/);
  assert.match(html, /Mon, 02 Nov 2026, 09:00 UTC/);
  assert.match(html, /id="demo-step" class="primary" >Run next staged step/);
  const done = demoBarView(demoState(3, {last_result:{action:"step", ok:true, code:"DEMO_STEP_DONE"}}));
  assert.match(done, /Week complete/);
  assert.match(done, /id="demo-step"[^>]*disabled/);
  assert.match(demoBarView(demoState(1, {running:true, action:"step"})), /id="demo-reset"[^>]*disabled/);
});

test("demo bar text is escaped and failures show only fixed codes", () => {
  const attack = demoState(0, {steps:[{index:1, name:"x", label:'<img src=x onerror="1">', staged:true, done:false}], last_result:{action:"step", ok:false, code:"<b>X</b>"}});
  const html = demoBarView(attack);
  assert.ok(!html.includes("<img") && !html.includes("<b>X"));
  assert.match(resultText(demoState(0, {last_result:{action:"step", ok:false, code:"DEMO_TICK_FAILED"}})), /stopped \(DEMO_TICK_FAILED\)/);
  assert.match(resultText(demoState(0, {last_result:{action:"reset", ok:true, code:"DEMO_RESET"}})), /Week restarted/);
  assert.match(resultText(demoState(0)), /Run the first staged step/);
});

test("demo bar stays hidden when the server has no demo endpoints (live mode)", async () => {
  const doc = barDouble();
  const demo = createDemoBar(doc, {fetcher: async () => response({error:{code:"NOT_FOUND"}}, 404)});
  assert.equal(await demo.load(), null);
  assert.equal(doc.bar.hidden, true);
  assert.equal(doc.bar.innerHTML, "");
});

test("one click posts once without a body, follows the step and refreshes the dashboard", async () => {
  const doc = barDouble(), calls = [];
  let reads = 0, changed = 0;
  const fetcher = async (url, options = {}) => {
    calls.push({url, method:options.method || "GET", body:options.body});
    if (options.method === "POST") return response(demoState(0, {running:true, action:"step"}), 202);
    reads++;
    return response(reads === 1 ? demoState(0) : reads === 2 ? demoState(0, {running:true, action:"step"}) : demoState(1, {last_result:{action:"step", ok:true, code:"DEMO_PAUSED_AFTER_SETUP"}}));
  };
  const demo = createDemoBar(doc, {fetcher, wait: async () => {}, onChange: async () => {changed++;}});
  await demo.load();
  assert.equal(doc.bar.hidden, false);
  const first = doc.getElementById("demo-step").onclick();
  await doc.getElementById("demo-step").onclick?.();
  await first;
  assert.deepEqual(calls.filter(c => c.method === "POST").map(c => [c.url, c.body]), [["/api/v1/demo/step", undefined]]);
  assert.equal(changed, 1);
  assert.equal(demo.state.step, 1);
  assert.match(doc.bar.innerHTML, /Step complete/);
});

test("a busy or finished week reports the server's fixed reason and restart asks first", async () => {
  const doc = barDouble(), posts = [];
  const fetcher = async (url, options = {}) => {
    if (options.method === "POST") {posts.push(url); return response({error:{code:"DEMO_BUSY", message:"private"}}, 409);}
    return response(demoState(1));
  };
  let asked = 0;
  const demo = createDemoBar(doc, {fetcher, confirm: () => {asked++; return asked > 1;}});
  await demo.load();
  await doc.getElementById("demo-step").onclick();
  assert.equal(doc.getElementById("demo-status").textContent, "A staged step is already running. Wait for it to finish.");
  await doc.getElementById("demo-reset").onclick();
  assert.deepEqual(posts, ["/api/v1/demo/step"]);
  await doc.getElementById("demo-reset").onclick();
  assert.deepEqual(posts, ["/api/v1/demo/step", "/api/v1/demo/reset"]);
});

test("demo bar sits with the banner outside every routed view", async () => {
  const html = await readFile(new URL("../index.html",import.meta.url),"utf8");
  const bar = html.indexOf('id="demo-bar"');
  assert.ok(bar > html.indexOf('id="simulation"') && bar < html.indexOf('class="layout"'));
  assert.match(html, /<section id="demo-bar"[^>]*hidden><\/section>/);
});

test("cash before the first balance read says unavailable, not zero or a generic failure", async () => {
  const doc = documentDouble(), location = {hash:"#cash"};
  const views = createWorkspaceViews(doc, location, async () => response({error:{code:"DATA_UNAVAILABLE"}}, 503));
  await views.setWorkspace({meta, fixture:false, incidents:[], disputes:[]});
  assert.match(doc.getElementById("view-body").innerHTML, /No PayPal balance has been read yet/);
  location.hash = "#audit";
  await views.render();
  assert.match(doc.getElementById("view-body").innerHTML, /could not be loaded/);
});

test("the decision view opens the proposal waiting for the owner when none is chosen", async () => {
  const doc = documentDouble(), location = {hash:"#decision"};
  const executed = proposals.find(p => p.state === "EXECUTED");
  const views = createWorkspaceViews(doc, location, async url => response(url.endsWith("/proposals") ? {items:[executed, owner], next_cursor:null} : url.endsWith(owner.id) ? owner : executed));
  await views.setWorkspace({meta, fixture:false, incidents:[], disputes:[]});
  assert.ok(doc.getElementById("view-body").innerHTML.includes(owner.approval_request.payload_hash));
});
