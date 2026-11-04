// C67: committed model evidence only; no inference or operational requests.
import {esc} from "./views.mjs";
const field = (name, value) => `<div><dt>${esc(name)}</dt><dd>${esc(value == null ? "Not recorded" :
  typeof value === "string" ? value : JSON.stringify(value, null, 2))}</dd></div>`;

export function parseModelEvidence(value) {
  if (value?.schema_version !== 1 || !Array.isArray(value.weeks) ||
      !Array.isArray(value.smokes) || !Array.isArray(value.datasets)) throw Error("Invalid model evidence");
  const ids = new Set();
  for (const week of value.weeks) {
    if (typeof week.id !== "string" || ids.has(week.id) || week.ground !== "mock" ||
        !Array.isArray(week.decisions) || typeof week.model !== "string" ||
        !/^recordings\/real_model\/[A-Za-z0-9/_-]+\/README\.md$/.test(week.source)) throw Error("Invalid recorded week");
    ids.add(week.id);
  }
  return value;
}
export function decisionHTML(row) {
  return `<details class="c67-decision"><summary><strong>${esc(row.action)}${row.step ? ` · ${esc(row.step)}` : ""}</strong>
    <span>${esc(row.verdict ?? "Verdict not recorded")}</span></summary><dl>
    ${field("Recorded at",row.created_at)}${field("What the AI agent read · tools and arguments",row.reads)}
    ${field("Proposal parameters",row.params)}${field("Rationale",row.rationale)}
    ${field("Injection / payee-change flags",row.flags)}${field("Shield verdict in event log",row.verdict)}
    ${field("Shield rule results",row.rule_results)}
    ${row.recorded_outcome ? field("Recorded run README outcome",row.recorded_outcome) : ""}</dl></details>`;
}
export function modelWeekHTML(value) {
  const data = parseModelEvidence(value);
  const stage = data.stage_one;
  return `<div class="c67-model-page"><p class="c67-disclosure">Recorded real AI agent run · mock PayPal.
    The hosted demo uses a separate, labelled scripted AI agent. This panel only replays committed evidence.</p>
    ${stage ? `<section class="view-card c67-smokes"><h2>AI agent checks</h2>
      <p>Read-tool round trip: ${esc(stage.round_trip)}. Canary: ${esc(stage.canary.status)}.</p>
      ${stage.error ? `<p>Stage 1 stopped: ${esc(stage.error)}. No retry or later stage was started.</p>` : ""}
      <p>${esc(stage.turns.length)} application turn attempts. No new week or evaluation has run.</p>
      ${stage.run_details ? `<small class="c67-run-details">Run details: ${esc(stage.run_details)}.
        The CLI adds its own system instructions. <a href="https://github.com/Razeefshaik/CounterSign/blob/main/${esc(stage.source)}">Run record</a></small>` : ""}</section>` : ""}
    ${data.weeks.map(week => `<section class="c67-week view-card" data-week="${esc(week.id)}">
      <header><h3>${esc(week.label)}</h3></header>
      <p class="c67-status">${esc(week.status)}</p><p>${esc(week.note)}</p>
      ${week.decisions.length ? week.decisions.map(decisionHTML).join("") : '<p>No model week decisions were recorded for this attempt.</p>'}
      <small class="c67-run-details">Run details: ${esc(week.provider_label)} · ${esc(week.model)} · ${esc(week.date)}.
        <a href="https://github.com/Razeefshaik/CounterSign/blob/main/${week.source.split("/").map(encodeURIComponent).join("/")}" target="_blank" rel="noopener">Run record</a></small></section>`).join("")}
    <p><a href="#replay">Evaluation replay and separately labelled datasets</a> ·
      <a href="results/real-model-evidence.json" target="_blank" rel="noopener">Inspect the committed data</a></p></div>`;
}
export function createModelWeek(document, fetcher = fetch) {
  let data;
  return {
    async load() {
      if (data) return;
      const response = await fetcher("results/real-model-evidence.json", {cache:"no-store"});
      if (!response.ok) throw Error("Model recordings unavailable");
      data = parseModelEvidence(await response.json());
    },
    draw() {document.getElementById("view-body").innerHTML = modelWeekHTML(data);},
  };
}
export const datasetPickerHTML = () => `<section class="view-card c67-datasets">
  <label>Evaluation dataset<select data-c67-dataset><option value="historical">Historical · mock · model disabled</option>
    <option value="real">Real AI agent, mock PayPal</option></select></label>
  <p><a href="#model-week">Real AI agent week</a></p><div data-c67-results hidden></div></section>`;
export function modelResultsHTML(dataset) {
  if (!dataset || dataset.ground !== "mock" || dataset.id !== "c67-real-model" ||
      dataset.label !== "Real AI agent, mock PayPal" || !Array.isArray(dataset.runs)) throw Error("Invalid dataset");
  if (dataset.status !== "complete") {
    if (dataset.runs.length || dataset.summary?.length) throw Error("Partial dataset has final scores");
    return `<h3>${esc(dataset.label)}</h3><p>${esc(dataset.status)}</p>
      <p>${esc(dataset.reason)}</p><p>No score is available. Historical model-disabled scores are a separate dataset.</p>`;
  }
  return `<h3>${esc(dataset.label)}</h3><p>${esc(dataset.k)} runs per scenario</p>
    <div class="table-scroll"><table><thead><tr><th>System</th><th>Passed</th><th>Runs</th><th>Invariant violations</th></tr></thead><tbody>
    ${dataset.summary.map(row => `<tr><td>${esc(row.system)}</td><td class="num">${esc(dataset.runs.filter(r => r.system === row.system && r.passed).length)}</td>
      <td class="num">${esc(row.runs)}</td><td class="num">${esc(Object.values(row.invariant_violations).reduce((a,b) => a+b,0))}</td></tr>`).join("")}</tbody></table></div>`;
}
export function mountDatasetPicker(host, fetcher = fetch) {
  const picker = host.querySelector("[data-c67-dataset]");
  if (!picker) return;
  let serial = 0;
  picker.onchange = async () => {
    const ticket = ++serial, real = picker.value === "real", target = host.querySelector("[data-c67-results]");
    host.querySelector(".c58-explorer").hidden = real; target.hidden = !real;
    if (!real) return;
    target.textContent = "Loading recorded model results…";
    try {
      const response = await fetcher("results/real-model-evidence.json", {cache:"no-store"});
      if (!response.ok) throw Error();
      const data = parseModelEvidence(await response.json());
      if (ticket === serial) target.innerHTML = modelResultsHTML(data.datasets[0]);
    } catch {if (ticket === serial) target.textContent = "Model results unavailable. No score can be inferred.";}
  };
}
