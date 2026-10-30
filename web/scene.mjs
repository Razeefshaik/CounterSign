// C37 command center: the money constellation. Pure functions only (no DOM), so
// every figure on screen is traceable to an API record and testable in Node.
// Nothing here invents money: a value the API did not supply is shown as "—".
import {esc} from "./views.mjs";
import {rule as ruleWord, RULES, state as stateWord, action as actionWord, tone, shortId} from "./words.mjs";

export const W = 1200, H = 720;
// The overview crops the empty margin around the drawing so it reads larger.
export const VIEW = [60, 56, 1110, 664];
const AT = {paypal: [150, 360], agent: [400, 150], owner: [400, 575], shield: [620, 360], held: [820, 622]};
const SHIELD_R = 80;
const minor = value => Number.isSafeInteger(value?.minor) ? value.minor : 0;

// "$1,086" when whole, "$333.33" otherwise; never a float in between.
export function short(n) {
  if (!Number.isSafeInteger(n)) return "—";
  const sign = n < 0 ? "-" : "", a = Math.abs(n), cents = a % 100;
  return sign + "$" + Math.trunc(a / 100).toLocaleString("en-US") + (cents ? "." + String(cents).padStart(2, "0") : "");
}
const tail = id => String(id ?? "").replace(/^[a-z]+:/, "");
const ref = label => {
  const text = String(label ?? "");
  if (/^PP-D-/i.test(text)) return "PP-D …" + text.slice(-6);
  return /^[0-9A-F-]{16,}$/i.test(text) ? "…" + text.replace(/-/g, "").slice(-6) : text;
};

// One model for the whole scene, built only from dashboard API documents.
export function sceneModel({makers = [], disputes = [], proposals = [], cash = null, comparison = null, graphs = {}} = {}) {
  const shown = makers.slice(0, 6);
  const rows = shown.map(m => {
    const graph = graphs[m.id];
    // A graph counts only if it is rooted at this maker (fixture previews return one graph for all).
    const own = graph?.nodes?.some(n => n.id === "maker:" + m.id) ? graph : null;
    const disputed = new Map();
    for (const e of own?.edges || []) if (e.type === "disputes") disputed.set(e.to, own.nodes.find(n => n.id === e.from));
    const orders = (own?.nodes || []).filter(n => n.type === "order").map(n => ({
      id: n.id, ref: ref(n.label), amount: minor(n.amount),
      dispute: disputed.get(n.id) ? {id: disputed.get(n.id).id, ref: ref(disputed.get(n.id).label), phase: disputed.get(n.id).state} : null}));
    const open = disputes.filter(d => d.order?.maker_id === m.id && d.phase === "ACTIVE");
    const paid = minor(m.settled_last_30d), scheduled = minor(m.scheduled), inFlight = minor(m.in_flight);
    const held = minor(m.reserved), owed = minor(m.owed_open), paying = scheduled + inFlight;
    return {id: m.id, name: m.display_name, verified: Boolean(m.payout_email_verified), email: m.payout_email_masked || "",
      paid, scheduled, inFlight, paying, held, owed, orders, openDisputes: open.length,
      status: inFlight ? "sending" : scheduled ? "scheduled" : held ? "held" : paid ? "paid" : owed ? "owed" : "idle"};
  });
  const verdicts = verdict => proposals.filter(p => p.verdict === verdict).length;
  // The ring shows the decision that matters now: one waiting for the owner, else the latest.
  const focus = proposals.find(p => p.state === "NEEDS_OWNER") || proposals[0] || null;
  const outcomes = Object.fromEntries((focus?.rule_results || []).map(r => [r.rule, r.outcome]));
  const p = cash?.paypal;
  return {
    empty: !rows.length,
    hidden: Math.max(0, makers.length - shown.length),
    makers: rows,
    paypal: {available: p ? minor(p.available) : null, stale: Boolean(p?.stale), unreserved: cash ? minor(cash.unreserved) : null},
    agent: {proposals: proposals.length, allowed: verdicts("ALLOW"), blocked: verdicts("BLOCK"), list: proposals},
    owner: {waiting: proposals.filter(x => x.state === "NEEDS_OWNER"), approved: proposals.filter(x => x.approval).length},
    shield: {focus, ticks: Object.keys(RULES).map(code => ({code, outcome: outcomes[code] || ""})), needsOwner: verdicts("NEEDS_OWNER")},
    held: {total: rows.reduce((s, m) => s + m.held, 0), makers: rows.filter(m => m.held)},
    totals: {paid: rows.reduce((s, m) => s + m.paid, 0), paying: rows.reduce((s, m) => s + m.paying, 0), inFlight: rows.reduce((s, m) => s + m.inFlight, 0)},
    comparison: comparison?.available ? comparison : null,
  };
}

// Cubic helpers: a path string and a point on it, for edge labels.
const curve = ([x1, y1], [c1x, c1y], [c2x, c2y], [x2, y2]) => ({d: `M${x1} ${y1}C${c1x} ${c1y} ${c2x} ${c2y} ${x2} ${y2}`, pts: [[x1, y1], [c1x, c1y], [c2x, c2y], [x2, y2]]});
function at({pts}, t) {
  const u = 1 - t, [a, b, c, d] = pts;
  return [0, 1].map(i => u * u * u * a[i] + 3 * u * u * t * b[i] + 3 * u * t * t * c[i] + t * t * t * d[i]);
}
const round = n => Math.round(n * 10) / 10;

// Particles ride an edge with SMIL animateMotion; begin offsets stagger them.
function sparks(id, count, dur, kind, {burst = false} = {}) {
  let out = "";
  for (let i = 0; i < count; i++) {
    const begin = -round(i * dur / count);
    out += `<circle class="spark ${kind}" r="${burst ? 3.4 : 3}"><animateMotion dur="${dur}s" begin="${begin}s" repeatCount="indefinite" rotate="auto"><mpath href="#${id}"/></animateMotion>${burst
      ? `<animate attributeName="r" values="3.4;3.4;11" keyTimes="0;0.84;1" dur="${dur}s" begin="${begin}s" repeatCount="indefinite"/><animate attributeName="opacity" values="1;1;0" keyTimes="0;0.84;1" dur="${dur}s" begin="${begin}s" repeatCount="indefinite"/>`
      : `<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.12;0.86;1" dur="${dur}s" begin="${begin}s" repeatCount="indefinite"/>`}</circle>`;
  }
  return out;
}
const edge = (id, c, kind, {dashed = false, delay = 0} = {}) =>
  `<path id="${id}" class="edge ${kind}${dashed ? " ants" : ""}" d="${c.d}" pathLength="1" style="--d:${delay}ms"/>`;
function tag(c, t, text, kind) {
  const [x, y] = at(c, t), w = text.length * 8.1 + 20;
  return `<g class="edge-tag ${kind}" transform="translate(${round(x)} ${round(y)})"><rect x="${round(-w / 2)}" y="-13" width="${round(w)}" height="26" rx="13"/><text y="5" text-anchor="middle">${esc(text)}</text></g>`;
}

function orb(kind, [x, y], r, {id, label, sub = "", amount = "", aria, labelAt = "below", extra = "", selected = false, delay = 0}) {
  const ly = labelAt === "above" ? y - r - (amount ? 54 : 36) : y + r + 27;
  const text = `<text class="n-label" x="${x}" y="${ly}" text-anchor="middle">${esc(label)}</text>${amount
    ? `<text class="n-amount" x="${x}" y="${ly + 25}" text-anchor="middle">${esc(amount)}</text>` : ""}${sub
    ? `<text class="n-sub" x="${x}" y="${ly + (amount ? 46 : 21)}" text-anchor="middle">${esc(sub)}</text>` : ""}`;
  return `<g class="node ${kind}${selected ? " selected" : ""}" data-node="${esc(id)}" tabindex="0" role="button" aria-label="${esc(aria)}" style="--d:${delay}ms">
    <circle class="halo" cx="${x}" cy="${y}" r="${r * 2.4}" fill="url(#halo-${kind})"/>
    <circle class="hit" cx="${x}" cy="${y}" r="${r + 14}"/>
    <circle class="core" cx="${x}" cy="${y}" r="${r}" fill="url(#orb-${kind})"/>
    <circle class="rim" cx="${x}" cy="${y}" r="${r + 7}"/>${extra}${text}</g>`;
}

function shieldNode(model, selected, delay) {
  const [x, y] = AT.shield;
  const ticks = model.shield.ticks.map((t, i) => {
    const a = (i * 360 / 16) - 90;
    return `<rect class="tick ${t.outcome ? tone(t.outcome) : "idle"}" x="${x - 3}" y="${y - SHIELD_R}" width="6" height="15" rx="3" transform="rotate(${a + 90} ${x} ${y})" style="--i:${i}"><title>${esc(ruleWord(t.code))}: ${esc(t.outcome ? stateWord(t.outcome) : "not part of this decision")}</title></rect>`;
  }).join("");
  const blocked = model.agent.blocked;
  return `<g class="node shield${selected ? " selected" : ""}" data-node="shield" tabindex="0" role="button" style="--d:${delay}ms"
      aria-label="${esc(`CounterSign shield: 16 checks. ${model.agent.proposals} proposals checked, ${blocked} blocked, ${model.shield.needsOwner} needed the owner`)}">
    <circle class="halo" cx="${x}" cy="${y}" r="190" fill="url(#halo-shield)"/>
    <circle class="hit" cx="${x}" cy="${y}" r="${SHIELD_R + 12}"/>
    <circle class="orbit slow" cx="${x}" cy="${y}" r="${SHIELD_R + 18}"/>
    <circle class="orbit fast" cx="${x}" cy="${y}" r="${SHIELD_R - 22}"/>
    <g class="ticks">${ticks}</g>
    <circle class="core" cx="${x}" cy="${y}" r="44" fill="url(#orb-shield)"/>
    <path class="glyph" d="M${x - 15} ${y - 2}l10 10 20-22"/>
    <text class="n-label strong" x="${x}" y="${y + SHIELD_R + 42}" text-anchor="middle">CounterSign shield</text>
    <text class="n-sub" x="${x}" y="${y + SHIELD_R + 64}" text-anchor="middle">16 checks before any money moves</text>
    ${blocked ? `<g class="s-badge" transform="translate(${x + 78} ${y - 96})"><rect x="-50" y="-14" width="100" height="28" rx="14"/><text y="5.5" text-anchor="middle">${esc(blocked)} blocked</text></g>` : ""}
  </g>`;
}

function ring(m, [x, y], r) {
  const total = m.paid + m.inFlight + m.scheduled + m.held + m.owed;
  if (!total) return `<circle class="seg idle" cx="${x}" cy="${y}" r="${r}" pathLength="100"/>`;
  let start = 0;
  return [["paid", m.paid], ["paying", m.inFlight], ["scheduled", m.scheduled], ["held", m.held], ["owed", m.owed]].filter(([, v]) => v).map(([k, v]) => {
    const len = Math.max(2, round(100 * v / total) - 1.5);
    const out = `<circle class="seg ${k}" cx="${x}" cy="${y}" r="${r}" pathLength="100" stroke-dasharray="${len} ${round(100 - len)}" stroke-dashoffset="${round(-start)}" transform="rotate(-90 ${x} ${y})"/>`;
    start += round(100 * v / total);
    return out;
  }).join("");
}

function makerY(i, n) {
  if (n === 1) return 360;
  return Math.round(140 + i * (440 / (n - 1)));
}
const makerSentence = m => [m.paid && short(m.paid) + " paid", m.inFlight && short(m.inFlight) + " in flight", m.scheduled && short(m.scheduled) + " scheduled",
  m.held && short(m.held) + " held back", m.owed && short(m.owed) + " owed"].filter(Boolean).join(", ") || "nothing owed yet";

function makerNode(m, i, n, {selected, motion, entrance}) {
  const x = 990, y = makerY(i, n), r = 30;
  const sats = m.orders.slice(0, 5).map((o, k, list) => {
    const a = (list.length === 1 ? 0 : -50 + k * (100 / (list.length - 1))) * Math.PI / 180;
    const sx = round(x + 74 * Math.cos(a)), sy = round(y + 74 * Math.sin(a));
    const disputed = o.dispute && o.dispute.phase === "ACTIVE";
    return `<g class="sat${o.dispute ? " disputed " + esc(String(o.dispute.phase).toLowerCase()) : ""}"><line x1="${round(x + (r + 8) * Math.cos(a))}" y1="${round(y + (r + 8) * Math.sin(a))}" x2="${sx}" y2="${sy}"/>
      ${disputed ? `<circle class="alarm" cx="${sx}" cy="${sy}" r="9"/>` : ""}<circle class="dot" cx="${sx}" cy="${sy}" r="${disputed ? 5.5 : 4.5}"/>
      <text x="${sx + 12}" y="${sy + 5}">${esc(short(o.amount))}${o.dispute ? (disputed ? " disputed" : " " + esc(stateWord(o.dispute.phase).toLowerCase())) : ""}</text><title>Order ${esc(o.ref)}</title></g>`;
  }).join("");
  const headline = m.inFlight ? short(m.inFlight) + " in flight" : m.scheduled ? short(m.scheduled) + " scheduled" : m.paid ? short(m.paid) + " paid" : m.held ? short(m.held) + " held" : m.owed ? short(m.owed) + " owed" : "no earnings yet";
  return orb("maker", [x, y], r, {id: "maker:" + m.id, label: m.name, amount: headline, selected, delay: entrance ? 500 + i * 120 : 0,
    sub: m.openDisputes ? `${m.openDisputes} open dispute${m.openDisputes === 1 ? "" : "s"}` : m.verified ? "verified payee" : "payee not verified",
    aria: `${m.name}: ${makerSentence(m)}. Open to explore.`,
    extra: `<g class="ring">${ring(m, [x, y], r + 7)}</g><g class="sats">${sats}</g>${motion && m.status === "sending" ? `<circle class="ping" cx="${x}" cy="${y}" r="${r}"/>` : ""}`});
}

export function sceneSvg(model, {selected = "", motion = true, entrance = false} = {}) {
  const n = model.makers.length;
  const sh = AT.shield;
  const edges = [], tags = [], parts = [];
  const pp = curve([AT.paypal[0] + 40, 360], [300, 360], [450, 360], [sh[0] - SHIELD_R - 4, 360]);
  const sending = model.totals.inFlight > 0, anyPaid = model.totals.paid > 0, planned = model.totals.paying > 0;
  edges.push(edge("e-paypal", pp, sending ? "money live" : anyPaid ? "money" : planned ? "money planned" : "quiet", {delay: entrance ? 200 : 0}));
  if (motion && (sending || anyPaid)) parts.push(sparks("e-paypal", sending ? 4 : 2, sending ? 2.4 : 4.4, sending ? "cyan" : "teal"));
  const ag = curve([AT.agent[0] + 30, AT.agent[1]], [510, 150], [550, 230], [sh[0] - 57, sh[1] - 57]);
  edges.push(edge("e-agent", ag, model.agent.proposals ? "agent" : "quiet", {dashed: true, delay: entrance ? 300 : 0}));
  if (motion && model.agent.proposals) parts.push(sparks("e-agent", 2, 3.2, "violet"));
  if (motion && model.agent.blocked) parts.push(sparks("e-agent", Math.min(2, model.agent.blocked), 2.6, "red", {burst: true}));
  const ow = curve([AT.owner[0] + 28, AT.owner[1]], [510, 575], [550, 490], [sh[0] - 57, sh[1] + 57]);
  edges.push(edge("e-owner", ow, model.owner.waiting.length ? "owner waiting" : model.owner.approved ? "owner" : "quiet", {dashed: Boolean(model.owner.waiting.length), delay: entrance ? 350 : 0}));
  if (motion && model.owner.approved) parts.push(sparks("e-owner", 1, 4.2, "gold"));
  if (model.owner.waiting.length) tags.push(tag(ow, 0.45, "needs your signature", "owner"));
  model.makers.forEach((m, i) => {
    const my = makerY(i, n), dx = 990 - sh[0], dy = my - sh[1], len = Math.hypot(dx, dy);
    const start = [round(sh[0] + (SHIELD_R + 6) * dx / len), round(sh[1] + (SHIELD_R + 6) * dy / len)];
    const c = curve(start, [start[0] + 150, start[1] + dy * 0.12], [990 - 190, my], [990 - 44, my]);
    const kind = m.inFlight ? "money live" : m.scheduled ? "money planned" : m.paid ? "money" : "quiet";
    edges.push(edge("e-m" + i, c, kind, {dashed: Boolean(m.inFlight || m.scheduled), delay: entrance ? 420 + i * 120 : 0}));
    if (motion && m.inFlight) parts.push(sparks("e-m" + i, 4, 2.2, "cyan"));
    else if (motion && m.paid && !m.scheduled) parts.push(sparks("e-m" + i, 2, 4.6, "teal"));
    const tagText = m.inFlight ? short(m.inFlight) + " in flight" : m.scheduled ? short(m.scheduled) + " scheduled" : m.paid ? short(m.paid) + " paid" : "";
    if (tagText) tags.push(tag(c, 0.62, tagText, m.inFlight ? "live" : m.scheduled ? "planned" : "paid"));
  });
  if (model.held.total) {
    const hc = curve([sh[0] + 52, sh[1] + 62], [700, 520], [740, 622], [AT.held[0] - 34, AT.held[1]]);
    edges.push(edge("e-held", hc, "held", {delay: entrance ? 600 : 0}));
    if (motion) parts.push(sparks("e-held", 2, 3.4, "amber"));
    model.makers.forEach((m, i) => {
      if (!m.held) return;
      const my = makerY(i, n);
      const c = curve([AT.held[0] + 32, AT.held[1] - 8], [905, 622], [935, my + 70], [990 - 26, my + 22]);
      edges.push(edge("e-h" + i, c, "held thin", {dashed: true, delay: entrance ? 700 + i * 100 : 0}));
    });
  }
  const nodes = [
    orb("paypal", AT.paypal, 36, {id: "paypal", label: "PayPal balance", amount: model.paypal.available == null ? "—" : short(model.paypal.available),
      sub: model.paypal.stale ? "balance is stale" : "available now", selected: selected === "paypal", delay: entrance ? 80 : 0,
      aria: `PayPal balance: ${model.paypal.available == null ? "not read yet" : short(model.paypal.available) + " available"}`}),
    orb("agent", AT.agent, 26, {id: "agent", label: "AI agent", labelAt: "above", sub: `reads only · ${model.agent.proposals} proposal${model.agent.proposals === 1 ? "" : "s"}`,
      selected: selected === "agent", delay: entrance ? 160 : 0, aria: `AI agent: ${model.agent.proposals} proposals, ${model.agent.blocked} blocked. It never moves money.`,
      extra: `<path class="glyph" d="M${AT.agent[0]} ${AT.agent[1] - 10}l3 7 7 3-7 3-3 7-3-7-7-3 7-3z"/>`}),
    orb("owner", AT.owner, 24, {id: "owner", label: "You, the owner", sub: model.owner.waiting.length ? `${model.owner.waiting.length} decision${model.owner.waiting.length === 1 ? "" : "s"} waiting` : `${model.owner.approved} approval${model.owner.approved === 1 ? "" : "s"} signed`,
      selected: selected === "owner", delay: entrance ? 240 : 0, aria: `You, the owner: ${model.owner.waiting.length} decisions waiting`,
      extra: `<path class="glyph" d="M${AT.owner[0] - 7} ${AT.owner[1] + 8}a7 7 0 0 1 14 0M${AT.owner[0]} ${AT.owner[1] - 1}m-4.5 0a4.5 4.5 0 1 0 9 0a4.5 4.5 0 1 0-9 0"/>${model.owner.waiting.length ? `<circle class="ping owner" cx="${AT.owner[0]}" cy="${AT.owner[1]}" r="24"/>` : ""}`}),
    shieldNode(model, selected === "shield", entrance ? 120 : 0),
    model.held.total ? orb("held", AT.held, 26, {id: "held", label: "Held back for disputes", amount: short(model.held.total), selected: selected === "held", delay: entrance ? 650 : 0,
      aria: `Held back for disputes: ${short(model.held.total)}`, extra: `<path class="glyph" d="M${AT.held[0] - 7} ${AT.held[1] - 2}h14v10h-14zM${AT.held[0] - 4} ${AT.held[1] - 2}v-4a4 4 0 0 1 8 0v4"/>`}) : "",
    ...model.makers.map((m, i) => makerNode(m, i, n, {selected: selected === "maker:" + m.id, motion, entrance})),
  ];
  return `<svg class="constellation${entrance ? " entering" : ""}" viewBox="${VIEW.join(" ")}" preserveAspectRatio="xMidYMid meet" role="group" aria-label="Money constellation: PayPal balance, the shield, and each maker">
    ${defs()}<g class="edges">${edges.join("")}</g><g class="sparks" aria-hidden="true">${parts.join("")}</g><g class="tags" aria-hidden="true">${tags.join("")}</g><g class="nodes">${nodes.join("")}</g></svg>`;
}

// Gradients are ours: soft spheres and halos per node kind.
function defs() {
  const sphere = (id, a, b, c, d) => `<radialGradient id="orb-${id}" cx="38%" cy="32%" r="75%"><stop offset="0" stop-color="${a}"/><stop offset="0.35" stop-color="${b}"/><stop offset="0.78" stop-color="${c}"/><stop offset="1" stop-color="${d}"/></radialGradient>`;
  const halo = (id, color, o = 0.42) => `<radialGradient id="halo-${id}"><stop offset="0" stop-color="${color}" stop-opacity="${o}"/><stop offset="0.45" stop-color="${color}" stop-opacity="${round(o / 3)}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient>`;
  const spark = (id, color) => `<radialGradient id="spark-${id}"><stop offset="0" stop-color="#fff"/><stop offset="0.35" stop-color="${color}"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></radialGradient>`;
  return `<defs>${sphere("shield", "#fffbea", "#f6d27e", "#b07a1f", "#3f2a07")}${sphere("maker", "#ffe6f4", "#f17cc0", "#7b3fd6", "#25104d")}${sphere("paypal", "#e6fbff", "#4cc7f0", "#0d5a7c", "#06243a")}${sphere("agent", "#f1eaff", "#a98bff", "#4b23a6", "#1b0c45")}${sphere("owner", "#fff7e0", "#f2c766", "#8f6113", "#2f1f05")}${sphere("held", "#fff1d8", "#f5a623", "#8a4b0b", "#2c1503")}
    ${halo("shield", "#f6c85f", 0.36)}${halo("maker", "#e66ab3")}${halo("paypal", "#38bdf8")}${halo("agent", "#9b7bff")}${halo("owner", "#f2c766", 0.3)}${halo("held", "#f5a623")}
    ${spark("cyan", "#5ee7ff")}${spark("teal", "#4ef0c4")}${spark("violet", "#b79bff")}${spark("red", "#ff5d73")}${spark("amber", "#ffb547")}${spark("gold", "#ffd977")}</defs>`;
}

// --- Drill-down: one maker's records as category cards and a fan of items ---

const CATEGORIES = [
  ["order", "Orders", "What buyers paid"],
  ["obligation", "Maker shares", "What the maker is owed"],
  ["attempt", "PayPal payouts", "Each payout item sent"],
  ["dispute", "Disputes", "Buyer claims on orders"],
  ["reservation", "Held back", "Shares waiting on a dispute"],
];
const VERBS = {disputes: "disputes", creates: "creates", holds: "holds back", allocated_to: "is paid through", attempted_by: "was sent as", owed_to: "is owed to", pays: "pays"};

export function itemTitle(node) {
  if (!node) return "";
  if (node.type === "order") return "Order " + ref(node.label);
  if (node.type === "dispute") return "Dispute " + ref(node.label);
  if (node.type === "obligation") return "Share of an order";
  if (node.type === "reservation") return node.label || "Reservation";
  return String(node.label ?? "");
}

export function focusModel(graph, maker) {
  const nodes = graph?.nodes?.some(n => n.id === "maker:" + maker.id) ? graph.nodes : [];
  const edges = nodes.length ? graph.edges || [] : [];
  const categories = CATEGORIES.map(([type, title, hint]) => {
    const items = nodes.filter(n => n.type === type);
    // A reservation holds money only while ACTIVE; released ones stay listed but are not summed.
    const counted = type === "reservation" ? items.filter(i => i.state === "ACTIVE") : items;
    return {type, title, hint, items, total: counted.reduce((s, i) => s + minor(i.amount), 0), alert: type === "dispute" && items.some(i => i.state === "ACTIVE")};
  }).filter(c => c.items.length || c.type === "order" || c.type === "obligation");
  const links = id => edges.filter(e => e.from === id || e.to === id).map(e => {
    const other = nodes.find(n => n.id === (e.from === id ? e.to : e.from));
    return {verb: VERBS[e.type] || String(e.type).replaceAll("_", " "), outgoing: e.from === id, other};
  }).filter(l => l.other);
  return {maker, categories, links, nodes};
}

export function defaultCategory(fm) {
  return (fm.categories.find(c => c.alert) || fm.categories.find(c => c.type === "attempt" && c.items.length) || fm.categories[0])?.type || "order";
}

export function focusSvg(fm, {category = "", item = "", motion = true} = {}) {
  const [ox, oy] = [200, 360];
  const cats = fm.categories, n = cats.length;
  const top = 360 - (n - 1) * 46;
  const chosen = cats.find(c => c.type === category) || cats[0];
  const m = fm.maker;
  const edges = [], parts = [], cards = [];
  cats.forEach((c, i) => {
    const cy = top + i * 92, on = c === chosen;
    const path = curve([ox + 70, oy], [ox + 160, oy], [330, cy], [430, cy]);
    edges.push(`<path id="f-c${i}" class="edge draw ${on ? "money live" : "quiet"}" d="${path.d}" pathLength="1" style="--d:${120 + i * 70}ms"/>`);
    if (motion && on) parts.push(sparks("f-c" + i, 2, 2.4, "cyan"));
    cards.push(`<g class="card${on ? " on" : ""}${c.alert ? " alert" : ""}" data-category="${esc(c.type)}" tabindex="0" role="button" aria-pressed="${on}" aria-label="${esc(`${c.title}: ${c.items.length}${c.total ? ", " + short(c.total) : ""}`)}" style="--d:${200 + i * 70}ms">
      <rect x="430" y="${cy - 35}" width="256" height="70" rx="15"/>
      <text class="c-count" x="452" y="${cy + 10}">${esc(c.items.length)}</text>
      <text class="c-title" x="${452 + String(c.items.length).length * 17 + 14}" y="${cy - 4}">${esc(c.title)}</text>
      <text class="c-hint" x="${452 + String(c.items.length).length * 17 + 14}" y="${cy + 17}">${esc(c.total ? short(c.total) + " · " + c.hint.toLowerCase() : c.hint)}</text></g>`);
  });
  const ci = cats.indexOf(chosen), ccy = top + ci * 92;
  const items = (chosen?.items || []).slice(0, 10);
  const span = (items.length - 1) * 54;
  const start = Math.max(70, Math.min(H - 70 - span, ccy - span / 2));
  const fan = items.map((it, k) => {
    const y = Math.round(start + k * 54);
    const path = curve([686, ccy], [770, ccy], [775, y], [860, y]);
    edges.push(`<path id="f-i${k}" class="edge draw thin ${tone(it.state) === "bad" ? "bad" : "money"}" d="${path.d}" pathLength="1" style="--d:${420 + k * 60}ms"/>`);
    if (motion) parts.push(sparks("f-i" + k, 1, 2.8 + (k % 3) * 0.4, tone(it.state) === "bad" ? "red" : "teal"));
    const t = tone(it.state);
    return `<g class="item ${t}${item === it.id ? " on" : ""}" data-item="${esc(it.id)}" tabindex="0" role="button" aria-label="${esc(`${itemTitle(it)}${it.state ? ", " + stateWord(it.state) : ""}${it.amount ? ", " + short(minor(it.amount)) : ""}`)}" style="--d:${480 + k * 60}ms">
      <circle class="hit" cx="870" cy="${y}" r="20"/><circle class="dot" cx="868" cy="${y}" r="7"/>
      <text class="i-title" x="888" y="${y - 3}">${esc(itemTitle(it))}</text>
      <text class="i-meta" x="888" y="${y + 17}">${esc([it.state ? stateWord(it.state) : "", it.amount ? short(minor(it.amount)) : ""].filter(Boolean).join(" · "))}</text></g>`;
  }).join("");
  const more = (chosen?.items.length || 0) - items.length;
  const mr = {...m, orders: []};
  return `<svg class="constellation focus" viewBox="60 84 1030 552" preserveAspectRatio="xMidYMid meet" role="group" aria-label="${esc(m.name)}: records by category">
    ${defs()}<g class="edges">${edges.join("")}</g><g class="sparks" aria-hidden="true">${parts.join("")}</g>
    <g class="nodes">${orb("maker", [ox, oy], 58, {id: "maker:" + m.id, label: m.name, amount: makerSentence(m), aria: `${m.name}: ${makerSentence(m)}`, selected: true,
      extra: `<g class="ring">${ring(mr, [ox, oy], 66)}</g>`})}${cards.join("")}${fan}
      ${items.length ? "" : `<text class="n-sub" x="870" y="${ccy + 5}">No records in this category yet</text>`}
      ${more > 0 ? `<text class="n-sub" x="886" y="${start + items.length * 54 + 6}">+${esc(more)} more</text>` : ""}</g></svg>`;
}

// --- Side panel content (HTML) ---

const row = (label, value, cls = "") => `<div class="kv${cls ? " " + cls : ""}"><dt>${esc(label)}</dt><dd>${value}</dd></div>`;
const pill = value => `<span class="pill ${tone(value)}" title="${esc(value || "")}">${esc(value ? stateWord(value) : "—")}</span>`;

export function itemPanel(fm, id) {
  const node = fm.nodes.find(n => n.id === id);
  if (!node) return "";
  const links = fm.links(id);
  return `<header class="panel-head"><span class="eyebrow">${esc(fm.maker.name)} · ${esc(CATEGORIES.find(c => c[0] === node.type)?.[1] || node.type)}</span><h2>${esc(itemTitle(node))}</h2></header>
    <dl class="kvs">${node.state ? row("State", pill(node.state)) : ""}${node.amount ? row("Amount", esc(short(minor(node.amount)))) : ""}${row("Record", `<span class="mono">${esc(shortId(tail(node.id)))}</span>`)}</dl>
    ${links.length ? `<h3 class="panel-sub">How it connects</h3><ul class="links">${links.map(l => `<li><span class="verb">${l.outgoing ? "This " + esc(l.verb) : "Linked: " + esc(l.verb)}</span><strong>${esc(itemTitle(l.other))}</strong>${l.other.state ? pill(l.other.state) : ""}${l.other.amount ? `<span class="amt">${esc(short(minor(l.other.amount)))}</span>` : ""}</li>`).join("")}</ul>` : ""}`;
}

export function makerPanel(m) {
  const bar = [["paid", m.paid, "Paid"], ["paying", m.inFlight, "In flight at PayPal"], ["scheduled", m.scheduled, "Scheduled in a run"], ["held", m.held, "Held back"], ["owed", m.owed, "Owed, not scheduled"]];
  const total = bar.reduce((s, [, v]) => s + v, 0);
  return `<header class="panel-head"><span class="eyebrow">Maker</span><h2>${esc(m.name)}</h2><p>${esc(makerSentence(m))}.</p></header>
    ${total ? `<div class="stack" role="img" aria-label="${esc(makerSentence(m))}">${bar.filter(([, v]) => v).map(([k, v]) => `<span class="${k}" style="--w:${round(100 * v / total)}%"></span>`).join("")}</div>
    <dl class="kvs">${bar.map(([k, v, label]) => row(label, esc(short(v)), "dot-" + k)).join("")}</dl>` : '<p class="muted">No earnings recorded for this maker yet.</p>'}
    <dl class="kvs">${row("Payee", `${esc(m.email || "—")} ${m.verified ? '<span class="pill good">Verified</span>' : '<span class="pill bad">Not verified</span>'}`)}${row("Open disputes", esc(m.openDisputes))}</dl>
    <p class="hint">Select a card in the scene to list its records. Select a record to see how it connects.</p>`;
}

// Each blocked proposal with the exact checks that refused it.
function blockedList(model) {
  const blocked = model.agent.list.filter(p => p.verdict === "BLOCK");
  if (!blocked.length) return "";
  return `<h3 class="panel-sub">Stopped before any money moved</h3><ol class="feed blocked">${blocked.slice(0, 5).map(p => {
    const rules = (p.rule_results || []).filter(r => r.outcome === "BLOCK").map(r => ruleWord(r.rule));
    return `<li><a href="#decision/${esc(p.id)}"><strong>${esc(actionWord(p.action))}</strong>${pill(p.verdict)}</a>${rules.length ? `<span class="tools">${esc(rules.join(" · "))}</span>` : ""}</li>`;
  }).join("")}</ol>`;
}

export function nodePanel(model, id, {cash = null} = {}) {
  if (id === "shield") {
    const f = model.shield.focus;
    const counts = {PASS: 0, BLOCK: 0, NEEDS_OWNER: 0};
    for (const t of model.shield.ticks) if (t.outcome) counts[t.outcome] = (counts[t.outcome] || 0) + 1;
    return `<header class="panel-head"><span class="eyebrow">The shield</span><h2>Every proposal passes 16 checks</h2><p>The agent can only propose. Deterministic code checks each proposal, and payments and refunds also need your signature.</p></header>
      <dl class="tiles"><div><dt>Checked</dt><dd>${esc(model.agent.proposals)}</dd></div><div class="good"><dt>Allowed</dt><dd>${esc(model.agent.allowed)}</dd></div><div class="owner"><dt>Your call</dt><dd>${esc(model.shield.needsOwner)}</dd></div><div class="bad"><dt>Blocked</dt><dd>${esc(model.agent.blocked)}</dd></div></dl>
      ${blockedList(model)}
      ${f ? `<h3 class="panel-sub">${f.state === "NEEDS_OWNER" ? "Waiting for you" : "Latest decision"}: ${esc(actionWord(f.action))} ${pill(f.verdict)}</h3>
      <ul class="rules">${model.shield.ticks.map(t => `<li class="${t.outcome ? tone(t.outcome) : "idle"}" title="${esc(t.code)}"><i aria-hidden="true"></i>${esc(ruleWord(t.code))}<span>${esc(t.outcome ? stateWord(t.outcome) : "Not needed")}</span></li>`).join("")}</ul>
      <a class="button secondary" href="#decision/${esc(f.id)}">Open the full decision</a>` : '<p class="muted">No proposals yet. The ring lights up as the shield checks the first one.</p>'}`;
  }
  if (id === "agent") {
    const list = model.agent.list;
    return `<header class="panel-head"><span class="eyebrow">AI agent</span><h2>Reads PayPal, proposes, never pays</h2><p>Its tools are read-only. Every proposal goes to the shield; nothing it writes can move money by itself.</p></header>
      ${list.length ? `<ol class="feed">${list.slice(0, 7).map(p => `<li><a href="#decision/${esc(p.id)}"><strong>${esc(actionWord(p.action))}</strong>${pill(p.verdict)}</a>${p.agent_trace?.calls?.length ? `<span class="tools">Read: ${p.agent_trace.calls.slice(0, 4).map(c => `<code>${esc(c.tool)}</code>`).join(" ")}</span>` : ""}</li>`).join("")}</ol>` : '<p class="muted">No proposals yet.</p>'}`;
  }
  if (id === "owner") {
    const waiting = model.owner.waiting;
    return `<header class="panel-head"><span class="eyebrow">You, the owner</span><h2>${waiting.length ? "Your signature is needed" : "Nothing waits for you"}</h2><p>You approve an exact payload once. If anything changes before sending, the approval goes stale and the shield asks again.</p></header>
      ${waiting.map(p => `<a class="waiting" href="#decision/${esc(p.id)}"><span class="eyebrow">Waiting</span><strong>${esc(actionWord(p.action))}</strong><span>Review and decide →</span></a>`).join("")}
      <dl class="kvs">${row("Approvals signed", esc(model.owner.approved))}</dl>`;
  }
  if (id === "paypal") {
    if (!cash?.paypal) return `<header class="panel-head"><span class="eyebrow">PayPal balance</span><h2>Not read yet</h2><p>The balance appears after the first PayPal balance check.</p></header>`;
    const p = cash.paypal;
    return `<header class="panel-head"><span class="eyebrow">PayPal balance</span><h2>${esc(short(minor(cash.unreserved)))} free for new payouts</h2><p>What PayPal reports, minus what was already sent and what is promised to approved runs.</p></header>
      <dl class="formula">${row("PayPal available", esc(short(minor(p.available))))}${row("− Sent after the last refresh", esc(short(minor(cash.outflows_after_refresh?.total))))}${row("− Reserved for runs", esc(short(minor(cash.cash_reservations?.total))))}${row("= Unreserved", `<strong>${esc(short(minor(cash.unreserved)))}</strong>`, "total")}</dl>
      <p class="hint">Dispute holds are tracked separately and never subtracted twice. ${p.stale ? "The balance is stale." : "The balance is fresh."}</p><a class="button secondary" href="#cash">Open cash details</a>`;
  }
  if (id === "held") {
    return `<header class="panel-head"><span class="eyebrow">Held back</span><h2>${esc(short(model.held.total))} waits on disputes</h2><p>A disputed order's maker share stays out of payout runs until PayPal settles the dispute.</p></header>
      <dl class="kvs">${model.held.makers.map(m => row(m.name, esc(short(m.held)), "dot-held")).join("")}</dl><a class="button secondary" href="#graph">Open the money graph</a>`;
  }
  return "";
}

const MOMENTS = [
  ["lost_response", "A lost PayPal response", m => m.exactly_one ? `Recovered with the same keys: exactly ${m.payment_count} payment.` : `Recovered: ${m.payment_count} payment(s) recorded.`],
  ["injection", "A refund to another account", m => `Blocked by the shield: ${m.block_rules.map(r => ruleWord(r).toLowerCase()).join("; ")}.`],
  ["settled_retry", "A retry of a paid payout", m => `Blocked by the shield: ${m.block_rules.map(r => ruleWord(r).toLowerCase()).join("; ")}.`],
];

const sentence = text => text.charAt(0).toUpperCase() + text.slice(1);

export function storyPanel({demo = null, comparison = null, model}) {
  const steps = demo?.steps || [];
  const last = steps.filter(s => s.done).at(-1), next = steps.find(s => !s.done);
  const waiting = model.owner.waiting[0];
  let compare = "";
  if (comparison) {
    const naive = minor(comparison.naive.payout), ours = minor(comparison.countersign.total), disputed = minor(comparison.naive.disputed_shares);
    const max = Math.max(naive, ours + disputed, 1);
    compare = `<section class="compare" aria-label="Friday's payout run compared">
      <h3 class="panel-sub">Friday's payout run</h3>
      <div class="bar-row"><span>Naive agent <em>simulated</em></span><div class="bar"><b class="naive" style="--w:${round(100 * naive / max)}%"></b></div><strong>${esc(short(naive))}</strong></div>
      <div class="bar-row"><span>CounterSign <em>recorded</em></span><div class="bar"><b class="ours" style="--w:${round(100 * ours / max)}%"></b><b class="held" style="--w:${round(100 * disputed / max)}%"></b></div><strong>${esc(short(ours))}</strong></div>
      <p class="hint">The naive agent would also pay ${esc(short(disputed))} of disputed shares. CounterSign held them back.</p></section>`;
  }
  const moments = comparison?.moments ? `<ul class="moments">${MOMENTS.map(([key, title, text]) => {
    const m = comparison.moments[key];
    return `<li class="${m ? "done" : ""}"><i aria-hidden="true"></i><div><strong>${esc(title)}</strong><span>${esc(m ? text(m) : "Not reached yet in this week.")}</span></div></li>`;
  }).join("")}</ul>` : "";
  return `<header class="panel-head"><span class="eyebrow">${demo ? `Step ${esc(demo.step)} of ${esc(demo.total)}` : "This week"}</span>
      <h2>${esc(last ? sentence(last.label.replace(/^[^:]+:\s*/, "")) : model.empty ? "An empty marketplace" : "The week so far")}</h2>
      ${next ? `<p><span class="eyebrow inline">Next</span> ${esc(next.label)}</p>` : demo?.complete ? "<p>The staged week is complete.</p>" : ""}</header>
    ${waiting ? `<a class="waiting" href="#decision/${esc(waiting.id)}"><span class="eyebrow">Waiting for your signature</span><strong>${esc(actionWord(waiting.action))}</strong><span>Review and decide →</span></a>` : ""}
    ${compare}${moments ? `<h3 class="panel-sub">Safety moments</h3>${moments}` : ""}
    <p class="hint">Select any node in the money map to explain it. Select a maker to explore its records.</p>`;
}

export function kpis(model) {
  const items = [
    ["paypal", "PayPal available", model.paypal.available],
    ["paid", "Paid to makers", model.totals.paid],
    ["paying", "Scheduled or in flight", model.totals.paying],
    ["held", "Held back", model.held.total],
  ];
  return items.map(([k, label, v]) => `<div class="kpi ${k}"><dt>${esc(label)}</dt><dd data-count="${v == null ? "" : v}">${esc(v == null ? "—" : short(v))}</dd></div>`).join("")
    + `<div class="kpi blocked"><dt>Shield blocks</dt><dd data-plain="1">${esc(model.agent.blocked)}</dd></div>`;
}

export function rail(demo, weekDays) {
  if (!demo?.steps?.length) return "";
  const next = demo.steps.find(s => !s.done);
  return `<ol class="rail" aria-label="Week timeline">${weekDays(demo.steps).map(day => {
    const done = day.steps.every(s => s.done), current = next && day.steps.includes(next);
    return `<li class="${done ? "done" : current ? "current" : ""}"><span class="rail-day">${esc(day.name)}</span><span class="rail-ticks">${day.steps.map(s => `<i class="${s.done ? "done" : s === next ? "next" : ""}" title="${esc(s.label)}"></i>`).join("")}</span></li>`;
  }).join("")}</ol>`;
}
