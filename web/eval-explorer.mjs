// C58: historical, model-disabled mock analysis. No operational API or write path.
import {esc} from "./views.mjs";
import {loadVendor} from "./grid.mjs";
import {datasetPickerHTML, mountDatasetPicker} from "./gemini-week.mjs"; // C67

export const SOURCE = "eval/results/2026-10-27-model-none-c31/runs.jsonl";
export const README = "https://github.com/Razeefshaik/CounterSign/blob/main/eval/results/2026-10-27-model-none-c31/README.md";
export const INVARIANTS = {
  duplicate_settlement:"Duplicate settlement",
  payment_on_disputed_order_without_approval:"Paid on disputed order without approval",
  refund_without_owner_approval:"Refund without owner approval",
  payment_to_unverified_payee:"Paid unverified payee",
};
export const systemName = value => ({countersign:"CounterSign", rules_only:"Rules-only"}[value] || value);
export const rowId = row => JSON.stringify([row.system, row.scenario_id, row.k]);

export function summary(rows) {
  const systems = [...new Set(rows.map(r => r.system))].sort().map(system => {
    const selected = rows.filter(r => r.system === system), groups = new Map();
    for (const row of selected) {
      if (!groups.has(row.scenario_id)) groups.set(row.scenario_id, []);
      groups.get(row.scenario_id).push(row);
    }
    return {system, runs:selected.length, passing:selected.filter(r => r.passed).length,
      scenarios:groups.size, consistent:[...groups.values()].filter(group => group.length === 5 &&
        new Set(group.map(r => r.k)).size === 5 && group.every(r => r.passed)).length};
  });
  return {runs:rows.length, systems, invariants:Object.fromEntries(Object.keys(INVARIANTS)
    .map(key => [key, rows.reduce((sum, row) => sum + row.invariants[key], 0)]))};
}

export function comparisonScenarios(rows) {
  const groups = new Map();
  for (const row of rows) {
    if (!groups.has(row.scenario_id)) groups.set(row.scenario_id, []);
    groups.get(row.scenario_id).push(row);
  }
  return new Set([...groups].filter(([, group]) => {
    const cs = group.filter(r => r.system === "countersign"), rules = group.filter(r => r.system === "rules_only");
    return cs.length > 0 && rules.length > 0 && cs.every(r => r.passed) && rules.every(r => !r.passed);
  }).map(([scenario]) => scenario));
}

export function matches(row, filters = {}, comparison = new Set()) {
  return (!filters.system || row.system === filters.system) && (!filters.failing || !row.passed) &&
    (filters.preset !== "comparison" || comparison.has(row.scenario_id)) &&
    (filters.preset !== "invariants" || Object.keys(INVARIANTS).some(key => row.invariants[key] > 0));
}
export function preset(name) {
  if (name === "failed") return {system:"countersign", failing:true, preset:"failed"};
  if (["comparison", "invariants"].includes(name)) return {system:"", failing:false, preset:name};
  return {system:"", failing:false, preset:"all"};
}
export function filteredRows(rows, filters) {
  const comparison = comparisonScenarios(rows);
  return rows.filter(row => matches(row, filters, comparison));
}
export const emptyMessage = filters => filters.preset === "invariants"
  ? "No run violated an invariant" : "No matching runs. Clear filters to see all 480 runs.";

export function parseBundle(value) {
  if (value?.source !== SOURCE || JSON.stringify(value.labels) !== JSON.stringify([
    "mock", "model disabled", "not a real-model or sandbox run"
  ]) || !Array.isArray(value.runs) || value.runs.length !== 480) throw Error("Invalid historical bundle");
  const seen = new Set();
  for (const r of value.runs) {
    if (!["countersign", "rules_only"].includes(r.system) || r.ground !== "mock" || r.model !== "none" ||
        !Number.isSafeInteger(r.k) || r.k < 0 || r.k > 4 ||
        ["scenario_id", "fault_class", "provenance"].some(k => typeof r[k] !== "string") ||
        ["passed", "correct_final_action", "task_completed"].some(k => typeof r[k] !== "boolean") ||
        Object.keys(INVARIANTS).some(k => !Number.isSafeInteger(r.invariants?.[k]) || r.invariants[k] < 0) ||
        ["paid", "due"].some(k => !Number.isSafeInteger(r.makers_paid_on_time?.[k]) || r.makers_paid_on_time[k] < 0) ||
        !Number.isSafeInteger(r.owner_escalations) || !Number.isSafeInteger(r.duration_ms) ||
        seen.has(rowId(r))) throw Error("Invalid historical row");
    seen.add(rowId(r));
  }
  return value.runs;
}

const text = p => esc(p.valueFormatted ?? p.value ?? "");
const bool = p => p.node?.rowPinned ? text(p) : p.value === true ? "Yes" : "No";
export function columns() {
  const col = (field, headerName, extra = {}) => ({field, headerName, minWidth:150,
    filter:"agTextColumnFilter", cellRenderer:text, ...extra});
  return [
    col("system", "System", {pinned:"left", valueFormatter:p => systemName(p.value)}),
    col("scenario_id", "Scenario", {minWidth:300, tooltipField:"scenario_id"}),
    col("fault_class", "Fault class", {minWidth:220}),
    col("k", "Run", {minWidth:85, width:85, filter:"agNumberColumnFilter"}),
    col("passed", "Passed", {cellRenderer:p => p.node?.rowPinned ? text(p) :
      `<span class="stamp ${p.value ? "good" : "bad"}">${p.value ? "PASS" : "FAIL"}</span>`}),
    col("correct_final_action", "Final action correct", {cellRenderer:bool}),
    col("task_completed", "Task completed", {cellRenderer:bool}),
    ...Object.entries(INVARIANTS).map(([key, name]) => col(key, name, {
      minWidth:205, filter:"agNumberColumnFilter", valueGetter:p => p.data?.invariants?.[key] ?? 0})),
    col("makers_paid", "Makers paid on time", {valueGetter:p => p.data?.makers_paid_on_time
      ? `${p.data.makers_paid_on_time.paid}/${p.data.makers_paid_on_time.due}` : ""}),
    col("owner_escalations", "Owner escalations", {filter:"agNumberColumnFilter"}),
    col("provenance", "Provenance"),
  ];
}
export function totals(rows) {
  const result = summary(rows);
  return {system:"Filtered totals", scenario_id:`${rows.length} runs`,
    passed:`${rows.filter(r => r.passed).length} pass`,
    correct_final_action:rows.filter(r => r.correct_final_action).length,
    task_completed:rows.filter(r => r.task_completed).length,
    invariants:result.invariants,
    makers_paid_on_time:{paid:rows.reduce((n,r) => n + r.makers_paid_on_time.paid, 0),
      due:rows.reduce((n,r) => n + r.makers_paid_on_time.due, 0)},
    owner_escalations:rows.reduce((n,r) => n + r.owner_escalations, 0)};
}

export function summaryHTML(rows) {
  const result = summary(rows);
  return `<div class="c58-summary">${result.systems.map(s => `<div><strong>${esc(systemName(s.system))}</strong>
    <span>${s.passing}/${s.runs} runs passing</span><span>${s.consistent}/${s.scenarios} scenarios at 5/5</span></div>`).join("")}
    ${Object.entries(INVARIANTS).map(([key,label]) => `<div><strong>${result.invariants[key]}</strong><span>${esc(label)}</span></div>`).join("")}</div>`;
}
export function detailHTML(row) {
  const reason = row.passed ? "All graded checks passed." : [
    !row.correct_final_action ? "Final action did not match the expected answer." : "",
    !row.task_completed ? "Expected payouts or refunds were not completed by the deadline." : "",
    Object.values(row.invariants).some(n => n > 0) ? "A named invariant was violated." : "",
  ].filter(Boolean).join(" ");
  return `<h3>${esc(row.scenario_id)}</h3><p>${esc(systemName(row.system))} · Run ${esc(row.k)} · ${row.passed ? "PASS" : "FAIL"}</p>
    <p>${esc(reason)}</p><dl>${Object.entries({"Fault class":row.fault_class,"Provenance":row.provenance,
      "Makers paid on time":`${row.makers_paid_on_time.paid}/${row.makers_paid_on_time.due}`,
      "Owner escalations":row.owner_escalations,"Duration (ms)":row.duration_ms,"Model":row.model,"Ground":row.ground,
      ...Object.fromEntries(Object.entries(INVARIANTS).map(([key,label]) => [label,row.invariants[key]]))})
      .map(([name,value]) => `<dt>${esc(name)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>
    <p>No action trace is bundled for this sample. These metrics cannot identify the specific missing action.</p>`;
}
export function explorerHTML(rows) {
  return `<section class="view-card c58-explorer" aria-labelledby="c58-heading"><header><p class="eyebrow">Historical evaluation · mock · model disabled</p>
    <h2 id="c58-heading">All ${rows.length} runs</h2><p>Slice the published experiment. Not a real-model or sandbox run.</p></header>
    ${summaryHTML(rows)}<p class="c58-note">This run had the model disabled. Scenarios needing an agent-proposed action (evidence, refund, cancel) cannot pass for CounterSign; safety refusals earn no completion credit.
    Other recorded failures remain visible. <a href="${README}" target="_blank" rel="noopener">Read the experiment and its limitations</a>.</p>
    <div class="c58-presets" aria-label="Evaluation presets"><button type="button" data-c58-preset="failed">Where CounterSign failed</button>
    <button type="button" data-c58-preset="comparison">Where rules-only failed but CounterSign passed</button>
    <button type="button" data-c58-preset="invariants">Any invariant above zero</button><button type="button" data-c58-preset="all">Clear filters</button></div>
    <div class="c58-tools"><div class="c58-chips" role="group" aria-label="System">${[["","All"],["countersign","CounterSign"],["rules_only","Rules-only"]]
      .map(([id,label]) => `<button type="button" data-c58-system="${id}" aria-pressed="${!id}">${label}</button>`).join("")}</div>
    <label class="c58-failing"><input type="checkbox" data-c58-failing> Failing only</label>
    <label class="c58-search">Search runs<input type="search" data-c58-search placeholder="Scenario, fault or provenance"></label>
    <button type="button" data-c58-export>Export current view CSV</button></div>
    <p class="c58-count" aria-live="polite"></p><p class="c58-help">Filter a column from its menu. Shift-click column headers for multi-column sorting. Drag headers to move them; drag column edges to resize.</p>
    <div class="c58-layout"><div data-c58-grid><p class="c58-loading">Loading the Community grid…</p></div>
    <aside class="c58-detail" aria-label="Selected run details" tabindex="-1" hidden></aside></div>
    <p class="c58-selection" aria-live="polite"></p></section>`;
}

export function createEvalExplorer(document, fetcher = fetch, onReplay = () => false) {
  let rows = null, pending = null, active = null, filters = preset("all"), search = "";
  async function load() {
    if (!pending) pending = (async () => {
      const response = await fetcher("results/eval-runs.json", {cache:"no-store"});
      if (!response.ok) throw Error("Unavailable");
      rows = parseBundle(await response.json());
    })().catch(error => {pending = null; throw error;});
    await pending;
  }
  async function mount(host) {
    if (!host?.ownerDocument || !host.isConnected) return;
    active?.destroy();
    let api, observer, motion, motionChange, disposed = false;
    const current = {destroy() {disposed = true; observer?.disconnect(); motion?.removeEventListener("change",motionChange); api?.destroy();}};
    active = current;
    const valid = () => !disposed && host.isConnected && active === current &&
      (!document.body?.dataset?.route || document.body.dataset.route === "replay");
    host.innerHTML = '<p class="c58-loading" role="status">Loading all 480 historical runs…</p>';
    try {await load();} catch {
      if (valid()) {host.innerHTML = '<p class="c58-error">The historical runs could not be loaded. <button type="button">Retry</button></p>';
        host.querySelector("button").onclick = () => mount(host);}
      return;
    }
    if (!valid()) return;
    host.innerHTML = datasetPickerHTML() + explorerHTML(rows); // C67
    mountDatasetPicker(host, fetcher); // C67
    const library = await loadVendor(document);
    if (!valid()) return;
    const $ = selector => host.querySelector(selector), $$ = selector => host.querySelectorAll(selector);
    if (!library?.createGrid) {$("[data-c58-grid]").textContent = "Community grid unavailable. Reload to retry; the source README remains available."; return;}
    const viewport = $("[data-c58-grid]"); viewport.innerHTML = "";
    viewport.className = "ag-theme-quartz cs-grid c58-grid";
    viewport.setAttribute("aria-label", "All 480 evaluation runs");
    const comparison = comparisonScenarios(rows);
    motion = document.defaultView.matchMedia("(prefers-reduced-motion: reduce)");
    function updateTotals() {
      if (!api || !valid()) return;
      const visible = []; api.forEachNodeAfterFilterAndSort(node => {if (!node.rowPinned) visible.push(node.data);});
      api.setGridOption("pinnedBottomRowData", [totals(visible)]);
      $(".c58-count").textContent = `${visible.length} of ${rows.length} runs shown${filters.preset === "comparison" ? " · Both systems for scenarios where all CounterSign samples passed and all rules-only samples failed" : ""}.`;
      api.setGridOption("overlayNoRowsTemplate", `<span class="cs-grid-empty">${esc(emptyMessage(filters))}</span>`);
      if (!visible.length) api.showNoRowsOverlay(); else api.hideOverlay();
    }
    const open = row => {
      if (onReplay(row)) return;
      $(".c58-detail").innerHTML = detailHTML(row); $(".c58-detail").hidden = false;
      $(".c58-detail").focus({preventScroll:true});
      $(".c58-detail").scrollIntoView({block:"nearest",behavior:"instant"});
      $(".c58-selection").textContent = "Selected run details. No matching action trace is available in the replay lanes.";
    };
    try {api = library.createGrid(viewport, {columnDefs:columns(), rowData:rows,
      defaultColDef:{sortable:true,resizable:true,suppressMovable:false},
      getRowId:p => rowId(p.data), rowHeight:44, headerHeight:64,
      pagination:true,paginationPageSize:25,paginationPageSizeSelector:false,
      quickFilterText:search, animateRows:!motion.matches, enableCellTextSelection:true,
      ensureDomOrder:true, isExternalFilterPresent:() => true,
      doesExternalFilterPass:node => matches(node.data,filters,comparison),
      onFilterChanged:updateTotals, onSortChanged:updateTotals,
      onRowClicked:e => {if (!e.node.rowPinned) open(e.data);},
      onCellKeyDown:e => {if (e.event.key === "Enter" && !e.node.rowPinned) {e.event.preventDefault(); open(e.data);}},
      defaultCsvExportParams:{fileName:"countersign-historical-mock-runs.csv",skipPinnedBottom:true,
        exportedRows:"filteredAndSorted",processCellCallback:p => {
          const value = p.value ?? "";
          return /^[=+\-@\t\r]/.test(String(value)) ? "'" + value : value;
        }},
    });} catch {
      viewport.textContent = "Community grid could not be initialized. Reload to retry; the source README remains available.";
      return;
    }
    function controls() {
      for (const chip of $$("[data-c58-system]")) chip.setAttribute("aria-pressed",String(chip.dataset.c58System === filters.system));
      $("[data-c58-failing]").checked = filters.failing;
      for (const button of $$("[data-c58-preset]")) button.setAttribute("aria-pressed", String(button.dataset.c58Preset === filters.preset));
      api.onFilterChanged(); api.paginationGoToFirstPage(); updateTotals();
    }
    for (const button of $$("[data-c58-preset]")) button.onclick = () => {
      filters = preset(button.dataset.c58Preset); search = ""; $("[data-c58-search]").value = "";
      api.setGridOption("quickFilterText", ""); api.setFilterModel(null); controls();
    };
    for (const chip of $$("[data-c58-system]")) chip.onclick = () => {filters = {...filters, system:chip.dataset.c58System}; controls();};
    $("[data-c58-failing]").onchange = e => {filters = {...filters,failing:e.target.checked}; controls();};
    $("[data-c58-search]").value = search;
    $("[data-c58-search]").oninput = e => {search=e.target.value; api.setGridOption("quickFilterText", search);};
    $("[data-c58-export]").onclick = () => api.exportDataAsCsv();
    motionChange = () => api.setGridOption("animateRows", !motion.matches);
    motion.addEventListener("change",motionChange);
    const Observer = document.defaultView.MutationObserver;
    if (Observer) {observer = new Observer(() => {if (!host.isConnected || document.body.dataset.route !== "replay") current.destroy();}); observer.observe(document.body,{childList:true,subtree:true,attributes:true,attributeFilter:["data-route"]});}
    controls();
  }
  return {mount};
}
