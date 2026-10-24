import {createClient, submitDecision} from "./api.mjs";
import {decisionView, runView, auditView, cashView, graphView, esc, canDecide} from "./views.mjs";
import {label} from "./data.mjs";

const titles = {incidents: "Incident desk", decision: "Decision", runs: "Payout runs", audit: "Execution history", cash: "Cash", graph: "Money graph"};
export function route(hash) {
  const [name, id] = hash.replace(/^#/, "").split("/");
  return {name: Object.hasOwn(titles, name) ? name : "incidents", id: id || ""};
}

export function createWorkspaceViews(document, location, fetcher = fetch) {
  const $ = id => document.getElementById(id);
  let workspace, client, serial = 0, proposal = null, busy = false;
  const notice = (message = "") => {$("view-notice").textContent = message; $("view-notice").hidden = !message;};
  const invalidate = () => {
    proposal = null;
    for (const id of ["approve", "reject"]) if ($(id)) $(id).disabled = true;
  };
  async function render() {
    if (!workspace) return;
    const ticket = ++serial, current = route(location.hash);
    invalidate(); notice();
    $("breadcrumb").textContent = titles[current.name];
    document.title = `CounterSign · ${titles[current.name]}`;
    for (const anchor of document.querySelectorAll("[data-view]")) {
      const active = anchor.dataset.view === current.name;
      anchor.classList.toggle("nav-current", active);
      if (active) anchor.setAttribute("aria-current", "page"); else anchor.removeAttribute("aria-current");
    }
    for (const id of ["intro", "incident-stats", "incidents", "incident-detail"]) $(id).hidden = current.name !== "incidents";
    $("workspace-view").hidden = current.name === "incidents";
    if (current.name === "incidents") return;
    $("view-title").textContent = titles[current.name];
    $("view-picker").hidden = true;
    $("view-body").innerHTML = '<p class="empty">Loading records…</p>';
    try {
      let html;
      if (current.name === "decision" || current.name === "runs") {
        const resource = current.name === "decision" ? "proposals" : "payout-runs";
        const items = await client.list(resource);
        if (ticket !== serial) return;
        const id = current.id || items[0]?.id;
        $("view-picker").hidden = !items.length;
        $("record-picker").innerHTML = items.map(item => `<option value="${esc(item.id)}" ${item.id === id ? "selected" : ""}>${esc(current.name === "decision" ? `${label(item.action)} · ${label(item.state)}` : `Week of ${item.week_start} · ${label(item.state)}`)}</option>`).join("");
        $("record-picker").onchange = event => {location.hash = `${current.name}/${event.target.value}`;};
        const doc = id ? await client.get(resource, id) : null;
        if (ticket !== serial) return;
        if (current.name === "decision") {
          proposal = doc;
          const incident = workspace.incidents.find(i => i.id === doc?.incident_id);
          const staged = workspace.disputes.some(d => d.id === incident?.subject.dispute_id && d.staged);
          html = decisionView(doc, workspace.meta, staged);
        } else html = runView(doc);
      } else if (current.name === "audit") html = auditView(await client.list("audit-log"));
      else if (current.name === "cash") html = cashView(await client.singleton("cash"));
      else {
        const disputes = [...workspace.disputes].sort((a, b) => a.paypal_dispute_id.localeCompare(b.paypal_dispute_id));
        const id = current.id || disputes[0]?.id;
        $("view-picker").hidden = !disputes.length;
        $("record-picker").innerHTML = disputes.map(d => `<option value="${esc(d.id)}" ${d.id === id ? "selected" : ""}>${esc(d.paypal_dispute_id)}${d.staged ? " · staged" : ""}</option>`).join("");
        $("record-picker").onchange = event => {location.hash = `graph/${event.target.value}`;};
        html = id ? graphView(await client.singleton("money-graph", {dispute_id: id})) : '<p class="empty">No disputes to explore yet.</p>';
      }
      if (ticket !== serial) return;
      $("view-body").innerHTML = html;
      if (current.name === "decision") bindDecision();
      return true;
    } catch {
      if (ticket !== serial) return;
      invalidate();
      $("view-body").innerHTML = '<p class="empty">These records could not be loaded. Use Refresh to try again.</p>';
      return false;
    }
  }
  function bindDecision() {
    for (const decision of ["approve", "reject"]) if ($(decision)) $(decision).onclick = async () => {
      if (busy || !canDecide(proposal, workspace.meta.clock)) return;
      const selected = proposal, token = $("owner-token")?.value || "", reason = $("reject-reason").value;
      if (workspace.meta.mode === "live" && !token) {notice("Enter your owner token for this request."); return;}
      if (decision === "reject" && !reason.trim()) {notice("Enter a reason for rejection."); return;}
      busy = true;
      const ticket = serial;
      for (const id of ["approve", "reject"]) $(id).disabled = true;
      if ($("owner-token")) $("owner-token").value = "";
      try {
        const result = await submitDecision(client, selected, decision, {reason, token},
          () => {if (ticket === serial) invalidate();},
          async () => {if (ticket === serial && !await render()) throw new Error("Reload failed");});
        if (route(location.hash).name === "decision" && (!route(location.hash).id || route(location.hash).id === selected.id)) {
          notice(result.stale ? "The proposal changed or expired. It has been reloaded; review it before deciding again." : "Your decision was recorded. The latest proposal is shown.");
        }
      } catch (error) {
        if (ticket === serial) {
          // An ambiguous response must not leave a stale approval button active.
          invalidate();
          notice([404, 405, 501].includes(error.status)
            ? "Owner decisions are unavailable in this preview. Connect the core approval service, then Refresh."
            : "The decision could not be confirmed. Refresh the proposal before trying again.");
        }
      } finally {busy = false;}
    };
  }
  $("view-refresh").onclick = () => render();
  return {render, async setWorkspace(value) {workspace = value; client = createClient({fixture: value.fixture, fetcher}); await render();}};
}
