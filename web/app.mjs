import {filterIncidents, summary, atRisk, label, isStaged, modeLabel, loadWorkspace, bannerText} from "./data.mjs";
import {createWorkspaceViews} from "./workspace.mjs";
import {createDemoBar} from "./demo.mjs";
const $ = id => document.getElementById(id);
const views = createWorkspaceViews(document, window.location);
const utc = value => new Date(value).toLocaleString("en-GB", {day:"2-digit",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit",timeZone:"UTC"});
let demoState = null;
function showClock() {
  // The staged week runs on the mock's clock; show it while the demo bar is present.
  const staged = workspace?.meta.mode === "demo";
  $("clock-label").textContent = staged ? "Staged clock · UTC" : "Workspace clock · UTC";
  const value = workspace?.meta.clock;
  if (value) $("clock").textContent = utc(value);
}
function showBanner() {
  // The persistent label comes only from the server's simulation_label.
  const text = bannerText(workspace?.meta, demoState);
  $("simulation").hidden = !text;
  $("simulation").textContent = text;
}
const demo = createDemoBar(document, {
  onState: state => {demoState = state; showClock(); showBanner();},
  onChange: () => refresh(),
});
window.addEventListener("hashchange", () => views.render());
let workspace, api;
function badge(text, kind = "") {
  const span = document.createElement("span"); span.className = "badge " + kind; span.textContent = text; return span;
}
function inspect(row) {
  $("open-decision").hidden = !row.latest_proposal;
  $("open-decision").href = row.latest_proposal ? "#decision/" + row.latest_proposal.id : "#decision";
  $("detail-title").textContent = row.title;
  $("detail-text").textContent = row.latest_proposal ? label(row.latest_proposal.action) + " · " + label(row.latest_proposal.state) + ". Payments and refunds require owner approval." : "Investigation is in progress.";
  const verdict = $("detail-verdict"); verdict.hidden = !row.latest_proposal;
  verdict.textContent = row.latest_proposal?.verdict || "";
  verdict.className = "badge " + (row.latest_proposal?.verdict === "BLOCK" ? "block" : row.latest_proposal?.verdict === "ALLOW" ? "allow" : "");
}
function update() {
  if (!workspace) return;
  const rows = filterIncidents(workspace.incidents, {state: $("state").value, kind: $("kind").value, query: $("search").value});
  api.setGridOption("rowData", rows);
  $("grid").style.height = Math.min(420, Math.max(160, 60 + rows.length * 69)) + "px";
  $("result-count").textContent = rows.length;
  $("table-note").textContent = rows.length + " of " + workspace.incidents.length + " incidents";
  if (rows.length) inspect(rows[0]);
  else {$("detail-title").textContent = workspace.incidents.length ? "No incidents match these filters" : "Your incident desk is clear"; $("detail-text").textContent = workspace.incidents.length ? "Clear a filter or try another search." : workspace.meta.mode === "demo" ? "Run the first staged step above to begin the mock week." : "New incidents appear after confirmed PayPal observations."; $("detail-verdict").hidden = true; $("open-decision").hidden = true;}
}
function setupGrid() {
  api = agGrid.createGrid($("grid"), {
    overlayNoRowsTemplate: '<span class="grid-empty">No incidents to show. Run a staged step or clear your filters.</span>',
    rowData: [], rowHeight: 69, headerHeight: 40, animateRows: false,
    rowSelection: {mode: "singleRow", checkboxes: false, enableClickSelection: true}, suppressCellFocus: false,
    defaultColDef: {sortable: true, resizable: true},
    getRowId: p => p.data.id,
    columnDefs: [
      {headerName: "Incident", field: "title", minWidth: 300, flex: 2, cellRenderer: p => {
        const div = document.createElement("div"); div.className = "incident-cell";
        const title = document.createElement("span"); title.className = "incident-title"; title.textContent = p.value;
        const ref = document.createElement("span"); ref.className = "incident-ref"; ref.textContent = label(p.data.kind);
        if (isStaged(p.data, workspace?.disputes || [])) {const staged = document.createElement("span"); staged.className = "staged"; staged.textContent = "staged"; ref.append(staged);}
        div.append(title, ref); return div;
      }},
      {headerName: "State", field: "state", width: 158, cellRenderer: p => badge(label(p.value), p.value === "WAITING_EXTERNAL" ? "wait" : "")},
      {headerName: "At risk", field: "amount_at_risk.minor", width: 113, type: "numericColumn", valueFormatter: p => atRisk(p.data.amount_at_risk)},
      {headerName: "Latest check", field: "latest_proposal.verdict", width: 135, cellRenderer: p => p.value ? badge(p.value, p.value === "ALLOW" ? "allow" : p.value === "BLOCK" ? "block" : "") : "—"},
      {headerName: "Response due", field: "deadline", width: 140, valueFormatter: p => p.value ? new Date(p.value).toLocaleDateString("en-GB", {day:"2-digit",month:"short",timeZone:"UTC"}) : "—"},
    ],
    onRowClicked: event => inspect(event.data),
    onCellKeyDown: event => {if (event.event.key === "Enter") inspect(event.data);},
  });
}
async function refresh() {
  $("refresh").disabled = true;
  $("error").hidden = true;
  try {
    workspace = await loadWorkspace();
    $("mode").textContent = modeLabel(workspace.meta);
    showBanner();
    showClock();
    $("source").textContent = workspace.fixture ? "Contract fixtures · preview" : "Connected to dashboard API";
    $("view-source-note").textContent = workspace.fixture ? "Contract fixture preview. Owner decisions require the core approval service." : "Records from the dashboard API. Payments and refunds still need an exact owner approval.";
    $("amount-note").textContent = workspace.meta.simulated ? "Amounts in USD · " + workspace.meta.simulation_label : "Amounts in USD";
    $("contract").textContent = "Contract v" + workspace.meta.contract_version;
    const stats = summary(workspace.incidents);
    $("stat-open").textContent = stats.open; $("stat-owner").textContent = stats.owner;
    $("nav-count").textContent = workspace.incidents.length;
    if (!api) setupGrid();
    update();
    await views.setWorkspace(workspace);
  } catch (error) {$("error").textContent = "The incident source could not be loaded. Try Refresh."; $("error").hidden = false; console.error(error);}
  finally {$("refresh").disabled = false;}
}
for (const id of ["state", "kind"]) $(id).addEventListener("change", update);
$("search").addEventListener("input", update);
$("refresh").addEventListener("click", refresh);
$("clear").addEventListener("click", () => {for (const id of ["state","kind","search"]) $(id).value = ""; update();});
refresh();
demo.load();
