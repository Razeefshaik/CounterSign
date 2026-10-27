// "Try to trick the agent": runs the proposal a fooled agent would make through the
// real shield (C32 playground). The server rolls every attempt back; nothing is saved.
import {esc, checksList, status} from "./views.mjs";
import {action as actionWord} from "./words.mjs";

const VERDICTS = {
  BLOCK: ["bad", "Blocked. No money would move.", "At least one check refused this proposal, so the executor would never send it."],
  NEEDS_OWNER: ["owner", "Stopped for your approval.", "The agent cannot do this alone. It would wait for you, and you would see exactly what it wants to do."],
  ALLOW: ["good", "Allowed.", "Every check passed for this proposal."],
};

export function trickView({attacks = [], chosen = "", message = "", result = null, error = ""} = {}) {
  const current = attacks.find(a => a.id === chosen);
  const [tone, headline, explain] = VERDICTS[result?.verdict] || ["quiet", "", ""];
  return `<div class="page-head enter"><div><p>Pick a message a buyer might send, or write your own. CounterSign builds the proposal an agent would make if it believed the message, then runs it through the real shield on this week's records. Nothing is saved and no money moves.</p></div></div>
    <section class="view-card"><h3>Choose a message</h3>
      <div class="attacks" role="group" aria-label="Messages">${attacks.map(a => `<button type="button" class="attack" data-attack="${esc(a.id)}" aria-pressed="${a.id === chosen}"${a.available ? "" : ' aria-disabled="true"'}><strong>${esc(a.title)}</strong><span>${a.available ? esc(a.message || "Your own words") : "Available after " + esc(a.needs)}</span></button>`).join("")}</div>
      <div class="compose"><label for="trick-message" class="small-label">Buyer message${current ? "" : " (choose one above)"}</label>
        <textarea id="trick-message" maxlength="500" ${current?.available ? "" : "disabled"}>${esc(message)}</textarea>
        <div class="compose-row"><span><span id="trick-count">${esc(message.length)}</span> of 500 characters</span><button type="button" id="trick-run" class="primary" ${current?.available && message.trim() ? "" : "disabled"}>Run it through the shield</button></div></div>
    </section>
    ${error ? `<p class="error" role="alert" style="margin:0">${esc(error)}</p>` : ""}
    ${result ? `<div id="trick-result" class="reveal" style="display:flex;flex-direction:column;gap:16px">
      <div class="verdict-banner ${tone}">${status(result.verdict)}<div><h3>${esc(headline)}</h3><p>${esc(explain)} Nothing was written.</p></div></div>
      <div class="split">
        <section class="view-card"><h3>1. Early warning from the message filter</h3>${result.message_flags?.length
          ? result.message_flags.map(f => `<blockquote class="flag"><strong>${esc(f.type)}</strong><p>${esc(f.excerpt)}</p></blockquote>`).join("")
          : '<p class="muted">The filter did not flag this message. It is only an early warning; the shield below still decides.</p>'}</section>
        <section class="view-card"><h3>2. What a fooled agent would propose</h3><p><strong>${esc(actionWord(result.proposal.action))}</strong> <code>${esc(result.proposal.action)}</code></p><pre style="margin-top:10px">${esc(JSON.stringify(result.proposal.params, null, 2))}</pre></section>
      </div>
      <section class="view-card"><h3>3. What the shield decided</h3>${checksList(result.rule_results)}</section>
    </div>` : ""}`;
}

export function createTrick(document, fetcher = fetch) {
  const $ = id => document.getElementById(id);
  let attacks = [], chosen = "", message = "", result = null, error = "", running = false;
  async function load() {
    const response = await fetcher("/api/v1/playground/attacks", {cache: "no-store"});
    if (!response.ok) throw Object.assign(new Error("Playground unavailable"), {status: response.status});
    attacks = (await response.json()).items;
    if (!attacks.some(a => a.id === chosen && a.available)) {
      const first = attacks.find(a => a.available);
      chosen = first?.id || ""; message = first?.message || ""; result = null;
    }
  }
  async function run() {
    if (running) return;
    running = true; error = ""; $("trick-run").disabled = true; $("trick-run").textContent = "Checking…";
    try {
      const response = await fetcher("/api/v1/playground/attempts", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({attack: chosen, message})});
      const body = await response.json().catch(() => ({}));
      if (response.ok) result = body;
      else {result = null; error = response.status === 409 ? "This message needs more of the week first. Run the next staged step, then try again." : "The shield could not be reached. Refresh and try again.";}
    } catch {result = null; error = "The shield could not be reached. Refresh and try again.";}
    finally {running = false; draw();}
  }
  function draw() {
    $("view-body").innerHTML = trickView({attacks, chosen, message, result, error});
    for (const button of document.querySelectorAll("[data-attack]")) button.onclick = () => {
      const attack = attacks.find(a => a.id === button.dataset.attack);
      if (!attack?.available) return;
      chosen = attack.id; message = attack.message || ""; result = null; error = ""; draw();
      if (attack.id === "custom") $("trick-message").focus();
    };
    $("trick-message").oninput = event => {
      message = event.target.value; $("trick-count").textContent = message.length;
      $("trick-run").disabled = !message.trim();
    };
    $("trick-run").onclick = run;
  }
  return {load, draw};
}
