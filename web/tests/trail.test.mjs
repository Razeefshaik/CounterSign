import test from 'node:test';
import assert from 'node:assert/strict';
import {sceneModel} from '../scene.mjs';
import {chronological, proposalStages, weekTimeline, trailRows, trailDetail} from '../trail.mjs';
const proposal = (id,state='EXECUTED') => ({id,action:'PROVIDE_EVIDENCE',state,verdict:'ALLOW',tier:'AUTONOMOUS',created_at:'2026-11-05T09:00:03Z',rule_results:[]});
test('chronology sorts a copy by recorded creation time and deterministic ties',()=>{
  const list=[{...proposal('z'),created_at:'2026-11-06T00:00:00Z'},proposal('b'),proposal('a'),{...proposal('q'),created_at:null}];
  assert.deepEqual(chronological(list).map(p=>p.id),['a','b','z','q']);assert.equal(list[0].id,'z');
});
test('blocked paths end at the shield and do not infer a signature or request',()=>{
  const p={...proposal('x','BLOCKED'),verdict:'BLOCK'};
  assert.deepEqual(proposalStages(p),{stopped:true,endsAtShield:true,shield:'Stopped',owner:null,paypal:null});
  const html=trailRows(sceneModel({proposals:[p]}));assert.match(html,/Stopped at the shield/);assert.ok(!html.includes('trail-paypal'));
});
test('execution recheck refusals retain recorded signatures and operation facts',()=>{
  const p={...proposal('x','BLOCKED_AT_EXECUTION'),approval:{approved_at:'2026-11-06T09:00:00Z'},operation:{state:'PREPARED'}};
  const stages=proposalStages(p);assert.equal(stages.stopped,true);assert.equal(stages.endsAtShield,false);
  assert.equal(stages.owner,'Signed');assert.match(stages.paypal,/Recorded operation: Prepared/);
  const html=trailRows(sceneModel({proposals:[p]}));assert.match(html,/Stopped at final recheck/);assert.match(html,/Signed/);
  assert.ok(!html.includes('No path to owner approval'));assert.match(html,/Recorded operation: Prepared/);
  assert.equal(proposalStages({...p,operation:null}).paypal,'No operation recorded; stopped');
});
test('allow is not execution, old needs-owner verdicts are not signatures',()=>{
  assert.equal(proposalStages({...proposal('a','ALLOWED')}).paypal,'No operation recorded');
  for(const state of ['REJECTED','EXPIRED']) assert.equal(proposalStages({...proposal('a',state),tier:'OWNER',verdict:'NEEDS_OWNER'}).owner,state==='REJECTED'?'Rejected':'Expired');
  assert.equal(proposalStages({...proposal('b','APPROVED'),approval:{approved_at:'2026-11-06T00:00:00Z'}}).owner,'Signed');
});
test('current stop is the completed current API step, and progress has no jump controls',()=>{
  const html=weekTimeline({step:1,total:2,steps:[{index:1,label:'Monday: orders',done:true},{index:2,label:'Friday: plan',done:false}]});
  assert.match(html,/class="done current" aria-current="step"/);assert.ok(!html.includes('<button'));assert.match(weekTimeline(null),/unavailable/);
});
test('selected evidence, names and payloads remain inert and filters show only their records',()=>{
  const p={...proposal('x'),rationale:'<img src=x onerror=alert(1)>',params:{email:'secret@example.com'}};
  const model=sceneModel({proposals:[p,proposal('waiting','NEEDS_OWNER')]});
  const html=trailDetail(model,'x');assert.ok(!html.includes('<img'));assert.ok(!html.includes('secret@example.com'));
  assert.match(html,/&lt;img/);assert.ok(!trailRows(model,'waiting').includes('data-proposal="x"'));
  assert.match(trailRows(model,'stopped'),/No proposals/);
});
test('cash effects are labelled expected and missing effects are not zero',()=>{
  const p={...proposal('x'),expected_effect:{available_change:{minor:-108600,currency:'USD'}}};
  assert.match(trailRows(sceneModel({proposals:[p]})),/-\$1,086 expected cash change/);
  assert.match(trailRows(sceneModel({proposals:[proposal('y')]})),/Cash effect not recorded/);
});
