import {filterIncidents, summary, money, label, isStaged, modeLabel, loadWorkspace} from "./data.mjs";
const $ = id => document.getElementById(id);
let workspace, api;
function badge(text, kind = "") {
  const span = document.createElement("span"); span.className = "badge " + kind; span.textContent = text; return span;
}
function inspect(row) {
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
  $("result-count").textContent = rows.length;
  $("table-note").textContent = rows.length + " of " + workspace.incidents.length + " incidents";
  if (rows.length) inspect(rows[0]);
  else {$("detail-title").textContent = "No incidents match these filters"; $("detail-text").textContent = "Clear a filter or try another search."; $("detail-verdict").hidden = true;}
}
function setupGrid() {
  api = agGrid.createGrid($("grid"), {
    rowData: [], rowHeight: 69, headerHeight: 40, animateRows: false,
    rowSelection: "single", suppressCellFocus: false,
    defaultColDef: {sortable: true, resizable: true},
    getRowId: p => p.data.id,
    columnDefs: [
      {headerName: "INCIDENT", field: "title", minWidth: 300, flex: 2, cellRenderer: p => {
        const div = document.createElement("div"); div.className = "incident-cell";
        const title = document.createElement("span"); title.className = "incident-title"; title.textContent = p.value;
        const ref = document.createElement("span"); ref.className = "incident-ref"; ref.textContent = label(p.data.kind);
        if (isStaged(p.data, workspace?.disputes || [])) {const staged = document.createElement("span"); staged.className = "staged"; staged.textContent = "staged"; ref.append(staged);}
        div.append(title, ref); return div;
      }},
      {headerName: "STATE", field: "state", width: 158, cellRenderer: p => badge(label(p.value), p.value === "WAITING_EXTERNAL" ? "wait" : "")},
      {headerName: "AT RISK", field: "amount_at_risk.minor", width: 113, type: "numericColumn", valueFormatter: p => money(p.data.amount_at_risk)},
      {headerName: "LATEST CHECK", field: "latest_proposal.verdict", width: 135, cellRenderer: p => p.value ? badge(p.value, p.value === "ALLOW" ? "allow" : p.value === "BLOCK" ? "block" : "") : "—"},
      {headerName: "RESPONSE DUE", field: "deadline", width: 140, valueFormatter: p => p.value ? new Date(p.value).toLocaleDateString("en-GB", {day:"2-digit",month:"short",timeZone:"UTC"}) : "—"},
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
    $("simulation").hidden = !workspace.meta.simulated;
    $("simulation").textContent = workspace.meta.simulation_label || "";
    $("clock").textContent = new Date(workspace.meta.clock).toLocaleString("en-GB", {day:"2-digit",month:"short",year:"numeric",hour:"2-digit",minute:"2-digit",timeZone:"UTC"});
    $("source").textContent = workspace.fixture ? "Contract fixtures · preview" : "Connected to dashboard API";
    $("contract").textContent = "Contract v" + workspace.meta.contract_version;
    const stats = summary(workspace.incidents);
    $("stat-open").textContent = stats.open; $("stat-owner").textContent = stats.owner;
    $("nav-count").textContent = workspace.incidents.length;
    if (!api) setupGrid();
    update();
  } catch (error) {$("error").textContent = "The incident source could not be loaded. Try Refresh."; $("error").hidden = false; console.error(error);}
  finally {$("refresh").disabled = false;}
}
for (const id of ["state", "kind"]) $(id).addEventListener("change", update);
$("search").addEventListener("input", update);
$("refresh").addEventListener("click", refresh);
$("clear").addEventListener("click", () => {for (const id of ["state","kind","search"]) $(id).value = ""; update();});
refresh();
