// C41: recorded proposal stages, never reconstructed money movements or times.
import {esc} from './views.mjs';
import {short} from './scene.mjs';
import {action, rule, state, when} from './words.mjs';

export function chronological(proposals = []) {
  const time = p => Number.isFinite(Date.parse(p.created_at)) ? Date.parse(p.created_at) : Infinity;
  return [...proposals].sort((a,b) => {
    const difference = time(a) - time(b);
    return (Number.isNaN(difference) ? 0 : difference) || String(a.id).localeCompare(String(b.id));
  });
}

export function proposalStages(p) {
  const stopped = p.verdict === 'BLOCK' || ['BLOCKED','BLOCKED_AT_EXECUTION'].includes(p.state);
  // A final recheck can fail after the owner signed. Never erase that history.
  const endsAtShield = stopped && p.state !== 'BLOCKED_AT_EXECUTION' && !p.approval && !p.operation;
  return {
    stopped, endsAtShield,
    shield: p.state === 'BLOCKED_AT_EXECUTION' ? 'Stopped at final recheck' : stopped ? 'Stopped' : p.verdict === 'NEEDS_OWNER' ? 'Owner required' : p.verdict === 'ALLOW' ? 'Allowed' : 'No verdict recorded',
    owner: endsAtShield ? null : p.approval ? 'Signed' : p.state === 'NEEDS_OWNER' ? 'Waiting for you' : ['REJECTED','EXPIRED'].includes(p.state) ? state(p.state) : p.tier === 'AUTONOMOUS' ? 'Not required by policy' : 'No signature recorded',
    paypal: endsAtShield ? null : p.operation ? 'Recorded operation: ' + state(p.operation.state) : stopped ? 'No operation recorded; stopped' : p.state === 'EXECUTED' && ['RESERVE_EARNINGS','RELEASE_RESERVE','DRAFT_MESSAGE','WAIT','DEFER_SCHEDULED_PAYOUT'].includes(p.action) ? 'Internal action; no PayPal write' : 'No operation recorded',
  };
}

export function weekTimeline(demo) {
  if (!demo?.steps?.length) return '<p class="trail-note">Staged week progress is unavailable in this workspace.</p>';
  const current = demo.steps.find(s => s.index === demo.step);
  return `<div class="trail-week-top"><h2>The staged week</h2><span>${esc(current?.label || (demo.step === 0 ? 'Before the first step' : 'Current step unavailable'))}</span><strong>${esc(demo.step)} / ${esc(demo.total)}</strong></div><ol class="trail-week" aria-label="Display-only staged week">${demo.steps.map(s => `<li class="${s.done ? 'done' : ''}${s.index === demo.step ? ' current' : ''}"${s.index === demo.step ? ' aria-current="step"' : ''}><span class="trail-stop" aria-hidden="true">${esc(s.index)}</span><span>${esc(s.label)}</span></li>`).join('')}</ol><p class="trail-note">Use the demo dock to run the next step. The stops above show progress only.</p>`;
}

export function trailRows(model, filter = 'all', selected = '') {
  const list = chronological(model.agent.list).filter(p => filter === 'waiting' ? p.state === 'NEEDS_OWNER' : filter === 'stopped' ? proposalStages(p).stopped : true);
  if (!list.length) return '<p class="trail-empty">No proposals in this view. Recorded proposals will appear as the week runs.</p>';
  return `<ol class="trail-rows">${list.map(p => {
    const stages = proposalStages(p), checks = p.rule_results || [], blocked = checks.filter(r => r.outcome === 'BLOCK');
    const effect = p.expected_effect?.available_change;
    const expected = Number.isSafeInteger(effect?.minor) ? short(effect.minor) + (effect.minor > 0 ? ' expected back' : effect.minor < 0 ? ' expected cash change' : ' expected cash change') : 'Cash effect not recorded';
    return `<li class="trail-row${stages.stopped ? ' stopped' : ''}${p.state === 'NEEDS_OWNER' ? ' needs-owner' : ''}${p.id === selected ? ' selected' : ''}">
      <div class="trail-cell trail-origin"><span class="trail-mobile-label">Proposed</span><button type="button" data-proposal="${esc(p.id)}" aria-pressed="${p.id === selected}"><b>${esc(action(p.action))}</b><span>${esc(p.model === 'deterministic-planner' ? 'Deterministic planner' : p.model || 'Model not recorded')}</span></button><time datetime="${esc(p.created_at || '')}">${esc(when(p.created_at))}</time></div>
      <div class="trail-cell trail-shield"><span class="trail-mobile-label">Shield</span><strong>${esc(stages.shield)}</strong><small>${esc(checks.filter(r => r.outcome === 'PASS').length)} passed · ${esc(checks.filter(r => r.outcome === 'BLOCK').length)} blocked · ${esc(checks.filter(r => r.outcome === 'NEEDS_OWNER').length)} owner</small>${blocked.length ? `<span class="trail-block-reason">${esc(blocked.map(r => rule(r.rule)).join(' · '))}</span>` : ''}</div>
      ${stages.endsAtShield ? '<div class="trail-terminal"><span class="trail-end" aria-hidden="true"></span><b>Stopped at the shield</b><span>No signature or PayPal operation recorded.</span></div>' : `<div class="trail-cell trail-owner"><span class="trail-mobile-label">Owner</span><strong>${esc(stages.owner)}</strong>${p.approval?.approved_at ? `<small>${esc(when(p.approval.approved_at))}</small>` : ''}${p.state === 'NEEDS_OWNER' ? `<a href="#decision/${esc(p.id)}">Review decision <span aria-hidden="true">↗</span></a>` : ''}</div><div class="trail-cell trail-paypal"><span class="trail-mobile-label">PayPal / effect</span><strong>${esc(stages.paypal)}</strong><small class="trail-effect">${esc(expected)}</small></div>`}
    </li>`;
  }).join('')}</ol>`;
}

export function trailDetail(model, selected) {
  const p = model.agent.list.find(p => p.id === selected);
  if (!p) return '<div class="trail-detail-empty"><h3>Follow a proposal</h3><p>Select a proposal name to see its checks, exact parameters and recorded operation.</p></div>';
  return `<header><div><h3>${esc(action(p.action))}</h3><p>Proposal state: ${esc(state(p.state))} · shield verdict: ${esc(state(p.verdict))}</p></div><a class="button secondary" href="#decision/${esc(p.id)}">Open decision <span aria-hidden="true">↗</span></a></header><p class="trail-rationale">${esc(p.rationale || 'No rationale recorded.')}</p><ul class="trail-rules">${(p.rule_results || []).map(r => `<li><span>${esc(rule(r.rule))}<small>${esc(r.detail || 'No detail recorded.')}</small></span><strong>${esc(state(r.outcome))}</strong></li>`).join('')}</ul><details><summary>Parameters and operation</summary><pre>${esc(JSON.stringify({params:p.params || {},operation:p.operation || null},null,2))}</pre></details>`;
}
