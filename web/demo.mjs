// M17 demo control bar. It exists only when the server mounts /api/v1/demo (demo
// mode on the local mock); a 404 keeps it hidden. Each button sends one POST with
// no body: the server replays the next step of the fixed staged week.
import {esc} from "./views.mjs";
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
  const next = (state.steps || []).find(s => !s.done);
  const busy = Boolean(state.running);
  const label = state.complete ? "Week complete" : next?.label || "Next staged step";
  const context = state.model === "scripted" ? "Scripted model · mock PayPal" : "Mock PayPal";
  return `<span class="demo-progress" aria-label="${esc(`Step ${state.step} of ${state.total}`)}" title="${esc(context)}">${esc(state.step)}/${esc(state.total)}</span>
    <div class="demo-actions"><button type="button" id="demo-step" class="primary" title="${esc(label)}" aria-label="${esc(`Next step: ${label}`)}" ${busy || state.complete ? "disabled" : ""}>${busy ? "Running…" : "Next step"}<svg class="icon" aria-hidden="true"><use href="#i-step"/></svg></button>
      <button type="button" id="demo-reset" class="secondary" title="Restart the staged week" ${busy ? "disabled" : ""}><svg class="icon" aria-hidden="true"><use href="#i-restart"/></svg>Restart</button></div>
    <p id="demo-status" class="demo-status sr-only" role="status" aria-live="polite">${esc(resultText(state))}</p>`;
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
    const status = document.getElementById("demo-status");
    if (notice) status.textContent = notice;
    status.className = "demo-status" + (notice || state.last_result?.ok === false ? "" : " sr-only");
    document.getElementById("demo-step").onclick = () => send("step");
    document.getElementById("demo-reset").onclick = reset; // C57: one confirmed restart for both surfaces.
    onState(state, notice); // C57: share fixed errors with the guide too.
  }
  // C57: public restart must keep the compact control's confirmation and guard.
  function reset() {
    if (!state || state.running || sending) return;
    if (!confirm || confirm("Restart the staged week? The mock marketplace starts again from Monday.")) return send("reset");
  }
  async function follow() {
    if (polling) return;
    polling = true;
    try {
      do {
        while (state?.running) {
          await wait(1500);
          show(await read());
        }
        await onChange(state);
        // An enabled control can start another command during the refresh.
        // Keep this poller responsible for it until its state is confirmed too.
      } while (state?.running);
    } catch {
      if (state) show({...state, running: false}, "The staged step status could not be read. Use Refresh.");
    } finally {polling = false;}
  }
  async function send(action) {
    // One request per click burst: the button is disabled before the POST resolves.
    if (!state || state.running || sending) return;
    sending = true;
    for (const id of ["demo-step", "demo-reset"]) document.getElementById(id).disabled = true;
    onState({...state, running:true, action}); // C57: guide gets immediate shared busy feedback.
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
        onState(null); // C57: clear stale guidance when the demo API disappears.
        return null;
      }
    },
    step: () => send("step"),
    reset, // C57: reuse confirmation, request handling and polling.
    get state() {return state;},
  };
}
