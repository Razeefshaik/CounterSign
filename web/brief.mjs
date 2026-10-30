// C40: read-only decision brief. The decision route remains the only signer.
import {esc} from './views.mjs';
import {short} from './scene.mjs';
import {action, rule, state, when} from './words.mjs';

export function nextDecision(model) {
  const waiting = model.owner.waiting;
  return waiting.find(p => p.action === 'SCHEDULE_PAYOUT_RUN') || waiting[0] || null;
}

const money = amount => Number.isSafeInteger(amount?.minor) ? short(amount.minor) : '—';
const arrow = '<svg class="icon" aria-hidden="true"><use href="#i-arrow"/></svg>';

export function decisionFinding(model, run = null) {
  const p = nextDecision(model);
  if (!p) return model.empty ? 'Start the staged week to see the first records.' : 'No proposals are currently waiting for you.';
  return p.action === 'SCHEDULE_PAYOUT_RUN' && run?.id === p.params?.run_id
    ? `${money(run.total)} is proposed in this run. Your signature is still needed.`
    : `${model.owner.waiting.length} decision${model.owner.waiting.length === 1 ? '' : 's'} waiting. Review the exact proposal before signing.`;
}

export function decisionBrief(model, run = null) {
  const p = nextDecision(model);
  if (!p) return `<div class="brief-clear"><svg class="brief-seal" aria-hidden="true"><use href="#i-check"/></svg><h2>Nothing needs your signature.</h2><p>${model.empty ? 'The marketplace is empty. Open the week with the demo dock.' : 'No proposal is currently waiting for an owner decision. Review recorded decisions without moving money.'}</p><a class="button secondary" href="#decision">View decisions${arrow}</a></div>`;
  // A run from any other proposal must never fill this payload's recipients.
  const exact = p.action === 'SCHEDULE_PAYOUT_RUN' && run?.id === p.params?.run_id ? run : null;
  const recipients = exact ? [...(exact.lines || []).map(l => ({...l, purpose:'New payout'})), ...(exact.replacements || []).map(l => ({...l, purpose:'Replacement · previous attempt ' + state(l.previous_attempt_state)}))] : [];
  const hash = p.approval_request?.payload_hash;
  const amount = exact ? money(exact.total) : '—';
  return `<div class="brief-heading"><span class="brief-wait">Your decision</span><span>${esc(model.owner.waiting.length)} waiting</span></div>
    <div class="brief-question"><h2>${esc(action(p.action))}</h2><strong class="brief-total">${esc(amount)}</strong></div>
    <p class="brief-summary">${exact ? 'Review these recipients and the held shares, then sign the exact proposal.' : 'Review the exact parameters and checks before deciding. No total has been inferred.'}</p>
    ${recipients.length ? `<div class="brief-recipient-head"><span>Recipient / purpose</span><span>Amount</span></div><ul class="brief-recipients">${recipients.map(l => {
      const maker = model.makers.find(m => m.id === l.maker?.id);
      return `<li><div><b>${esc(l.maker?.display_name || 'Unnamed maker')}</b><span>${esc(l.purpose)}${maker ? ' · ' + (maker.verified ? 'Verified payee' : 'Payee not verified') : ''}</span></div><strong>${esc(money(l.amount))}</strong></li>`;
    }).join('')}</ul>` : p.action === 'SCHEDULE_PAYOUT_RUN' ? '<p class="brief-unavailable">Recipient details unavailable. Open the decision to reload the exact run.</p>' : ''}
    <details class="brief-payload"><summary>Inspect exact payload</summary><pre>${esc(JSON.stringify(p.params || {}, null, 2))}</pre>${hash ? `<dl><dt>Payload hash</dt><dd>${esc(hash)}</dd><dt>Expires</dt><dd>${esc(p.approval_request.expires_at || '—')}</dd></dl>` : '<p>No approval request recorded.</p>'}</details>
    <div class="brief-sign"><div><b>Your signature is still needed.</b><span>${hash ? 'Bound to this payload; checked again before sending.' : 'The decision view checks whether approval is available.'}</span></div><a class="button primary" href="#decision/${esc(p.id)}">Review exact proposal${arrow}</a></div>`;
}

export function decisionEvidence(model, run = null) {
  const p = nextDecision(model);
  const held = model.held;
  const exact = p?.action === 'SCHEDULE_PAYOUT_RUN' && run?.id === p.params?.run_id ? run : null;
  const holds = exact ? (exact.reserved || []).map(r => ({name:r.maker?.display_name || 'Unnamed maker', amount:money(r.amount)})) : held.makers.map(m => ({name:m.name,amount:short(m.held)}));
  const total = exact ? money(exact.reserved_not_paid) : short(held.total);
  return `<header class="ov-card-head"><div><h2>Why this amount?</h2><p>Evidence beside your decision.</p></div></header>
    <div class="brief-hold"><span>${exact ? 'Kept out of this run' : 'Currently held for disputes'}</span><strong>${esc(total)}</strong></div>
    ${holds.length ? `<ul class="brief-holds">${holds.map(m => `<li><span>${esc(m.name)}</span><strong>${esc(m.amount)}</strong></li>`).join('')}</ul><p class="brief-note">${exact ? 'Recorded with this run: disputed shares are excluded from its recipients.' : 'These shares stay out of payouts while their disputes are open.'}</p>` : `<p class="brief-note">${exact ? 'No held-share details recorded with this run.' : 'No disputed shares are currently held.'}</p>`}
    <h3 class="brief-check-heading">${p ? 'Checks on this proposal' : 'No pending proposal to check'}</h3>
    ${p?.rule_results?.length ? `<ul class="brief-checks">${p.rule_results.map(r => `<li class="${r.outcome === 'BLOCK' ? 'stop' : r.outcome === 'NEEDS_OWNER' ? 'ask' : 'quiet'}"><span><b>${esc(rule(r.rule))}</b><small>${esc(r.detail || 'No detail recorded.')}</small></span><strong>${esc(state(r.outcome))}</strong></li>`).join('')}</ul>` : '<p class="brief-note">Checks appear when a proposal has recorded results.</p>'}`;
}

export function recentDecisions(model) {
  const list = model.agent.list.filter(p => p.action !== 'WAIT').slice(0, 4);
  return list.length ? `<ol class="brief-history">${list.map(p => `<li><a href="#decision/${esc(p.id)}"><span><b>${esc(action(p.action))}</b><small>${esc(when(p.created_at))}</small></span><strong class="${p.state === 'NEEDS_OWNER' ? 'ask' : ''}">${esc(state(p.state))}</strong>${arrow}</a></li>`).join('')}</ol>` : '<p class="empty-note">No proposals recorded yet.</p>';
}
