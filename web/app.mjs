import {filterIncidents, summary, money, isStaged, modeLabel, loadWorkspace, bannerText} from "./data.mjs";
import {createClient} from "./api.mjs";
import {incidentTable, incidentTitle, ownerCallout, esc} from "./views.mjs";
import {action as actionWord, state as stateWord, tone, when} from "./words.mjs";
import {createWorkspaceViews} from "./workspace.mjs";
import {createDemoBar} from "./demo.mjs";
import {createGuide} from "./guide.mjs"; // C57
// C49: loaded on demand when the incident desk is opened.
import {mountGrid, incidentRows} from "./grid.mjs";

const $ = id => document.getElementById(id);
const views = createWorkspaceViews(document, window.location);
let workspace = null, demoState = null, selected = "", firstPaint = true;
// C49: only the visible incident grid owns these resources.
let incidentGrid = null, incidentGridSerial = 0;
async function showIncidentGrid() {
  if (!workspace || location.hash.split("/")[0] !== "#incidents") return;
  const ticket = ++incidentGridSerial, source = workspace;
  const client = createClient({fixture: source.fixture});
  const makers = await client.list("makers").catch(() => []);
  if (ticket !== incidentGridSerial || workspace !== source || location.hash.split("/")[0] !== "#incidents") return;
  const filtered = filterIncidents(source.incidents, {state: $("state").value, kind: $("kind").value});
  const host = $("incident-list"); host.dataset.c49Grid = "incidents";
  // C66: keep the existing grid through demo refreshes; replace only its rows/callbacks.
  const rows=incidentRows(filtered, {makers,disputes:source.disputes,staged:row => isStaged(row,source.disputes)});
  const settings={
    isActive:() => ticket === incidentGridSerial && location.hash.split("/")[0] === "#incidents",
    quickFilterText:$("search").value, onOpen:row => inspect(source.incidents.find(item => item.id === row.id)),
    onFilterChanged:event => {
      const count = event.api.getDisplayedRowCount(); $("result-count").textContent = count; $("table-note").textContent = `${count} of ${source.incidents.length} incidents`;
      let first = null, current = null;
      event.api.forEachNodeAfterFilterAndSort(node => {first ||= node.data; if (node.data.id === selected) current = node.data;});
      const visible = current || first;
      if (visible) inspect(source.incidents.find(row => row.id === visible.id));
      else {$("detail-title").textContent = "No incidents match these filters"; $("detail-text").textContent = "Clear a filter or try another search."; $("detail-verdict").hidden = true; $("open-decision").hidden = true;}
    },
  };
  if (incidentGrid?.api && incidentGrid.update(rows,settings)) return;
  incidentGrid?.destroy();
  const handle=await mountGrid(host,"incidents",rows,settings);
  // C49: navigation can happen while the vendor bundle is still loading.
  if (ticket !== incidentGridSerial || location.hash.split("/")[0] !== "#incidents") handle?.destroy();
  else incidentGrid = handle;
}
window.addEventListener("hashchange", () => {
  if (location.hash.split("/")[0] === "#incidents") showIncidentGrid();
  else {incidentGridSerial++; incidentGrid?.destroy(); incidentGrid = null;}
});

function showClock() {
  // The staged week runs on the mock's clock; show it while the demo bar is present.
  $("clock-label").textContent = workspace?.meta.mode === "demo" ? "Staged clock" : "Workspace clock";
  const value = workspace?.meta.clock;
  if (value) $("clock").textContent = when(value);
}
function showBanner() {
  // The persistent label comes only from the server's simulation_label.
  const text = bannerText(workspace?.meta, demoState);
  $("simulation").hidden = !text;
  $("simulation").textContent = text;
}
// C38: the glass theme is light by default; dark stays a per-browser convenience.
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  $("theme").setAttribute("aria-label", theme === "dark" ? "Switch to light theme" : "Switch to dark theme");
}
let theme = "light";
try {theme = localStorage.getItem("countersign-theme") === "dark" ? "dark" : "light";} catch {}
applyTheme(theme);
$("theme").addEventListener("click", () => {
  theme = theme === "dark" ? "light" : "dark";
  applyTheme(theme);
  try {localStorage.setItem("countersign-theme", theme);} catch {}
});
const demo = createDemoBar(document, {
  onState: (state, notice) => {demoState = state; document.body.classList.toggle("demo-running", Boolean(state?.running)); showClock(); showBanner(); guide.update(state, notice);}, // C57
  onChange: () => refresh(),
});
// C57: shared guarded commands; guidance never sends its own requests.
const guide = createGuide(document, {step:() => demo.step(), reset:() => demo.reset(), friday:() => demo.friday()});
// Every route starts at the top; #incidents also names a section, which would scroll.
window.addEventListener("hashchange", () => {window.scrollTo({top: 0}); views.render();});
if (location.hash === "#incidents") requestAnimationFrame(() => window.scrollTo({top: 0}));

function inspect(row) {
  selected = row.id;
  for (const tr of $("incident-list").querySelectorAll("tr[data-id]")) tr.classList.toggle("selected", tr.dataset.id === row.id);
  const latest = row.latest_proposal;
  $("detail-title").textContent = incidentTitle(row).text;
  $("detail-text").textContent = latest
    ? `Latest proposal: ${actionWord(latest.action)}, ${stateWord(latest.state).toLowerCase()}. Payments and refunds always wait for your approval.`
    : "The agent is still investigating. No proposal yet.";
  const verdict = $("detail-verdict");
  verdict.hidden = !latest?.verdict;
  verdict.className = "pill " + tone(latest?.verdict);
  verdict.textContent = latest?.verdict ? stateWord(latest.verdict) : "";
  verdict.title = latest?.verdict || "";
  $("open-decision").hidden = !latest;
  $("open-decision").href = latest ? "#decision/" + latest.id : "#decision";
}

function update() {
  if (!workspace) return;
  // C49: destroy before replacing the fallback so late grid loads cannot win.
  incidentGridSerial++;
  const keepGrid=Boolean(incidentGrid?.api); // C66
  const rows = filterIncidents(workspace.incidents, {state: $("state").value, kind: $("kind").value, query: $("search").value});
  if (!keepGrid) $("incident-list").innerHTML = incidentTable(rows, {staged: row => isStaged(row, workspace.disputes || []), selected});
  for (const button of $("incident-list").querySelectorAll(".row-title")) {
    button.onclick = () => inspect(workspace.incidents.find(row => row.id === button.dataset.id));
  }
  $("result-count").textContent = rows.length;
  $("table-note").textContent = `${rows.length} of ${workspace.incidents.length} incidents`;
  const current = rows.find(row => row.id === selected) || rows[0];
  if (current) inspect(current);
  else {
    if (!keepGrid) $("incident-list").innerHTML = `<p class="empty" style="padding:24px 20px">${esc(workspace.incidents.length ? "No incidents match these filters. Clear a filter or try another search." : workspace.meta.mode === "demo" ? "The desk is clear. Run the first staged step above to start the week." : "The desk is clear. New incidents appear after confirmed PayPal observations.")}</p>`;
    $("detail-title").textContent = workspace.incidents.length ? "No incidents match these filters" : "Your incident desk is clear";
    $("detail-text").textContent = workspace.incidents.length ? "Clear a filter or try another search." : "Nothing needs attention yet.";
    $("detail-verdict").hidden = true; $("open-decision").hidden = true;
  }
  // C49: untouched HTML remains visible while the vendored grid loads.
  showIncidentGrid();
}

async function overview(client) {
  // Optional context for the summary strip; a failure here never hides the desk.
  const [proposals, cash] = await Promise.all([
    client.list("proposals").catch(() => null),
    client.singleton("cash").catch(() => null),
  ]);
  const stats = summary(workspace.incidents, proposals);
  $("stat-open").textContent = stats.open;
  $("stat-owner").textContent = stats.owner;
  $("stat-owner").classList.toggle("owner-waiting", stats.owner > 0);
  $("nav-owner").hidden = !stats.owner; $("nav-owner").textContent = stats.owner;
  const bell = $("top-owner"); if (bell) {bell.hidden = !stats.owner; bell.textContent = stats.owner;}
  $("stat-held").textContent = cash?.earmarks_not_subtracted?.dispute ? money(cash.earmarks_not_subtracted.dispute) : "—";
  $("stat-cash").textContent = cash?.unreserved ? money(cash.unreserved) : "—";
  const waiting = proposals?.find(p => p.state === "NEEDS_OWNER");
  let run = null;
  if (waiting?.action === "SCHEDULE_PAYOUT_RUN" && waiting.params?.run_id) run = await client.get("payout-runs", waiting.params.run_id).catch(() => null);
  $("owner-callout").innerHTML = waiting ? ownerCallout(waiting, {run}) : "";
  $("owner-callout").hidden = !waiting;
}

async function refresh() {
  $("refresh").disabled = true;
  $("error").hidden = true;
  try {
    workspace = await loadWorkspace();
    const client = createClient({fixture: workspace.fixture});
    $("mode").textContent = modeLabel(workspace.meta);
    showBanner(); showClock();
    $("source").textContent = workspace.fixture ? "Showing contract fixtures (preview)." : "";
    $("source").hidden = !workspace.fixture;
    $("view-source-note").textContent = workspace.fixture ? "Contract fixture preview. Owner decisions require the core approval service." : "Live records from the dashboard API. Payments and refunds still need your exact approval.";
    $("amount-note").textContent = workspace.meta.simulated ? "Amounts in USD · " + workspace.meta.simulation_label : "Amounts in USD";
    $("contract").textContent = "Contract v" + workspace.meta.contract_version;
    $("nav-count").textContent = workspace.incidents.filter(row => row.state !== "RESOLVED").length;
    update();
    if (firstPaint) {for (const id of ["intro", "incident-stats", "incidents"]) $(id).classList.add("enter"); firstPaint = false;}
    await Promise.all([overview(client), views.setWorkspace(workspace)]);
  } catch (error) {
    $("error").textContent = "The incident source could not be loaded. Try Refresh.";
    $("error").hidden = false;
    console.error(error);
  } finally {$("refresh").disabled = false;}
}

for (const button of document.querySelectorAll("#kind-group button")) button.addEventListener("click", () => {
  for (const other of document.querySelectorAll("#kind-group button")) other.setAttribute("aria-pressed", String(other === button));
  $("kind").value = button.dataset.kind; update();
});
$("state").addEventListener("change", update);
// C49: quick search updates the live grid without refetching or losing its sort.
$("search").addEventListener("input", () => incidentGrid?.api ? incidentGrid.setQuickFilter($("search").value) : update());
$("refresh").addEventListener("click", refresh);
$("clear").addEventListener("click", () => {
  $("state").value = ""; $("search").value = ""; $("kind").value = "";
  incidentGrid?.setQuickFilter("");incidentGrid?.api?.setFilterModel(null); // C66
  for (const button of document.querySelectorAll("#kind-group button")) button.setAttribute("aria-pressed", String(!button.dataset.kind));
  update();
});
// The playground exists only in demo mode on the mock; its nav item follows the server.
fetch("/api/v1/playground/attacks", {cache: "no-store"}).then(response => {$("nav-trick").hidden = $("nav-lab").hidden = !response.ok;}).catch(() => {});
refresh();
demo.load();
