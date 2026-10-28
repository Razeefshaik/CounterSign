import {createClient, submitDecision} from "./api.mjs";
import {decisionView, runView, auditView, cashView, graphView, esc, canDecide} from "./views.mjs";
import {isStaged} from "./data.mjs";
import {createReplay} from "./replay.mjs";
import {createTrick} from "./trick.mjs";
import {createCommand} from "./command.mjs";
import {action as actionWord, state as stateWord, when} from "./words.mjs";

const titles = {overview: "Command center", incidents: "Incident desk", decision: "Decision", runs: "Payout runs", audit: "Execution history", cash: "Cash", graph: "Money graph", trick: "Try to trick the agent", replay: "Evaluation replay"};
export function route(hash) {
  const [name, id] = hash.replace(/^#/, "").split("/");
  return {name: Object.hasOwn(titles, name) ? name : "overview", id: id || ""};
}

export function createWorkspaceViews(document, location, fetcher = fetch) {
  const $ = id => document.getElementById(id);
  let workspace, client, serial = 0, proposal = null, busy = false;
  const replay = createReplay(document, fetcher);
  const trick = createTrick(document, fetcher);
  const command = createCommand(document, fetcher);
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
    $("workspace-view").hidden = current.name === "incidents" || current.name === "overview";
    if ($("command")) $("command").hidden = current.name !== "overview";
    if (document.body) document.body.dataset.route = current.name;
    if (current.name === "incidents") return;
    if (current.name === "overview") {
      try {await command.show(workspace, client); return true;}
      catch {if (ticket === serial) $("stage").innerHTML = '<p class="scene-loading">The constellation could not be loaded. Use Refresh.</p>'; return false;}
    }
    $("view-title").textContent = titles[current.name];
    $("view-picker").hidden = true;
    $("view-body").innerHTML = '<p class="empty">Loading records…</p>';
    try {
      if (current.name === "replay" || current.name === "trick") {
        const page = current.name === "replay" ? replay : trick;
        await page.load();
        if (ticket !== serial) return;
        page.draw();
        return true;
      }
      let html;
      if (current.name === "decision" || current.name === "runs") {
        const resource = current.name === "decision" ? "proposals" : "payout-runs";
        const items = await client.list(resource);
        if (ticket !== serial) return;
        // Without a chosen record, open the proposal that is waiting for the owner.
        const waiting = current.name === "decision" ? items.find(item => item.state === "NEEDS_OWNER") : null;
        const id = current.id || (waiting || items[0])?.id;
        $("view-picker").hidden = !items.length;
        $("record-picker").innerHTML = items.map(item => `<option value="${esc(item.id)}" ${item.id === id ? "selected" : ""}>${esc(current.name === "decision" ? `${actionWord(item.action)} · ${stateWord(item.state)}` : `Week of ${when(item.week_start + "T00:00:00Z").replace(/, 00:00 UTC$/, "")} · ${stateWord(item.state)}`)}</option>`).join("");
        $("record-picker").onchange = event => {location.hash = `${current.name}/${event.target.value}`;};
        const doc = id ? await client.get(resource, id) : null;
        if (ticket !== serial) return;
        if (current.name === "decision") {
          workspace.meta = await client.singleton("meta");
          if (ticket !== serial) return;
          proposal = doc;
          const incident = workspace.incidents.find(i => i.id === doc?.incident_id);
          const staged = Boolean(incident) && isStaged(incident, workspace.disputes);
          // A payout run's sentence names makers and held-back shares from the run itself.
          let run = null;
          if (doc?.action === "SCHEDULE_PAYOUT_RUN" && doc.params?.run_id) {
            try {run = await client.get("payout-runs", doc.params.run_id);} catch {run = null;}
            if (ticket !== serial) return;
          }
          html = decisionView(doc, workspace.meta, staged, {run});
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
      for (const button of document.querySelectorAll("[data-copy]")) button.onclick = async () => {
        try {await navigator.clipboard.writeText(button.dataset.copy); notice("Payload hash copied.");} catch {notice("Copy is unavailable in this browser.");}
      };
      return true;
    } catch (error) {
      if (ticket !== serial) return;
      invalidate();
      // A new marketplace has no balance snapshot yet; cash is unavailable, never zero.
      $("view-body").innerHTML = current.name === "cash" && error?.status === 503
        ? '<p class="empty">No PayPal balance has been read yet, so cash is unavailable. It appears after the first balance check.</p>'
        : '<p class="empty">These records could not be loaded. Use Refresh to try again.</p>';
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
