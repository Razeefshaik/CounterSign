// C38 overview pieces: the money map as a network plot, the shield scan, maker
// folios, status bars and the activity list. Pure string builders over the C37
// scene model, so every figure is an API value and every piece is testable in Node.
import {esc} from "./views.mjs";
import {rule as ruleWord, action as actionWord} from "./words.mjs";
import {short} from "./scene.mjs";

// World units of the plot; the SVG scales to its card.
export const PW = 1000, PH = 560;
const AT = {paypal: [120, 290], agent: [270, 96], owner: [270, 474], shield: [490, 280], held: [560, 500]};
const r1 = n => Math.round(n * 10) / 10;
const total = m => m.paid + m.inFlight + m.scheduled + m.held + m.owed;

// Node area follows money: radius grows with the square root of the amount.
export const moneyRadius = (minor, lo = 7, hi = 24) => Math.max(lo, Math.min(hi, lo + Math.sqrt(Math.max(0, minor) / 100) * 0.55));
// Edge width follows money moved, with a hairline floor.
export const moneyWidth = minor => r1(Math.max(1, Math.min(7, Math.sqrt(Math.max(0, minor) / 100) * 0.2)));

export function makerAt(i, n) {
  return [820, n === 1 ? 280 : Math.round(96 + i * (368 / (n - 1)))];
}

const verdictKind = p => p.verdict === "BLOCK" ? "stop" : p.state === "NEEDS_OWNER" ? "ask" : p.verdict === "NEEDS_OWNER" ? "signed" : "ok";
const STAMP = {stop: "Stopped", ask: "Needs you", signed: "Signed by you", ok: "Allowed"};

function line(id, [x1, y1], [x2, y2], kind, {width = 1, dashed = false, arrow = true, r0 = 0, r2 = 0} = {}) {
  const L = Math.hypot(x2 - x1, y2 - y1) || 1, ux = (x2 - x1) / L, uy = (y2 - y1) / L;
  const a = [r1(x1 + ux * (r0 + 3)), r1(y1 + uy * (r0 + 3))], b = [r1(x2 - ux * (r2 + 5)), r1(y2 - uy * (r2 + 5))];
  return `<path id="${id}" class="p-edge ${kind}${dashed ? " dashed" : ""}" d="M${a[0]} ${a[1]}L${b[0]} ${b[1]}" style="stroke-width:${width}"${arrow ? ` marker-end="url(#p-arrow-${kind})"` : ""}/>`;
}
function flow(id, count, dur) {
  let out = "";
  for (let i = 0; i < count; i++) out += `<circle class="p-flow" r="3"><animateMotion dur="${dur}s" begin="${-r1(i * dur / count)}s" repeatCount="indefinite"><mpath href="#${id}"/></animateMotion></circle>`;
  return out;
}
const mid = ([x1, y1], [x2, y2], dy = -8) => [r1((x1 + x2) / 2), r1((y1 + y2) / 2 + dy)];
const label = ([x, y], text, cls = "", anchor = "start") => `<text class="p-text ${cls}" x="${x}" y="${y}" text-anchor="${anchor}">${esc(text)}</text>`;

function node(id, kind, [x, y], r, {name, value = "", aria, selected = false, left = false}) {
  const tx = left ? x - r - 10 : x + r + 9, anchor = left ? "end" : "start";
  return `<g class="p-node ${kind}${selected ? " selected" : ""}" data-node="${esc(id)}" tabindex="0" role="button" aria-label="${esc(aria)}">
    <circle class="hit" cx="${x}" cy="${y}" r="${r + 12}"/><circle class="dot" cx="${x}" cy="${y}" r="${r1(r)}"/><circle class="ring" cx="${x}" cy="${y}" r="${r1(r + 4)}"/>
    ${label([tx, y - 1], name, "name", anchor)}${value ? label([tx, y + 15], value, "value", anchor) : ""}</g>`;
}

// The shield draws one bar per rule check run this week: short and blue for pass,
// long and red for a block, long and pink where it asked the owner.
function shieldNode(model, selected) {
  const [x, y] = AT.shield, outcomes = model.agent.list.flatMap(p => (p.rule_results || []).map(r => r.outcome));
  const n = outcomes.length, R = 34;
  const bars = outcomes.map((o, i) => {
    const a = -Math.PI / 2 + (i + 0.5) / n * Math.PI * 2, h = o === "PASS" ? 6 : 12;
    const kind = o === "BLOCK" ? "stop" : o === "NEEDS_OWNER" ? "ask" : "ok";
    return `<line class="check ${kind}" x1="${r1(x + Math.cos(a) * R)}" y1="${r1(y + Math.sin(a) * R)}" x2="${r1(x + Math.cos(a) * (R + h))}" y2="${r1(y + Math.sin(a) * (R + h))}"/>`;
  }).join("");
  return `<g class="p-node shield${selected ? " selected" : ""}" data-node="shield" tabindex="0" role="button"
      aria-label="${esc(`CounterSign shield: ${n} checks on ${model.agent.proposals} proposals, ${model.agent.blocked} stopped, ${model.shield.needsOwner} asked the owner`)}">
    <circle class="hit" cx="${x}" cy="${y}" r="52"/><g class="checks">${bars}</g>
    <circle class="seal" cx="${x}" cy="${y}" r="28"/><circle class="seal-in" cx="${x}" cy="${y}" r="22"/>
    ${label([x, y + 4], String(n), "count", "middle")}${label([x, y + 16], "checks", "unit", "middle")}
    ${label([x, y - 58], "CounterSign shield", "name", "middle")}</g>`;
}

export function plotSvg(model, {selected = "", motion = true} = {}) {
  const sh = AT.shield, n = model.makers.length, edges = [], flows = [], tags = [], sats = [];
  const sending = model.totals.inFlight > 0, anyPaid = model.totals.paid > 0;
  const avail = model.paypal.available;
  edges.push(line("pe-paypal", AT.paypal, sh, avail ? "money" : "quiet", {width: moneyWidth(avail || 0), r0: 14, r2: 46}));
  if (avail) tags.push(label(mid(AT.paypal, sh), short(avail), "flowtag money", "middle"));
  if (motion && (sending || anyPaid)) flows.push(flow("pe-paypal", sending ? 3 : 2, sending ? 2.4 : 4.6));
  edges.push(line("pe-agent", AT.agent, sh, model.agent.proposals ? "agent" : "quiet", {r0: 12, r2: 46}));
  edges.push(line("pe-owner", AT.owner, sh, model.owner.waiting.length ? "ask" : model.owner.approved ? "signed" : "quiet", {dashed: Boolean(model.owner.waiting.length), r0: 12, r2: 46}));
  if (model.held.total) {
    edges.push(line("pe-held", sh, AT.held, "held", {width: moneyWidth(model.held.total), dashed: true, r0: 46, r2: 12}));
    tags.push(label(mid(sh, AT.held, 0).map((v, i) => i ? v : v + 14), short(model.held.total), "flowtag held"));
  }
  const makers = model.makers.map((m, i) => {
    const at = makerAt(i, n), r = moneyRadius(total(m), 9, 22);
    const moving = m.paid + m.inFlight + m.scheduled;
    const kind = m.inFlight ? "money live" : m.scheduled ? "ask" : m.paid ? "money" : "quiet";
    edges.push(line("pe-m" + i, sh, at, kind, {width: moneyWidth(moving), dashed: Boolean(m.scheduled && !m.inFlight), r0: 46, r2: r + 4}));
    if (moving) tags.push(label(mid(sh, at), short(moving), "flowtag " + (m.scheduled && !m.inFlight ? "ask" : "money"), "middle"));
    if (motion && m.inFlight) flows.push(flow("pe-m" + i, 3, 2.2));
    else if (motion && m.paid && !m.scheduled) flows.push(flow("pe-m" + i, 2, 4.8));
    // Satellites: each buyer order, sized by its amount; disputed orders in red.
    m.orders.slice(0, 5).forEach((o, k, list) => {
      const a = (list.length === 1 ? 0 : -42 + k * (84 / (list.length - 1))) * Math.PI / 180;
      const sx = r1(at[0] + 82 * Math.cos(a)), sy = r1(at[1] + 82 * Math.sin(a)), sr = moneyRadius(o.amount, 3, 8);
      const open = o.dispute && o.dispute.phase === "ACTIVE";
      sats.push(`<g class="p-sat${open ? " disputed" : o.dispute ? " settled" : ""}"><line x1="${r1(at[0] + (r + 4) * Math.cos(a))}" y1="${r1(at[1] + (r + 4) * Math.sin(a))}" x2="${sx}" y2="${sy}"/><circle cx="${sx}" cy="${sy}" r="${r1(sr)}"/>${label([r1(sx + sr + 5), r1(sy + 4)], short(o.amount) + (open ? " disputed" : ""), "sat")}<title>Order ${esc(o.ref)}</title></g>`);
    });
    const value = m.inFlight ? short(m.inFlight) + " sending" : m.scheduled ? short(m.scheduled) + " waiting" : m.paid ? short(m.paid) + " paid" : m.held ? short(m.held) + " held" : m.owed ? short(m.owed) + " owed" : "nothing owed";
    return node("maker:" + m.id, "maker", at, r, {name: m.name, value, selected: selected === "maker:" + m.id,
      aria: `${m.name}: ${short(m.paid)} paid, ${short(m.paying)} scheduled or sending, ${short(m.held)} held. Open to explore.`});
  });
  const nodes = [
    node("paypal", "paypal", AT.paypal, 14, {name: "PayPal balance", value: avail == null ? "not read yet" : short(avail) + " available", left: true, selected: selected === "paypal",
      aria: `PayPal balance: ${avail == null ? "not read yet" : short(avail) + " available"}`}),
    node("agent", "agent", AT.agent, 12, {name: "AI agent", value: `${model.agent.proposals} proposal${model.agent.proposals === 1 ? "" : "s"}, reads only`, selected: selected === "agent",
      aria: `AI agent: ${model.agent.proposals} proposals. It never moves money.`}),
    node("owner", "owner", AT.owner, 12, {name: "You, the owner", value: model.owner.waiting.length ? `${model.owner.waiting.length} to sign` : "nothing to sign", selected: selected === "owner",
      aria: `You, the owner: ${model.owner.waiting.length} decisions waiting`}),
    model.held.total ? node("held", "held", AT.held, moneyRadius(model.held.total, 8, 16), {name: "Held for disputes", value: short(model.held.total), selected: selected === "held",
      aria: `Held back for disputes: ${short(model.held.total)}`}) : "",
    shieldNode(model, selected === "shield"),
    ...makers,
  ];
  // Each proposal is a small mark around the agent; its ring shows the share of checks it passed.
  const pa = Math.atan2(AT.agent[1] - sh[1], AT.agent[0] - sh[0]), list = model.agent.list.slice(0, 14);
  const props = list.map((p, k) => {
    const a = pa + (list.length === 1 ? 0 : -1.15 + 2.3 * k / (list.length - 1)), d = k % 2 ? 74 : 50;
    const x = r1(AT.agent[0] + d * Math.cos(a)), y = r1(AT.agent[1] + d * Math.sin(a)), rules = p.rule_results || [];
    const pass = rules.length ? rules.filter(r => r.outcome === "PASS").length / rules.length : 1, kind = verdictKind(p), C = 2 * Math.PI * 7;
    return `<a class="p-prop ${kind}" href="#decision/${esc(p.id)}" aria-label="${esc(`${actionWord(p.action)}: ${STAMP[kind]}`)}"><line x1="${AT.agent[0]}" y1="${AT.agent[1]}" x2="${x}" y2="${y}"/>
      <circle class="arc-bg" cx="${x}" cy="${y}" r="7"/><circle class="arc" cx="${x}" cy="${y}" r="7" stroke-dasharray="${r1(pass * C)} ${r1(C)}" transform="rotate(-90 ${x} ${y})"/><circle class="core" cx="${x}" cy="${y}" r="3.6"/><title>${esc(actionWord(p.action))}: ${esc(STAMP[kind])}</title></a>`;
  }).join("");
  const markers = ["money", "ask", "held", "agent", "signed", "quiet"].map(k => `<marker id="p-arrow-${k}" class="p-arrow ${k}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" markerUnits="userSpaceOnUse" orient="auto-start-reverse"><path d="M0 1L9 5L0 9z"/></marker>`).join("");
  return `<svg class="plot" viewBox="0 0 ${PW} ${PH}" preserveAspectRatio="xMidYMid meet" role="group" aria-label="Money map: PayPal balance, the shield and each maker. Node area follows the amount; line width follows money moved.">
    <defs><pattern id="p-grid" width="28" height="28" patternUnits="userSpaceOnUse"><circle cx="1" cy="1" r="1"/></pattern>${markers}</defs>
    <rect class="p-ground" width="${PW}" height="${PH}" fill="url(#p-grid)"/>
    <g class="p-edges">${edges.join("")}</g><g class="p-sats">${sats.join("")}</g><g class="p-props">${props}</g>
    <g class="p-flows" aria-hidden="true">${flows.join("")}</g><g class="p-tags" aria-hidden="true">${tags.join("")}</g><g class="p-nodes">${nodes.join("")}</g></svg>`;
}

// The shield scan: each proposal is a blip. Cleared ones sit inside, ones waiting
// for the owner circle the holding loop, stopped ones sit in the turned-away sector.
export function scanSvg(model) {
  const list = model.agent.list, blocked = list.filter(p => p.verdict === "BLOCK"), waiting = list.filter(p => p.state === "NEEDS_OWNER");
  let rings = "";
  [140, 104, 68].forEach((r, i) => rings += `<circle class="s-ring${i === 1 ? " dashed" : ""}" r="${r}"/>`);
  for (let a = 0; a < 360; a += 10) {
    const rad = a * Math.PI / 180, r0 = a % 30 ? 134 : 128;
    rings += `<line class="s-tick" x1="${r1(Math.cos(rad) * r0)}" y1="${r1(Math.sin(rad) * r0)}" x2="${r1(Math.cos(rad) * 140)}" y2="${r1(Math.sin(rad) * 140)}"/>`;
  }
  const sector = (a0, a1) => `M0 0L${r1(Math.cos(a0) * 140)} ${r1(Math.sin(a0) * 140)}A140 140 0 0 1 ${r1(Math.cos(a1) * 140)} ${r1(Math.sin(a1) * 140)}Z`;
  let i = 0;
  const blips = list.map(p => {
    const kind = verdictKind(p);
    let x, y, tag = "";
    if (kind === "stop") { const k = blocked.indexOf(p), a = -2.32 + k * 0.42; x = Math.cos(a) * 112; y = Math.sin(a) * 112; tag = actionWord(p.action); }
    else if (kind === "ask") { const k = waiting.indexOf(p), a = k * 2.1; x = -58 + Math.cos(a) * 34; y = 86 + Math.sin(a) * 14; tag = actionWord(p.action); }
    else { const a = (i++ * 137.5) * Math.PI / 180, r = 38 + (i % 3) * 20; x = Math.cos(a) * r; y = Math.sin(a) * r; }
    x = r1(x); y = r1(y);
    const angle = r1(((Math.atan2(y, x) * 180 / Math.PI) + 450) % 360);
    return `<a class="s-blip ${kind}" href="#decision/${esc(p.id)}" style="--at:${angle}" aria-label="${esc(`${actionWord(p.action)}: ${STAMP[kind]}`)}"><circle class="halo" cx="${x}" cy="${y}" r="8"/><path d="M${x} ${r1(y - 5)}L${r1(x + 5)} ${y}L${x} ${r1(y + 5)}L${r1(x - 5)} ${y}Z"/>${tag && kind === "stop" ? `<text x="${r1(x - 9)}" y="${r1(y + 4)}" text-anchor="end">${esc(tag)}</text>` : ""}<title>${esc(actionWord(p.action))}: ${esc(STAMP[kind])}</title></a>`;
  }).join("");
  const askTag = waiting[0] ? `<text class="s-label ask" x="-58" y="62" text-anchor="middle">${esc(actionWord(waiting[0].action))}</text>` : "";
  return `<svg class="scan" viewBox="-160 -160 320 320" role="group" aria-label="${esc(`Shield scan: ${list.length} proposals, ${list.length - blocked.length - waiting.length} cleared, ${waiting.length} waiting for you, ${blocked.length} stopped`)}">
    <circle class="s-ground" r="140"/>${rings}<path class="s-away" d="${sector(-2.55, -1.75)}"/>
    <text class="s-label stop" x="-44" y="-84" text-anchor="middle">Turned away</text>
    <ellipse class="s-hold" cx="-58" cy="86" rx="38" ry="16"/><text class="s-label ask" x="-58" y="120" text-anchor="middle">Holding for you</text>${askTag}
    ${blips}<circle class="s-core" r="15"/></svg>`;
}

export function scanTally(model) {
  const list = model.agent.list, blocked = model.agent.blocked, waiting = model.owner.waiting.length;
  return `<dl class="tally"><div><dt>Scanned</dt><dd>${esc(list.length)}</dd></div><div class="ok"><dt>Cleared</dt><dd>${esc(list.length - blocked - waiting)}</dd></div><div class="ask"><dt>Holding for you</dt><dd>${esc(waiting)}</dd></div><div class="stop"><dt>Turned away</dt><dd>${esc(blocked)}</dd></div></dl>`;
}

// Maker folios: the selected maker in folio blue; the large figure is what matters now.
export function folios(model, chosen = "") {
  if (!model.makers.length) return '<p class="empty-note">No makers yet. Run the first staged step to load the week\'s orders.</p>';
  return model.makers.map(m => {
    const t = total(m) || 1, letter = m.name.trim().slice(-1);
    const [amount, what, chip] = m.inFlight ? [m.inFlight, "sent to PayPal, not confirmed yet", "Sending"] : m.scheduled ? [m.scheduled, "waiting for your signature", "Waiting"]
      : m.held ? [m.held, "held while a buyer's dispute is open", "Held"] : m.paid ? [m.paid, "paid through PayPal", "Paid"] : [m.owed, "owed, not scheduled yet", "Owed"];
    const extra = m.held && chip !== "Held" ? ` · ${short(m.held)} held` : "";
    const seg = (k, v) => v ? `<i class="${k}" style="flex-grow:${r1(v / t * 100)}"></i>` : "";
    return `<button type="button" class="folio${chosen === m.id ? " on" : ""}" data-node="maker:${esc(m.id)}" aria-label="${esc(`${m.name}: ${short(amount)} ${what}${extra}. Open records.`)}">
      <span class="wm" aria-hidden="true">${esc(letter)}</span><span class="who"><b>${esc(m.name)}</b><span>${m.verified ? "Payee verified" : "Payee not verified"}</span></span>
      <span class="chip ${chip.toLowerCase()}">${esc(chip)}</span><span class="amt">${esc(short(amount))}</span><span class="what">${esc(what + extra)}</span>
      <span class="pos" aria-hidden="true">${seg("paid", m.paid)}${seg("sending", m.inFlight)}${seg("waiting", m.scheduled)}${seg("held", m.held)}${seg("owed", m.owed)}</span></button>`;
  }).join("");
}

// Status bars: one stacked bar per maker, plus the owner's signature.
export function stands(model) {
  if (!model.makers.length) return '<p class="empty-note">Bars appear when makers have shares.</p>';
  const max = Math.max(1, ...model.makers.map(total));
  const part = (k, v) => v ? `<i class="${k}" style="flex-grow:${v}"></i>` : "";
  const sig = model.owner.waiting.length ? "ask" : model.owner.approved ? "signed" : "none";
  const cols = model.makers.map(m => `<div class="col"><div class="bar-stack" style="--h:${(total(m) / max).toFixed(3)}" role="img" aria-label="${esc(`${m.name}: ${short(m.paid)} paid, ${short(m.paying)} waiting or sending, ${short(m.held)} held, ${short(m.owed)} owed`)}">${part("paid", m.paid)}${part("sending", m.inFlight)}${part("waiting", m.scheduled)}${part("held", m.held)}${part("owed", m.owed)}</div>
    <b>${esc(m.name)}</b><span>${esc(m.inFlight ? short(m.inFlight) + " sending" : m.scheduled ? short(m.scheduled) + " waiting" : m.held ? short(m.held) + " held" : m.paid ? short(m.paid) + " paid" : short(m.owed) + " owed")}</span></div>`).join("");
  return `<div class="bars">${cols}<div class="col sig"><span class="ring ${sig}">${sig === "signed" ? "Signed" : sig === "ask" ? "Sign" : "—"}</span>
    <b>Your signature</b><span>${esc(sig === "ask" ? `${model.owner.waiting.length} waiting` : sig === "signed" ? `${model.owner.approved} signed` : "not needed yet")}</span></div></div>`;
}

// Activity: what the agent proposed and what the shield said, newest first.
export function activity(model, limit = 6) {
  const list = model.agent.list.filter(p => p.action !== "WAIT").slice(0, limit);
  if (!list.length) return '<p class="empty-note">No proposals yet. The agent starts when the first dispute arrives.</p>';
  return `<ol class="strips">${list.map(p => {
    const kind = verdictKind(p), rules = p.rule_results || [];
    const why = kind === "stop" ? "Refused by " + rules.filter(r => r.outcome === "BLOCK").map(r => ruleWord(r.rule).toLowerCase()).join(", ") + "."
      : kind === "ask" ? "Waits for your signature on this exact payload." : kind === "signed" ? `Passed ${rules.filter(r => r.outcome === "PASS").length} checks, then you signed.`
      : rules.length ? `Passed ${rules.length} checks and ran.` : "Moves no money, so no money checks ran.";
    return `<li><a href="#decision/${esc(p.id)}"><span class="what"><b>${esc(actionWord(p.action))}</b><span>${esc(why)}</span></span><span class="n">${esc(rules.length)} checks</span><span class="stamp ${kind}">${esc(STAMP[kind])}</span></a></li>`;
  }).join("")}</ol>`;
}

// The one finding under the title: what most needs the owner right now.
export function finding(model) {
  const waiting = model.owner.waiting.length, held = model.held.total, open = model.held.makers.length;
  const holdText = held ? ` ${short(held)} stays held for ${open} open dispute${open === 1 ? "" : "s"}.` : "";
  if (model.empty) return "Nothing is owed yet. The week starts when buyer orders arrive.";
  if (model.totals.paying && waiting) return `${short(model.totals.paying)} waits for your signature.${holdText}`;
  if (waiting) return `${waiting} decision${waiting === 1 ? "" : "s"} wait${waiting === 1 ? "s" : ""} for you.${holdText}`;
  if (model.agent.blocked) return `${short(model.totals.paid)} paid. The shield stopped ${model.agent.blocked} unsafe proposal${model.agent.blocked === 1 ? "" : "s"} before money moved.${holdText}`;
  if (model.totals.paid) return `${short(model.totals.paid)} reached makers through PayPal.${holdText}`;
  return `Nothing has been paid yet.${holdText}`;
}

// Week progress: one segment per staged step, grouped by day. Display only;
// the demo dock runs the steps.
export function progress(demo, weekDays) {
  if (!demo?.steps?.length) return "";
  const next = demo.steps.find(s => !s.done);
  return `<ol class="week" aria-label="${esc(`Staged week: step ${demo.step} of ${demo.total}`)}">${weekDays(demo.steps).map(day => {
    const done = day.steps.every(s => s.done), current = next && day.steps.includes(next);
    return `<li class="${done ? "done" : current ? "current" : ""}"><span>${esc(day.name === "Following week" ? "Next week" : day.name)}</span><span class="ticks">${day.steps.map(s => `<i class="${s.done ? "done" : s === next ? "next" : ""}" title="${esc(s.label)}"></i>`).join("")}</span></li>`;
  }).join("")}</ol>`;
}
