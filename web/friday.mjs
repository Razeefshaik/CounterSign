// C62: narrative is available only when the server confirms an unsigned restore.
import {esc} from "./views.mjs";

export function fridayStory(state, feedback = "", close = "") {
  if (state?.simulated !== true || state.step !== 6 || state.checkpoint !== "friday") return "";
  return `<div class="guide-friday"><div class="guide-copy"><p class="guide-label">The story so far · Simulated: mock PayPal</p><h2 id="guide-title" tabindex="-1">It's Friday. The weekly payout run is waiting for you.</h2><p>Monday to Thursday already happened: two buyers disputed orders, so Maker A's and Maker B's shares are held back, and the AI cancelled Maker C's stuck payout on its own.</p></div>${close}<div class="guide-actions"><a class="primary" href="#decision">Review and sign <span aria-hidden="true">→</span></a><span class="guide-passkey">Use your passkey, or plain approval.</span><a href="#overview" data-guide-target="stands-title">Held back</a><a href="#audit">Execution history</a><button type="button" class="secondary" data-guide-action="reset" ${state.running ? "disabled" : ""}>Watch from Monday</button></div>${feedback}</div>`;
}

export function fridayButton(state) {
  return state?.simulated === true && state.complete && state.friday_available === true
    ? `<button type="button" class="primary" data-guide-action="friday" ${state.running ? "disabled" : ""}>${esc("Back to Friday")}</button>` : "";
}
