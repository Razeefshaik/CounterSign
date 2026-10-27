import {money, label} from "./data.mjs";
import {rule, action, state as stateWord, kind as kindWord, tone, when, shortId, summarize} from "./words.mjs";

// All API strings, including untrusted buyer text, are escaped and emails masked.
export function esc(value) {
  return String(value ?? "").replace(/[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Z0-9.-]+\.[A-Z]{2,})/gi,
    (email, domain) => email[0] + "***@" + domain)
    .replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
}
const amount = value => value ? esc(money(value)) : "—";
const time = value => value ? `<time datetime="${esc(value)}" title="${esc(value)}">${esc(when(value))}</time>` : "—";
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
// A word with its code in the title, so the contract value stays inspectable.
export const status = value => `<span class="pill ${tone(value)}" title="${esc(value || "")}">${esc(value ? stateWord(value) : "—")}</span>`;
const empty = text => `<p class="empty">${esc(text)}</p>`;
const card = (title, body, extra = "") => `<section class="view-card"${extra}>${title ? `<h3>${esc(title)}</h3>` : ""}${body}</section>`;
const json = value => `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
const table = (headings, rows, numeric = []) => rows.length ? `<div class="table-scroll"><table><thead><tr>${headings.map((h, i) => `<th scope="col"${numeric.includes(i) ? ' class="num"' : ""}>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(cells => `<tr>${cells.map((c, i) => `<td${numeric.includes(i) ? ' class="num"' : ""}>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("No records yet.");
const mark = outcome => {
  const t = tone(outcome);
  return `<span class="mark ${t}" aria-hidden="true">${icon(t === "good" ? "check" : t === "bad" ? "x" : "owner")}</span>`;
};

export function checksList(rules = []) {
  return rules.length
    ? `<ul class="checks">${rules.map(r => `<li>${mark(r.outcome)}<div><span class="check-name">${esc(rule(r.rule))}</span> <code>${esc(r.rule)}</code><span class="check-why">${esc(r.detail)}</span></div><span class="outcome">${esc(r.outcome)}</span></li>`).join("")}</ul>`
    : empty("No rule results recorded.");
}

export function canDecide(proposal, clock) {
  const expires = Date.parse(proposal?.approval_request?.expires_at);
  return proposal?.state === "NEEDS_OWNER" && proposal.verdict === "NEEDS_OWNER" &&
    Boolean(proposal.approval_request?.payload_hash) && Number.isFinite(expires) && expires > Date.parse(clock);
}

// Incident titles carry long PayPal ids and internal "Staged goal:" prefixes.
export function incidentTitle(row) {
  let text = String(row?.title ?? "");
  const goal = /^Staged goal:\s*/i.test(text);
  text = text.replace(/^Staged goal:\s*/i, "").replace(/PP-D-0*([0-9A-F]{6,})/g, (_, id) => "PP-D …" + id.slice(-6));
  return {text: text.charAt(0).toUpperCase() + text.slice(1), goal};
}

export function incidentTable(rows, {staged = () => false, selected = ""} = {}) {
  if (!rows.length) return "";
  return `<table><thead><tr><th scope="col">Incident</th><th scope="col">State</th><th scope="col" class="num">At risk</th><th scope="col" class="hide-sm">Latest check</th><th scope="col" class="hide-sm">Response due</th></tr></thead><tbody>${rows.map(row => {
    const title = incidentTitle(row);
    const verdict = row.latest_proposal?.verdict;
    return `<tr data-id="${esc(row.id)}"${row.id === selected ? ' class="selected"' : ""}>
      <td><button type="button" class="row-title" data-id="${esc(row.id)}"><strong>${esc(title.text)}</strong><span>${esc(kindWord(row.kind))}${title.goal ? ' <span class="staged">demo goal</span>' : ""}${staged(row) ? ' <span class="staged">staged</span>' : ""}</span></button></td>
      <td>${status(row.state)}</td>
      <td class="num">${row.amount_at_risk ? amount(row.amount_at_risk) : "—"}</td>
      <td class="hide-sm">${verdict ? status(verdict) : "—"}</td>
      <td class="hide-sm">${row.deadline ? time(row.deadline) : "—"}</td></tr>`;
  }).join("")}</tbody></table>`;
}

export function ownerCallout(proposal, context = {}) {
  if (!proposal) return "";
  return `<span class="callout-icon" aria-hidden="true">${icon("owner")}</span>
    <div><h2>Waiting for your approval: ${esc(action(proposal.action))}</h2><p>${esc(summarize(proposal, context))}</p></div>
    <a class="button primary" href="#decision/${esc(proposal.id)}">Review and decide${icon("arrow")}</a>`;
}

function traceView(proposal) {
  const planner = proposal.model === "deterministic-planner";
  const calls = proposal.agent_trace?.calls || [];
  const results = proposal.rule_results || [];
  const count = outcome => results.filter(r => r.outcome === outcome).length;
  const read = planner
    ? "<p>Deterministic planner. No AI is involved in building a payout run.</p>"
    : calls.length
      ? `<ul>${calls.map(c => `<li><code>${esc(c.tool)}</code>${c.ok === false ? " (failed)" : ""}</li>`).join("")}</ul>`
      : (proposal.evidence_refs || []).length
        ? `<p>Cited records</p><ul class="refs">${proposal.evidence_refs.map(ref => `<li>${esc(ref)}</li>`).join("")}</ul>`
        : "<p>No records cited.</p>";
  const owner = proposal.approval
    ? `<p>Approved by ${esc(proposal.approval.approved_by)} ${time(proposal.approval.approved_at)}.</p>`
    : proposal.state === "NEEDS_OWNER" ? "<p>Waiting for you.</p>"
    : proposal.tier === "AUTONOMOUS" ? "<p>Not needed: a low-risk action the policy lets the agent take.</p>" : "<p>No approval recorded.</p>";
  const steps = [
    ["Read", planner ? "quiet" : "good", read],
    ["Proposed", "quiet", `<p>${esc(action(proposal.action))}. ${esc(proposal.rationale || "")}</p><p class="muted">Model: ${esc(proposal.model || "unknown")}</p>`],
    ["Shield", tone(proposal.verdict), `<p>${status(proposal.verdict)} ${count("PASS")} passed${count("BLOCK") ? `, ${count("BLOCK")} blocked` : ""}${count("NEEDS_OWNER") ? `, ${count("NEEDS_OWNER")} need you` : ""}.</p>`],
    ["Owner", proposal.approval ? "good" : proposal.state === "NEEDS_OWNER" ? "owner" : "quiet", owner],
    ["Sent to PayPal", proposal.operation ? tone(proposal.operation.state) : "quiet", proposal.operation ? `<p>${esc(label(proposal.operation.kind))}: ${status(proposal.operation.state)}</p>` : "<p>Nothing sent.</p>"],
  ];
  return `<ol class="trace">${steps.map(([title, t, body]) => `<li><span class="dot ${t}" aria-hidden="true"></span><strong>${esc(title)}</strong>${body}</li>`).join("")}</ol>`;
}

export function decisionView(proposal, meta, staged = false, context = {}) {
  if (!proposal) return empty("Choose an incident or proposal to review its decision.");
  const eligible = canDecide(proposal, meta.clock);
  const request = proposal.approval_request;
  const hash = request?.payload_hash || "";
  const rules = proposal.rule_results || [];
  const head = `<div class="page-head enter"><div><h2>${esc(action(proposal.action))}</h2>
      <div class="meta-line"><span>${esc(label(proposal.tier))} tier</span><span aria-hidden="true">·</span><span>Created ${time(proposal.created_at)}</span>${staged ? '<span class="staged">staged</span>' : ""}</div></div>
      <div class="verdict">${status(proposal.verdict)}${stateWord(proposal.state) !== stateWord(proposal.verdict) ? `<span class="muted">${esc(stateWord(proposal.state))}</span>` : ""}</div></div>`;
  const summary = card("", `<p class="summary">${esc(summarize(proposal, context))}</p>${proposal.expected_effect?.available_change ? `<p class="muted" style="margin-top:8px">Change to available cash: <strong>${amount(proposal.expected_effect.available_change)}</strong></p>` : ""}`);
  const flags = proposal.flags?.length ? card("Untrusted instructions found", proposal.flags.map(f => `<blockquote class="flag"><strong>${esc(f.type)}</strong><p>${esc(f.excerpt)}</p><small>${esc(f.source)}</small></blockquote>`).join("")) : "";
  const checks = card(`${rules.length} checks before any money moves`, checksList(rules));
  const ownerPanel = `<section class="view-card owner-panel"><h3>Your decision</h3>
      <p>${eligible ? "Approve this exact proposal once. The executor checks it again before sending." : "This proposal is not waiting for a valid owner decision."}</p>
      ${request ? `<div class="signature" style="margin-top:12px"><span title="${esc(hash)}">${esc(hash.slice(0, 13))}…${esc(hash.slice(-6))}</span><button type="button" class="copy" data-copy="${esc(hash)}" aria-label="Copy payload hash">${icon("copy")}</button></div><p class="muted" style="margin-top:8px">Valid until ${time(request.expires_at)}</p>` : ""}
      ${meta.mode === "live" ? '<label>Owner token<input id="owner-token" type="password" autocomplete="off" placeholder="Used for this request only"></label>' : ""}
      <label>Reason if you reject<textarea id="reject-reason" rows="2" maxlength="2000" ${eligible ? "" : "disabled"}></textarea></label>
      <div class="actions"><button type="button" id="approve" class="primary" ${eligible ? "" : "disabled"}>Approve exact proposal</button><button type="button" id="reject" class="secondary" ${eligible ? "" : "disabled"}>Reject proposal</button></div>
      ${!eligible ? '<p class="muted" style="margin-top:10px">Refresh to check its current state.</p>' : ""}</section>`;
  const tech = `<details class="tech"><summary>Technical details: exactly what the owner signs</summary>
      <dl class="facts"><dt>Proposal</dt><dd class="mono">${esc(proposal.id)}</dd>${hash ? `<dt>Payload hash</dt><dd class="mono">${esc(hash)}</dd>` : ""}${request ? `<dt>Expires</dt><dd class="mono">${esc(request.expires_at)}</dd>` : ""}</dl>
      <h4 style="margin:12px 0 6px">Parameters</h4>${json(proposal.params)}
      ${request ? `<h4 style="margin:12px 0 6px">Record versions</h4>${json(request.record_versions)}` : ""}
      <h4 style="margin:12px 0 6px">Execution</h4>${proposal.operation ? json(proposal.operation) : empty("No operation recorded.")}</details>`;
  return `${head}<div class="decision-grid"><div class="decision-main">${summary}${flags}${checks}${card("", tech)}</div>
    <div class="decision-side">${ownerPanel}${card("How this was decided", traceView(proposal))}</div></div>`;
}

export function runView(run) {
  if (!run) return empty("No payout runs yet.");
  const lines = (run.lines || []).map(line => {
    const orders = (line.obligations || []).map(o => o.order_ref?.startsWith("#") ? o.order_ref : shortId(o.order_ref));
    return `<div class="line"><div><span class="who">${esc(line.maker.display_name)}</span><span class="what">${esc(orders.length)} order share${orders.length === 1 ? "" : "s"}: ${esc(orders.join(", "))}</span></div>${status(line.state)}<span class="amt">${amount(line.amount)}</span></div>`;
  }).join("");
  const resends = (run.replacements || []).map(r => `<div class="line"><div><span class="who">${esc(r.maker.display_name)}</span><span class="what">Resend, attempt ${esc(r.attempt_no)}. The earlier payout is ${esc(stateWord(r.previous_attempt_state).toLowerCase())}.</span></div>${status(r.previous_attempt_state)}<span class="amt">${amount(r.amount)}</span></div>`).join("");
  const held = (run.reserved || []).map(r => `<div class="line"><div><span class="who">${esc(r.maker.display_name)}</span><span class="what">${esc(label(r.reason))}: <span class="mono">${esc(shortId(r.dispute_id))}</span></span></div><span class="pill owner">Held back</span><span class="amt held">${amount(r.amount)}</span></div>`).join("");
  const attempts = (run.lines || []).flatMap(line => (line.attempts || []).map(a => [esc(line.maker.display_name), esc(a.attempt_no), status(a.state), amount(a.amount),
    `<p class="mono">Batch ${esc(a.paypal_batch_id || "pending")}</p><p class="mono">Item ${esc(a.paypal_item_id || "pending")}</p>`,
    `<p class="mono">${esc(a.sender_batch_id)}</p><p class="mono">${esc(a.request_id)}</p>`, time(a.last_checked_at)]));
  return `<div class="page-head enter"><div><h2>Week of ${esc(when(run.week_start + "T00:00:00Z").replace(/, 00:00 UTC$/, ""))}</h2><p class="run-total">${amount(run.total)}</p>
      <div class="meta-line"><span>${esc(run.line_count)} payout line${run.line_count === 1 ? "" : "s"}</span><span aria-hidden="true">·</span><span>${esc(run.replacement_count)} resend${run.replacement_count === 1 ? "" : "s"}</span><span aria-hidden="true">·</span><span>${amount(run.reserved_not_paid)} held back</span></div></div>${status(run.state)}</div>
    ${card("Paying", lines ? `<div class="lines">${lines}</div>` : empty("No payout lines."))}
    ${resends ? card("Resending", `<div class="lines">${resends}</div>`) : ""}
    ${card("Held back, not paid", held ? `<div class="lines">${held}</div><p class="muted" style="margin-top:8px">Total ${amount(run.reserved_not_paid)}. These shares wait until their disputes settle.</p>` : empty("Nothing is held back in this run."))}
    ${card("", `<details class="tech"><summary>PayPal references and idempotency keys</summary>${table(["Maker", "Attempt", "State", "Amount", "PayPal references", "Keys", "Last checked"], attempts, [3])}
      <dl class="facts" style="margin-top:12px"><dt>Run</dt><dd class="mono">${esc(run.id)}</dd>${(run.lines || []).map(l => `<dt>${esc(l.maker.display_name)} line</dt><dd class="mono">${esc(l.id)}</dd>`).join("")}${(run.replacements || []).map(r => `<dt>Resend of</dt><dd class="mono">${esc(r.from_run_id)} / ${esc(r.line_id)}</dd>`).join("")}</dl></details>`)}`;
}

export function auditView(entries) {
  const items = entries.map(e => `<li>${time(e.at)}<div><div class="event"><strong>${esc(label(e.kind))}</strong>${e.verdict ? status(e.verdict) : ""}<span class="actor">by ${esc(e.actor)}</span></div>
    <details><summary>Details</summary><dl class="facts"><dt>Proposal</dt><dd class="mono">${esc(e.proposal_id || "—")}</dd><dt>Operation</dt><dd class="mono">${esc(e.operation_id || "—")}</dd></dl>${json(e.details)}</details></div></li>`);
  return card("Execution history", items.length ? `<p class="muted" style="margin-bottom:8px">Every shield verdict, owner decision and PayPal write, newest first.</p><ol class="timeline">${items.join("")}</ol>` : empty("No records yet."));
}

export function cashView(cash) {
  if (!cash?.paypal) return empty("Cash is unavailable until the first PayPal balance check. Run the first staged step in demo mode.");
  const p = cash.paypal;
  const total = Math.max(1, p.available.minor);
  const reserved = Math.max(0, cash.cash_reservations.total.minor), out = Math.max(0, cash.outflows_after_refresh.total.minor);
  const free = Math.max(0, cash.unreserved.minor);
  const pct = n => (100 * n / total).toFixed(2) + "%";
  const age = Number(p.age_seconds);
  const ageText = Number.isFinite(age) ? `${Math.floor(age / 3600) ? Math.floor(age / 3600) + " h " : ""}${Math.floor(age % 3600 / 60)} min (${esc(p.age_seconds)} s)` : esc(p.age_seconds);
  const kinds = Object.entries(cash.cash_reservations.by_kind).filter(([, value]) => value.minor !== 0);
  return `<div class="page-head enter"><div><h2>Cash available for new commitments</h2><p>What PayPal reports, minus payouts already sent and cash promised to approved or pending runs.</p></div>${status(p.stale ? "STALE" : "FRESH")}</div>
    ${card("", `<div class="cash-hero"><div><span class="muted">Unreserved</span><p class="big">${amount(cash.unreserved)}</p></div><div class="muted">of ${amount(p.available)} available at PayPal</div></div>
      <div class="cash-bar" role="img" aria-label="${esc(`${money(cash.unreserved)} unreserved, ${money(cash.cash_reservations.total)} reserved, ${money(cash.outflows_after_refresh.total)} already sent`)}"><span class="free" style="width:${pct(free)}"></span><span class="reserved" style="width:${pct(reserved)}"></span><span class="out" style="width:${pct(out)}"></span></div>
      <ul class="legend"><li><i style="background:var(--accent)"></i>Unreserved ${amount(cash.unreserved)}</li><li><i style="background:var(--owner)"></i>Reserved for runs ${amount(cash.cash_reservations.total)}</li><li><i style="background:var(--ink-3)"></i>Sent since last refresh ${amount(cash.outflows_after_refresh.total)}</li></ul>`)}
    <div class="split">
    ${card("How unreserved cash is worked out", `<dl class="formula"><dt>PayPal available</dt><dd>${amount(p.available)}</dd><dt>− Sent after the last balance refresh</dt><dd>${amount(cash.outflows_after_refresh.total)}</dd><dt>= Effective available</dt><dd>${amount(cash.effective_available)}</dd><dt>− Reserved for runs and proposals</dt><dd>${amount(cash.cash_reservations.total)}</dd><dt>= Unreserved</dt><dd>${amount(cash.unreserved)}</dd></dl><p class="mono muted" style="margin-top:12px">${esc(cash.formula)}</p>`)}
    ${card("Dispute earmarks · not subtracted from cash", `<p class="large-amount">${amount(cash.earmarks_not_subtracted.dispute)}</p><p class="muted">Maker earnings held back for open disputes. They are tracked separately and never reduce unreserved cash a second time.</p>`)}
    </div>
    <div class="split">
    ${card("Balance freshness", `<dl class="facts"><dt>Last refresh</dt><dd>${time(p.last_refresh_time)}</dd><dt>As of</dt><dd>${time(p.as_of_time)}</dd><dt>Fetched</dt><dd>${time(p.fetched_at)}</dd><dt>Snapshot age</dt><dd>${ageText}</dd></dl><p class="muted" style="margin-top:10px">${p.stale ? "The balance is stale. Review it before committing funds." : "Within the configured freshness policy."}</p>`)}
    ${card("PayPal balance context", `<dl class="facts"><dt>Withheld by PayPal</dt><dd>${amount(p.withheld)}</dd><dt>Total</dt><dd>${amount(p.total)}</dd><dt>Unclaimed payouts</dt><dd>${amount(cash.unclaimed_outstanding)} <span class="muted">(informational)</span></dd></dl>${kinds.length ? `<h4 style="margin:14px 0 6px">Reservations by kind</h4>${table(["Kind", "Amount"], kinds.map(([kind, value]) => [esc(label(kind)), amount(value)]), [1])}` : ""}`)}
    </div>
    ${card("Sent after the last balance refresh", table(["Operation", "Kind", "Amount", "Submitted"], cash.outflows_after_refresh.items.map(o => [`<span class="mono">${esc(shortId(o.operation_id))}</span>`, esc(label(o.kind)), amount(o.amount), time(o.submitted_at)]), [2]))}`;
}

const COLUMNS = {dispute: 0, order: 1, obligation: 2, reservation: 2, maker: 3};
const KIND_NAMES = {dispute: "Dispute", order: "Order", obligation: "Maker share", reservation: "Reservation", maker: "Maker"};

export function graphView(graph) {
  if (!graph.nodes.length) return empty("No linked money records for this selection.");
  const W = 214, H = 70, GAP_X = 70, GAP_Y = 52, PAD = 16;
  const rows = new Map();
  const points = new Map(graph.nodes.map(n => {
    const col = COLUMNS[n.type] ?? 1;
    const row = rows.get(col) || 0; rows.set(col, row + 1);
    return [n.id, {x: PAD + col * (W + GAP_X), y: PAD + 26 + row * (H + GAP_Y), col}];
  }));
  const width = PAD * 2 + 4 * W + 3 * GAP_X;
  const height = PAD * 2 + 26 + Math.max(...rows.values()) * (H + GAP_Y);
  const edges = graph.edges.map(e => {
    const a = points.get(e.from), b = points.get(e.to);
    if (!a || !b) return "";
    let d, lx, ly;
    if (a.col === b.col) {
      const [top, bottom] = a.y < b.y ? [a, b] : [b, a];
      const x = a.x + W / 2;
      d = a.y > b.y ? `M ${x} ${a.y} L ${x} ${b.y + H}` : `M ${x} ${a.y + H} L ${x} ${b.y}`;
      lx = x + 8; ly = (top.y + H + bottom.y) / 2 + 4;
      return `<g><path class="edge" d="${d}" marker-end="url(#arrow)"/><text class="edge-label" x="${lx}" y="${ly}">${esc(String(e.type).replaceAll("_", " "))}</text></g>`;
    }
    const [from, to] = a.x < b.x ? [a, b] : [b, a];
    const x1 = from.x + W, y1 = from.y + H / 2, x2 = to.x, y2 = to.y + H / 2, mid = (x1 + x2) / 2;
    d = a.x < b.x ? `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2 - 2} ${y2}` : `M ${x2} ${y2} C ${mid} ${y2}, ${mid} ${y1}, ${x1 + 2} ${y1}`;
    lx = mid; ly = (y1 + y2) / 2 - 8;
    const text = esc(String(e.type).replaceAll("_", " ")), w = text.length * 6.6 + 12;
    return `<g><path class="edge" d="${d}" marker-end="url(#arrow)"/><rect class="edge-label-bg" x="${lx - w / 2}" y="${ly - 12}" width="${w}" height="18" rx="4"/><text class="edge-label" x="${lx}" y="${ly + 1}" text-anchor="middle">${text}</text></g>`;
  }).join("");
  const headers = ["Dispute", "Order", "Maker share", "Maker"].map((t, i) => `<text class="node-kind" x="${PAD + i * (W + GAP_X)}" y="${PAD + 10}">${t}</text>`).join("");
  const nodes = graph.nodes.map(n => {
    const p = points.get(n.id);
    const raw = String(n.label ?? "").replace(/PP-D-0*([0-9A-F]{6,})/g, (_, id) => "PP-D …" + id.slice(-6));
    const title = /^[0-9A-F-]{20,}$/i.test(raw) ? `${KIND_NAMES[n.type] || label(n.type)} ${shortId(raw)}` : raw;
    const meta = [KIND_NAMES[n.type] || label(n.type), n.state ? stateWord(n.state) : n.type === "maker" ? "Payee" : null].filter(Boolean).join(" · ");
    return `<g class="node ${esc(n.type)}" transform="translate(${p.x} ${p.y})"><title>${esc(n.id)}</title><rect width="${W}" height="${H}" rx="10"/>
      <text class="node-title" x="14" y="27">${esc(String(title).slice(0, 26))}</text><text class="node-meta" x="14" y="50">${esc(meta)}</text>${n.amount ? `<text class="node-amount" x="${W - 14}" y="50" text-anchor="end">${amount(n.amount)}</text>` : ""}</g>`;
  }).join("");
  const svg = `<div class="graph-scroll"><svg class="money-graph" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="graph-title graph-description"><title id="graph-title">Linked money records</title><desc id="graph-description">${esc(graph.edges.map(e => `${e.from} ${e.type} ${e.to}`).join("; "))}</desc><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>${headers}${edges}${nodes}</svg></div>`;
  return card("", `<p class="muted">How a dispute reaches a maker's earnings, and what holds them.</p>${svg}<details class="tech"><summary>Every link as a table</summary>${table(["Record", "Relationship", "Linked record"], graph.edges.map(e => [`<span class="mono">${esc(e.from)}</span>`, esc(e.type), `<span class="mono">${esc(e.to)}</span>`]))}</details>`);
}
