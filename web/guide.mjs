// C57: display guidance from confirmed demo state; demo.mjs owns every request.
import {esc} from "./views.mjs";
import {resultText} from "./demo.mjs";
import {fridayStory, fridayButton} from "./friday.mjs"; // C62

const local = (label, target) => ({label, target, href:"#overview"});
const route = (label, href) => ({label, href});
export const STEP_HINTS = Object.freeze({
  1: {name:"setup", text:"The orders are staged. Next, load the makers and their shares.", links:[local("Makers", "makers-title"), local("Where the money stands", "stands-title")]},
  2: {name:"seed", text:"Meet the makers and see what the marketplace owes them.", links:[local("Makers", "makers-title"), local("Where the money stands", "stands-title")]},
  3: {name:"monday_dispute", text:"Follow the disputed share into Held back and the money map.", links:[local("Held back", "stands-title"), local("Money map", "map-title")]},
  4: {name:"wednesday_dispute", text:"See how both disputed shares are kept out of payouts.", links:[local("Held back", "stands-title"), local("Money map", "map-title")]},
  5: {name:"cancel_and_verify", text:"Inspect the autonomous cancellation and its confirmed return.", links:[route("Execution history", "#audit")]},
  6: {name:"friday_plan", text:"Your moment: review the exact run and countersign with a passkey, or use plain approval.", owner:true, links:[route("Review and sign", "#decision")]},
  7: {name:"friday_execute", text:"Inspect the recovery: one payment despite a lost response.", links:[route("Execution history", "#audit")]},
  8: {name:"owner_claim_and_draft", text:"Review the $60 refund proposal. It still needs the owner's approval.", owner:true, links:[route("Decisions", "#decision")]},
  9: {name:"safety_blocks", text:"Find the two unsafe proposals the shield stopped.", links:[local("Activity", "activity-title")]},
  10: {name:"following_week_settle", text:"A's reserve is released after settlement. Payment is the next step.", links:[local("Where the money stands", "stands-title")]},
  11: {name:"following_week_pay_a", text:"See A's payment after the release, then explore the recorded sandbox proof.", links:[local("Where the money stands", "stands-title")]},
});
// C65: writes keep their existing guidance and gain an evidence destination.
for (const index of [1,5,7,8,11]) STEP_HINTS[index].links.push(route("See the PayPal calls", "#paypal-calls"));

export function guideModel(state) {
  if (!state || state.simulated !== true || !Number.isInteger(state.step) || state.step < 0 || !Number.isInteger(state.total) || state.total < 1 || state.step > state.total) return null;
  if (state.step === 0 && !state.complete) return {kind:"start"};
  const current = state.steps?.find(step => step.index === state.step && step.done);
  if (typeof current?.label !== "string" || !current.label.trim()) return null;
  // A renamed/reordered server step keeps its own label and a neutral destination.
  const hint = STEP_HINTS[state.step];
  const help = hint?.name === current.name ? hint : {text:"Inspect the latest records in execution history.", links:[route("Execution history", "#audit")]};
  return {kind:"hint", label:current.label, ...help, complete:Boolean(state.complete)};
}

export function guideView(state, {notice = ""} = {}) {
  const model = guideModel(state);
  if (!model) return "";
  const busy = Boolean(state.running);
  const status = notice || (busy || state.last_result?.ok === false ? resultText(state) : "");
  const close = '<button type="button" class="guide-close" data-guide-action="dismiss" aria-label="Dismiss week guidance"><svg class="icon" aria-hidden="true"><use href="#i-x"/></svg></button>';
  const feedback = `<p class="guide-status" role="status" ${status ? "" : "hidden"}>${esc(status)}</p>`;
  const story = fridayStory(state, feedback, close); // C62: API-confirmed checkpoint only.
  if (story) return story;
  if (model.kind === "start") return `<div class="guide-start"><div class="guide-copy"><h2 id="guide-title" tabindex="-1">See CounterSign handle one week of disputes and payouts</h2><p>A staged week on a PayPal mock built from real sandbox recordings. ${esc(state.total)} steps; each one takes a few seconds.</p></div>${close}<div class="guide-actions"><button type="button" class="primary" data-guide-action="step" ${busy ? "disabled" : ""}><span aria-hidden="true">▶</span> ${busy ? "Running…" : "Start the week"}</button><details class="guide-explainer"><summary>What am I looking at?</summary><p>The AI proposes. The shield checks named rules. The owner countersigns payments and refunds. Only then does the adapter call PayPal. In this staged week, PayPal and the owner clicks are simulated; the model is scripted.</p></details></div>${feedback}</div>`;
  const links = model.links.map(link => `<a href="${esc(link.href)}"${link.target ? ` data-guide-target="${esc(link.target)}"` : ""}>${esc(link.label)} <span aria-hidden="true">→</span></a>`).join("");
  return `<div class="guide-hint${model.owner && !model.complete ? " guide-owner" : ""}"><div class="guide-copy"><p id="guide-title" class="guide-label" tabindex="-1">${esc(model.label)}</p><p class="guide-direction">${esc(model.text)} ${links}</p></div>${close}${model.complete ? `<div class="guide-actions"><button type="button" class="secondary" data-guide-action="reset" ${busy ? "disabled" : ""}>Restart the week</button>${fridayButton(state)}<a href="#proof">Sandbox proof <span aria-hidden="true">→</span></a></div>` : ""}${feedback}</div>`;
}

export function createGuide(document, {step, reset, friday} = {}) {
  const host = document.getElementById("week-guide");
  const view = document.defaultView || globalThis;
  let state = null, notice = "", previous = null, pulsed = false;
  const dismissed = new Set();
  function render() {
    if (!host) return;
    const model = guideModel(state), key = model?.kind === "start" ? 0 : state?.step;
    const html = model && !dismissed.has(key) ? guideView(state, {notice}) : "";
    if (host.innerHTML === html) return;
    const active = host.contains?.(document.activeElement), action = document.activeElement?.dataset?.guideAction;
    const open = host.querySelector("details")?.open;
    host.hidden = !html; host.innerHTML = html;
    const details = host.querySelector("details"); if (details && open) details.open = true;
    if (active) {
      const button = action && host.querySelector(`[data-guide-action="${action}"]`);
      (button && !button.disabled ? button : host.querySelector("#guide-title") || document.getElementById("demo-step"))?.focus({preventScroll:true});
    }
  }
  if (host) host.onclick = event => {
    const action = event.target.closest?.("[data-guide-action]")?.dataset.guideAction;
    if (action === "dismiss") {dismissed.add(state?.step); render();}
    if (action === "step") step?.();
    if (action === "reset") reset?.();
    if (action === "friday") friday?.(); // C62: same shared demo request guard.
    const target = event.target.closest?.("[data-guide-target]")?.dataset.guideTarget;
    const section = target && document.getElementById(target);
    if (section) {
      event.preventDefault(); section.setAttribute("tabindex", "-1");
      section.scrollIntoView({behavior:view.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block:"start"});
      section.focus({preventScroll:true});
    }
  };
  return {
    update(value, message = "") {
      if ((previous > 0 && value?.step === 0) || (previous !== 6 && value?.checkpoint === "friday")) dismissed.clear(); // C62
      previous = value?.step; state = value; notice = message;
      render();
      if (!pulsed && value?.step === 1 && !value.running && !view.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
        pulsed = true; document.getElementById("demo-bar")?.classList.add("c57-next-pulse");
      }
    },
  };
}
