import {money, label} from "./data.mjs";

// All API strings, including untrusted buyer text, are escaped and emails masked.
export function esc(value) {
  return String(value ?? "").replace(/[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Z0-9.-]+\.[A-Z]{2,})/gi,
    (email, domain) => email[0] + "***@" + domain)
    .replace(/[&<>"']/g, c => ({"&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;", "'":"&#39;"}[c]));
}
const amount = value => value ? esc(money(value)) : "—";
const date = value => value ? esc(value.replace("T", " ").replace("Z", " UTC")) : "—";
export const status = value => `<span class="badge ${["PASS", "ALLOW", "SUCCESS"].includes(value) ? "allow" : ["BLOCK", "FAILED"].includes(value) ? "block" : ""}">${esc(value || "—")}</span>`;
const empty = text => `<p class="empty">${esc(text)}</p>`;
const section = (title, body) => `<section class="view-card"><h3>${esc(title)}</h3>${body}</section>`;
const json = value => `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`;
const table = (headings, rows) => rows.length ? `<div class="table-scroll"><table><thead><tr>${headings.map(h => `<th scope="col">${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(cells => `<tr>${cells.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("No records yet.");
export function canDecide(proposal, clock) {
  const expires = Date.parse(proposal?.approval_request?.expires_at);
  return proposal?.state === "NEEDS_OWNER" && proposal.verdict === "NEEDS_OWNER" &&
    Boolean(proposal.approval_request?.payload_hash) && Number.isFinite(expires) && expires > Date.parse(clock);
}
export function decisionView(proposal, meta, staged = false) {
  if (!proposal) return empty("Choose an incident or proposal to review its decision.");
  const eligible = canDecide(proposal, meta.clock);
  return `<div class="view-heading"><div><span class="small-label">${esc(label(proposal.tier))} ACTION ${staged ? '<span class="staged">staged</span>' : ""}</span><h2>${esc(label(proposal.action))}</h2><p>${esc(proposal.rationale)}</p></div>${status(proposal.verdict)}</div>
    ${section("Proposed action", `<p>State: ${esc(label(proposal.state))} · Created ${date(proposal.created_at)}</p><p>Available change: <strong>${amount(proposal.expected_effect?.available_change)}</strong></p>${json(proposal.params)}`)}
    ${section("Shield rule results", table(["Rule", "Outcome", "Why"], (proposal.rule_results || []).map(r => [esc(r.rule), status(r.outcome), esc(r.detail)])))}
    ${section("Flags", proposal.flags?.length ? proposal.flags.map(f => `<blockquote class="flag"><strong>${esc(f.type)}</strong><p>${esc(f.excerpt)}</p><small>${esc(f.source)}</small></blockquote>`).join("") : empty("No flags reported."))}
    ${section("Evidence references", (proposal.evidence_refs || []).length ? `<ul>${proposal.evidence_refs.map(ref => `<li>${esc(ref)}</li>`).join("")}</ul>` : empty("No evidence references."))}
    ${section("Owner decision", `<p>Approve the exact displayed proposal once. The executor checks it again before acting.</p>${proposal.approval_request ? `<dl><dt>Payload hash</dt><dd class="mono">${esc(proposal.approval_request.payload_hash)}</dd><dt>Expires</dt><dd>${date(proposal.approval_request.expires_at)}</dd><dt>Record versions</dt><dd>${json(proposal.approval_request.record_versions)}</dd></dl>` : ""}
      ${meta.mode === "live" ? '<label>Owner token<input id="owner-token" type="password" autocomplete="off" placeholder="Used for this request only"></label>' : ""}
      <label>Reason for rejection<textarea id="reject-reason" rows="2" maxlength="2000" ${eligible ? "" : "disabled"}></textarea></label>
      <div class="actions"><button type="button" id="approve" class="primary" ${eligible ? "" : "disabled"}>Approve exact proposal</button><button type="button" id="reject" class="secondary" ${eligible ? "" : "disabled"}>Reject proposal</button></div>
      ${!eligible ? '<p class="muted">This proposal is not awaiting a valid owner decision. Refresh to check its current state.</p>' : ""}`)}
    ${section("Execution", proposal.operation ? json(proposal.operation) : empty("No operation recorded."))}`;
}

export function runView(run) {
  if (!run) return empty("No payout runs yet.");
  return `<div class="view-heading"><div><span class="small-label">WEEK OF ${esc(run.week_start)}</span><h2>Payout run · ${amount(run.total)}</h2><p>${run.line_count} lines · ${run.replacement_count} replacements</p></div>${status(run.state)}</div>
    ${(run.lines || []).map(line => section(`${line.maker.display_name} · ${money(line.amount)}`, `<p>${esc(line.id)} · ${status(line.state)}</p><h4>Obligations</h4>${table(["Order", "Amount", "State"], line.obligations.map(o => [esc(o.order_ref), amount(o.amount), status(o.state)]))}<h4>Attempts</h4>${table(["Attempt", "State", "Payee", "Amount", "PayPal references", "Idempotency", "Latest check"], line.attempts.map(a => [esc(a.attempt_no), status(a.state), esc(a.payee_email_masked), amount(a.amount), `<p>Batch: ${esc(a.paypal_batch_id || "Pending")}</p><p>Item: ${esc(a.paypal_item_id || "Pending")}</p>`, `<p>Sender: ${esc(a.sender_batch_id)}</p><p>Request: ${esc(a.request_id)}</p>`, date(a.last_checked_at)]))}`)).join("")}
    ${section("Replacements", table(["Maker", "Amount", "Attempt", "Previous state", "Original run / line"], (run.replacements || []).map(r => [esc(r.maker.display_name), amount(r.amount), esc(r.attempt_no), status(r.previous_attempt_state), `${esc(r.from_run_id)}<br>${esc(r.line_id)}`])))}
    ${section("Reserved, not paid", `<p>Total: <strong>${amount(run.reserved_not_paid)}</strong></p>${table(["Maker", "Amount", "Reason", "Dispute"], (run.reserved || []).map(r => [esc(r.maker.display_name), amount(r.amount), esc(r.reason), esc(r.dispute_id)]))}`)}`;
}

export function auditView(entries) {
  return section("Execution history", table(["Time · UTC", "Actor", "Event", "Verdict", "References", "Details"], entries.map(e => [date(e.at), esc(e.actor), esc(label(e.kind)), status(e.verdict), `<p>Proposal: ${esc(e.proposal_id || "—")}</p><p>Operation: ${esc(e.operation_id || "—")}</p>`, json(e.details)])));
}

export function cashView(cash) {
  if (!cash?.paypal) return empty("Cash is unavailable until the first PayPal balance check. Run the first staged step in demo mode.");
  const p = cash.paypal;
  return `<div class="view-heading"><div><h2>Cash available for commitments</h2><p>PayPal balances, recent outflows and active reservations.</p></div>${status(p.stale ? "STALE" : "FRESH")}</div>
    ${section("Balance freshness", `<p>Last refresh: ${date(p.last_refresh_time)}</p><p>As of: ${date(p.as_of_time)} · Fetched: ${date(p.fetched_at)}</p><p>Snapshot age: ${esc(p.age_seconds)} seconds. ${p.stale ? "Balance is stale; review before committing funds." : "Within the configured freshness policy."}</p>`)}
    ${section("Funding formula", `<p class="mono">${esc(cash.formula)}</p><dl class="cash-formula"><dt>PayPal available</dt><dd>${amount(p.available)}</dd><dt>− Outflows after refresh</dt><dd>${amount(cash.outflows_after_refresh.total)}</dd><dt>= Effective available</dt><dd>${amount(cash.effective_available)}</dd><dt>− Active cash reservations</dt><dd>${amount(cash.cash_reservations.total)}</dd><dt>= Unreserved</dt><dd><strong>${amount(cash.unreserved)}</strong></dd></dl>`)}
    ${section("Active cash reservations", table(["Kind", "Amount"], Object.entries(cash.cash_reservations.by_kind).map(([kind, value]) => [esc(label(kind)), amount(value)])))}
    ${section("Dispute earmarks · not subtracted from cash", `<p class="large-amount">${amount(cash.earmarks_not_subtracted.dispute)}</p><p>These reserve maker earnings separately. They do not reduce unreserved cash a second time.</p>`)}
    ${section("PayPal balance context", `<p>Withheld: ${amount(p.withheld)} · Total: ${amount(p.total)}</p><p>Unclaimed outstanding: ${amount(cash.unclaimed_outstanding)} (informational only).</p>`)}
    ${section("Outflows after refresh", table(["Operation", "Kind", "Amount", "Submitted"], cash.outflows_after_refresh.items.map(o => [esc(o.operation_id), esc(o.kind), amount(o.amount), date(o.submitted_at)])))}`;
}

export function graphView(graph) {
  if (!graph.nodes.length) return empty("No linked money records for this selection.");
  const width = 780, height = Math.max(300, graph.nodes.length * 95);
  const points = new Map(graph.nodes.map((n, i) => [n.id, {x: i % 2 ? 430 : 30, y: 25 + i * 95}]));
  const edges = graph.edges.map(e => {
    const a = points.get(e.from), b = points.get(e.to);
    if (!a || !b) return "";
    return `<g><path d="M ${a.x + 150} ${a.y + 54} L ${b.x + 150} ${b.y}" marker-end="url(#arrow)"/><text x="${(a.x + b.x) / 2 + 160}" y="${(a.y + b.y) / 2 + 22}">${esc(e.type)}</text></g>`;
  }).join("");
  return section("Money graph", `<p>Follow the links between disputes, orders, obligations and maker earnings.</p><div class="graph-scroll"><svg class="money-graph" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="graph-title graph-description"><title id="graph-title">Linked money records</title><desc id="graph-description">${esc(graph.edges.map(e => `${e.from} ${e.type} ${e.to}`).join("; "))}</desc><defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z"/></marker></defs>${edges}${graph.nodes.map(n => {
    const p = points.get(n.id);
    return `<g transform="translate(${p.x} ${p.y})"><title>${esc(n.id)}</title><rect width="300" height="66" rx="8"/><text x="14" y="22">${esc(n.label)}</text><text class="node-meta" x="14" y="46">${esc(n.type)} · ${esc(n.state || "—")} · ${amount(n.amount)}</text></g>`;
  }).join("")}</svg></div>${table(["Record", "Relationship", "Linked record"], graph.edges.map(e => [esc(e.from), esc(e.type), esc(e.to)]))}`);
}
