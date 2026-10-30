import test from 'node:test';
import assert from 'node:assert/strict';
import {sceneModel} from '../scene.mjs';
import {nextDecision, decisionBrief, decisionEvidence, decisionFinding, recentDecisions} from '../brief.mjs';
const usd = minor => ({minor,currency:'USD'});
const waiting = {id:'p1',action:'SCHEDULE_PAYOUT_RUN',state:'NEEDS_OWNER',verdict:'NEEDS_OWNER',params:{run_id:'r1'},rule_results:[{rule:'FUNDING',outcome:'PASS',detail:'Enough cash'}]};
const model = proposals => sceneModel({proposals,makers:[{id:'m1',display_name:'Maker A',reserved:usd(36000),payout_email_verified:true}]});
test('decision priority uses current state, with the payout run ahead of other waiting actions', () => {
  const other={...waiting,id:'p2',action:'ACCEPT_CLAIM'};
  assert.equal(nextDecision(model([other,waiting])).id,'p1');
  assert.equal(nextDecision(model([{...waiting,state:'EXECUTED'}])),null);
});
test('exact run recipients distinguish replacement and held money without inventing a total', () => {
  const run={id:'r1',total:usd(108600),lines:[{maker:{id:'m1',display_name:'Maker A'},amount:usd(78600)}],replacements:[{maker:{id:'m1',display_name:'Maker A'},amount:usd(30000),previous_attempt_state:'RETURNED'}]};
  const html=decisionBrief(model([waiting]),run);
  assert.match(html,/\$1,086/);assert.match(html,/Replacement · previous attempt Returned/);
  assert.match(html,/#decision\/p1/);assert.ok(!html.includes('id="approve"'));
  const wrong=decisionBrief(model([waiting]),{...run,id:'r2'});
  assert.ok(!wrong.includes('$1,086'));assert.match(wrong,/Recipient details unavailable/);
  assert.match(decisionEvidence(model([waiting])),/\$360/);
});
test('payloads and names stay escaped; no unchecked approval is inferred', () => {
  const p={...waiting,params:{run_id:'r1',note:'<img onerror=alert(1)>',email:'private@example.com'}};
  const html=decisionBrief(model([p]));
  assert.ok(!html.includes('<img'));assert.ok(!html.includes('private@example.com'));
  assert.match(html,/&lt;img/);assert.match(decisionBrief(model([])),/Nothing needs your signature/);
});
test('recorded history reports rejected and expired states honestly', () => {
  const html=recentDecisions(model([{...waiting,state:'REJECTED'},{...waiting,state:'EXPIRED'}]));
  assert.match(html,/Rejected/);assert.match(html,/Expired/);assert.ok(!html.includes('Signed'));
});
test('finding and holds bind to this exact run rather than aggregate scheduled balances', () => {
  const m=model([waiting]);
  const run={id:'r1',total:usd(108600),reserved_not_paid:usd(41400),reserved:[{maker:{display_name:'Maker B'},amount:usd(5400)}]};
  assert.match(decisionFinding(m,run),/\$1,086 is proposed in this run/);
  assert.match(decisionEvidence(m,run),/\$414/);assert.match(decisionEvidence(m,run),/\$54/);
  assert.ok(!decisionFinding(m,{...run,id:'other'}).includes('$1,086'));
});
