// M17 demo control bar. It exists only when the server mounts /api/v1/demo (demo
// mode on the local mock); a 404 keeps it hidden. Each button sends one POST with
// no body: the server replays the next step of the fixed staged week.
import {esc} from "./views.mjs";

const staged = on => on ? ' <span class="staged">staged</span>' : "";
const when = value => {
  const time = Date.parse(value);
  return Number.isFinite(time) ? new Date(time).toLocaleString("en-GB", {weekday: "short", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC"}) + " UTC" : "—";
};
const messages = {
  DEMO_BUSY: "A staged step is already running. Wait for it to finish.",
  DEMO_WEEK_COMPLETE: "The staged week is complete. Restart the week to replay it.",
};

export function resultText(state) {
  const last = state?.last_result;
  if (state?.running) return state.action === "reset" ? "Restarting the staged week…" : "Running the next staged step…";
  if (!last) return state?.step ? "" : "Nothing has run yet. Run the first staged step to begin the week.";
  if (last.action === "reset") return last.ok ? "Week restarted from an empty marketplace. Nothing has run yet." : `The restart stopped (${last.code}). Try again, or restart the container.`;
  if (state.complete) return "Week complete: every staged step has run. Restart the week to replay it.";
  if (!last.ok && last.code === "SLICE_ALREADY_RUNNING") return "Another staged week process is still running (for example the boot-time replay). Wait, then run the step again.";
  if (!last.ok) return `The staged step stopped (${last.code}). Run it again to resume, or restart the week.`;
  return "Step complete. The dashboard shows the new records.";
}

export function demoBarView(state) {
  const steps = state.steps || [];
  const done = steps.filter(s => s.done);
  const last = done.at(-1), next = steps.find(s => !s.done);
  const busy = Boolean(state.running);
  return `<div class="demo-head"><span class="small-label">STAGED DEMO WEEK${staged(true)}</span>
      <span>Step <strong>${esc(state.step)}</strong> of ${esc(state.total)}</span>
      <span>Staged clock: <strong>${esc(state.clock ? when(state.clock) : "not started")}</strong></span>
      <span>${state.model === "scripted" ? "Scripted model · mock PayPal" : "Mock PayPal"}</span></div>
    <div class="demo-steps">
      <p><span class="small-label">LAST</span> ${last ? esc(last.label) + staged(last.staged) : "Nothing yet"}</p>
      <p><span class="small-label">NEXT</span> ${next ? esc(next.label) + staged(next.staged) : "Week complete"}</p>
    </div>
    <div class="demo-actions"><button type="button" id="demo-step" class="primary" ${busy || state.complete ? "disabled" : ""}>${busy ? "Running…" : "Run next staged step"}</button>
      <button type="button" id="demo-reset" class="secondary" ${busy ? "disabled" : ""}>Restart week</button></div>
    <p id="demo-status" class="demo-status" role="status" aria-live="polite">${esc(resultText(state))}</p>
    <details class="demo-list"><summary>All ${esc(steps.length)} steps · ${esc(state.staged_note || "")}</summary><ol>${steps.map(s => `<li class="${s.done ? "done" : ""}">${s.done ? "✓ " : ""}${esc(s.label)}${staged(s.staged)}${s.interrupted ? ' <span class="badge block">stopped</span>' : ""}</li>`).join("")}</ol></details>`;
}

export function createDemoBar(document, {fetcher = fetch, onChange = async () => {}, onState = () => {}, confirm = globalThis.confirm, wait = ms => new Promise(resolve => setTimeout(resolve, ms))} = {}) {
  const bar = document.getElementById("demo-bar");
  let state = null, polling = false, sending = false;
  const read = async () => {
    const response = await fetcher("/api/v1/demo/state", {cache: "no-store"});
    if (!response.ok) {const error = new Error("Demo state unavailable"); error.status = response.status; throw error;}
    return response.json();
  };
  function show(value, notice = "") {
    state = value;
    bar.innerHTML = demoBarView(state);
    bar.hidden = false;
    if (notice) document.getElementById("demo-status").textContent = notice;
    document.getElementById("demo-step").onclick = () => send("step");
    document.getElementById("demo-reset").onclick = () => {
      if (!confirm || confirm("Restart the staged week? The mock marketplace starts again from Monday.")) return send("reset");
    };
    onState(state);
  }
  async function follow() {
    if (polling) return;
    polling = true;
    try {
      while (state?.running) {
        await wait(1500);
        show(await read());
      }
      await onChange(state);
    } catch {
      if (state) show({...state, running: false}, "The staged step status could not be read. Use Refresh.");
    } finally {polling = false;}
  }
  async function send(action) {
    // One request per click burst: the button is disabled before the POST resolves.
    if (!state || state.running || sending) return;
    sending = true;
    for (const id of ["demo-step", "demo-reset"]) document.getElementById(id).disabled = true;
    try {
      const response = await fetcher(`/api/v1/demo/${action}`, {method: "POST", cache: "no-store"});
      if (response.status === 202) {show(await response.json()); return follow();}
      const body = await response.json().catch(() => ({}));
      show(await read(), messages[body?.error?.code] || "The staged step could not start. Use Refresh.");
    } catch {
      show({...state, running: false}, "The staged step could not start. Use Refresh.");
    } finally {sending = false;}
  }
  return {
    async load() {
      try {
        show(await read());
        if (state.running) follow();
        return state;
      } catch {
        // No demo bar in live mode or when the server does not provide one.
        bar.hidden = true; bar.innerHTML = ""; state = null;
        return null;
      }
    },
    step: () => send("step"),
    reset: () => send("reset"),
    get state() {return state;},
  };
}
