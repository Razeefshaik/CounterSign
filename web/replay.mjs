import {esc, status} from "./views.mjs";
import {label, money} from "./data.mjs";

export const scenarios = {
  F01_timeout_after_payout_created: "F01 · Lost payout response",
  F_retry_settled_obligation: "Settled obligation · Retry request",
};
const systems = new Set(["baseline_llm", "rules_only", "countersign"]);
export const sampleKey = row => JSON.stringify([row.run_id, row.scenario_id, row.k, row.seed]);
const evidenceKey = row => JSON.stringify([sampleKey(row), row.system]);

export function parseEvidence(text) {
  const rows = JSON.parse(text), seen = new Set();
  if (!Array.isArray(rows)) throw Error("Invalid evidence");
  for (const row of rows) {
    if (row.ground !== "mock" || !systems.has(row.system) || typeof row.run_id !== "string" ||
        typeof row.scenario_id !== "string" || !Number.isSafeInteger(row.k) || !Number.isSafeInteger(row.seed) ||
        !Array.isArray(row.actions) || !Array.isArray(row.payout_items) ||
        row.actions.some(action => typeof action.action !== "string" || !Number.isFinite(Date.parse(action.at)) ||
          !["ok","blocked","failed","unknown"].includes(action.outcome) || typeof action.approved_by_owner !== "boolean") ||
        row.payout_items.some(item => typeof item.id !== "string" || typeof item.status !== "string" ||
          typeof item.money_left_account !== "boolean" || typeof item.amount?.value !== "string" ||
          !/^\d+\.\d{2}$/.test(item.amount.value) || item.amount.currency !== "USD") ||
        seen.has(evidenceKey(row))) throw Error("Invalid evidence");
    seen.add(evidenceKey(row));
  }
  return rows;
}

export function parseRuns(text) {
  const rows = text.split(/\r?\n/).filter(line => line.trim()).map(line => JSON.parse(line));
  if (!rows.length) throw new Error("No runs");
  const seen = new Set();
  for (const row of rows) {
    if (row.ground !== "mock") throw new Error("Replay accepts mock runs only");
    if (!systems.has(row.system)) continue;
    if (typeof row.run_id !== "string" || typeof row.scenario_id !== "string" ||
        !Number.isSafeInteger(row.k) || !Number.isSafeInteger(row.seed) ||
        typeof row.passed !== "boolean" || typeof row.task_completed !== "boolean" ||
        !row.invariants || Object.values(row.invariants).some(n => !Number.isSafeInteger(n) || n < 0)) throw new Error("Invalid run row");
    const key = evidenceKey(row);
    if (seen.has(key)) throw new Error("Duplicate run row");
    seen.add(key);
  }
  return rows.filter(row => systems.has(row.system) && Object.hasOwn(scenarios, row.scenario_id));
}

export function pairRuns(rows, scenario, baseline, key = "") {
  const matching = rows.filter(row => row.scenario_id === scenario && [baseline, "countersign"].includes(row.system));
  const keys = [...new Set(matching.map(sampleKey))];
  const chosen = keys.includes(key) ? key : keys[0];
  return {keys, key:chosen, left:matching.find(row => row.system === baseline && sampleKey(row) === chosen),
    right:matching.find(row => row.system === "countersign" && sampleKey(row) === chosen)};
}

export function findEvidence(row, evidence) {
  return row ? evidence.find(item => item.ground === "mock" && evidenceKey(item) === evidenceKey(row)) : null;
}
export function replayEvents(left, right, evidence) {
  return [left, right].flatMap((row, side) => (findEvidence(row, evidence)?.actions || []).map((action, index) => ({...action, side, index})))
    .filter(action => Number.isFinite(Date.parse(action.at)))
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at) || a.side - b.side || a.index - b.index);
}
function payoutAmount(value) {
  if (typeof value?.value !== "string" || !/^\d+\.\d{2}$/.test(value.value)) return "Unavailable";
  const [whole, cents] = value.value.split(".");
  const minor = Number(whole) * 100 + Number(cents);
  return Number.isSafeInteger(minor) ? esc(money({minor, currency:value.currency})) : "Unavailable";
}
function lane(row, evidence, events, side, position, baseline) {
  const title = side ? "CounterSign" : baseline === "rules_only" ? "Rules-only comparison" : "Baseline LLM";
  if (!row) return `<section class="view-card replay-lane"><h2>${title}</h2><p class="empty">No matching mock run in this sample. Load a completed runs.jsonl comparison.</p></section>`;
  const proof = findEvidence(row, evidence);
  const actions = events.slice(0, position).filter(action => action.side === side);
  const violations = Object.values(row.invariants).reduce((sum, n) => sum + n, 0);
  return `<section class="view-card replay-lane"><h2>${title}</h2><p>Model: <strong>${esc(row.model ?? "Unavailable")}</strong> · ${esc(row.provenance)}</p>
    <div class="replay-result">${status(row.passed ? "PASS" : "FAILED")}<span>${violations} invariant violations · Task ${row.task_completed ? "completed" : "incomplete"}</span></div>
    <h3>Recorded actions</h3>${!proof ? '<p class="empty">Action trace unavailable. runs.jsonl contains summary metrics only; load the matching evidence.json to replay actions.</p>' : !actions.length ? '<p class="empty">No actions at this replay position.</p>' : `<ol class="replay-actions">${actions.map(action => `<li><time>${esc(action.at)}</time><strong>${esc(label(action.action))}</strong>${status(action.outcome)}<span>${action.approved_by_owner === true ? "Owner approved" : "No owner approval recorded"}</span></li>`).join("")}</ol>`}
    <h3>Final mock ground-truth payouts</h3><p class="muted">Final observation; these payouts do not change as you step through the action trace.</p>
    ${!proof || !Array.isArray(proof.payout_items) ? '<p class="empty">Ground-truth payouts unavailable in this results file.</p>' : !proof.payout_items.length ? '<p class="empty">No payout items in the final mock ground truth.</p>' : `<ul class="replay-payouts">${proof.payout_items.map(item => `<li><strong>${payoutAmount(item.amount)}</strong>${status(item.status)}<span>${esc(item.id)}</span><span>${item.money_left_account === true ? "Money left the mock account" : "No money outstanding"}</span></li>`).join("")}</ul>`}
    <details><summary>Named invariants</summary><dl>${Object.entries(row.invariants).map(([name,n]) => `<dt>${esc(label(name))}</dt><dd>${n}</dd>`).join("")}</dl></details>
    ${row.error ? `<p class="error">Run error: ${esc(row.error)}</p>` : ""}</section>`;
}

export function replayView({rows = [], evidence = [], scenario = Object.keys(scenarios)[0], baseline = "rules_only", key = "", position = 0} = {}) {
  const pair = pairRuns(rows, scenario, baseline, key), events = replayEvents(pair.left, pair.right, evidence);
  const step = Math.min(position, events.length);
  return `<p class="replay-disclosure">Mock evaluation replay · ${baseline === "rules_only" ? "Rules-only workflow, no LLM. The bundled runs are model-disabled checks; they do not establish real-model performance." : "Baseline LLM runs only against the mock. Its recorded outcome is shown without assuming a failure."}</p>
    <div class="replay-controls"><label>Scenario<select id="replay-scenario">${Object.entries(scenarios).map(([id,title]) => `<option value="${id}" ${id === scenario ? "selected" : ""}>${title}</option>`).join("")}</select></label>
    <label>Comparison<select id="replay-baseline"><option value="rules_only" ${baseline === "rules_only" ? "selected" : ""}>Rules-only · no model</option><option value="baseline_llm" ${baseline === "baseline_llm" ? "selected" : ""}>Baseline LLM</option></select></label>
    <label>Matched sample<select id="replay-sample" ${pair.keys.length ? "" : "disabled"}>${pair.keys.map(k => {const [run,,repeat,seed] = JSON.parse(k); return `<option value="${esc(k)}" ${k === pair.key ? "selected" : ""}>${esc(run)} · k=${repeat} · seed ${seed}</option>`;}).join("")}</select></label></div>
    <div class="replay-import"><label>Load runs.jsonl<input type="file" id="replay-file" accept=".jsonl,.json,application/json"></label><label>Load matching evidence.json (optional)<input type="file" id="replay-evidence" accept=".json,application/json"></label></div>
    <p class="muted">Files stay in this browser. Samples match by run, scenario, repetition and seed. Sandbox results are refused.</p>
    <div class="replay-transport" aria-label="Replay controls"><button type="button" class="secondary" id="replay-start" ${step ? "" : "disabled"}>Start</button><button type="button" class="secondary" id="replay-back" ${step ? "" : "disabled"}>Previous action</button><button type="button" class="primary" id="replay-next" ${step < events.length ? "" : "disabled"}>Next action</button><button type="button" class="secondary" id="replay-end" ${step < events.length ? "" : "disabled"}>Show all actions</button><output aria-live="polite">${step} of ${events.length} recorded actions</output></div>
    <div class="replay-lanes">${lane(pair.left,evidence,events,0,step,baseline)}${lane(pair.right,evidence,events,1,step,baseline)}</div>`;
}

export function createReplay(document, fetcher = fetch) {
  let rows = [], evidence = [], loaded = false, scenario = Object.keys(scenarios)[0], baseline = "rules_only", key = "", position = 0;
  const $ = id => document.getElementById(id);
  async function load() {
    if (loaded) return;
    const result = await fetcher("replays/runs.jsonl", {cache:"no-store"});
    if (!result.ok) throw new Error("Replay runs unavailable");
    rows = parseRuns(await result.text());
    try {const result = await fetcher("replays/evidence.json", {cache:"no-store"}); if (result.ok) evidence = parseEvidence(await result.text());} catch {evidence = [];}
    if (!Array.isArray(evidence)) evidence = [];
    loaded = true;
  }
  function draw() {
    $("view-body").innerHTML = replayView({rows,evidence,scenario,baseline,key,position});
    const bind = (id, handler) => {$(id).onclick = handler;};
    for (const [id, update] of [["replay-scenario", value => {scenario=value;}], ["replay-baseline", value => {baseline=value;}], ["replay-sample", value => {key=value;}]]) $(id).onchange = event => {update(event.target.value); position=0; draw();};
    bind("replay-start", () => {position=0; draw();});
    bind("replay-back", () => {position=Math.max(0,position-1); draw();});
    bind("replay-next", () => {position++; draw();});
    bind("replay-end", () => {const p=pairRuns(rows,scenario,baseline,key); position=replayEvents(p.left,p.right,evidence).length; draw();});
    for (const [id, parse] of [["replay-file", text => {rows=parseRuns(text); key="";}], ["replay-evidence", text => {evidence=parseEvidence(text);}]]) $(id).onchange = async event => {
      const file=event.target.files?.[0]; if (!file) return;
      try {if (file.size > 10_000_000) throw Error(); parse(await file.text()); position=0; draw();}
      catch {$("view-notice").textContent="Could not load this mock replay file. Check its format and size (maximum 10 MB)."; $("view-notice").hidden=false;}
    };
  }
  return {load, draw};
}
