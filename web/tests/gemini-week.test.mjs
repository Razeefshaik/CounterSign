import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {decisionHTML,modelWeekHTML,parseModelEvidence,modelResultsHTML,datasetPickerHTML,mountDatasetPicker} from "../gemini-week.mjs";

const data = JSON.parse(await readFile(new URL("../results/real-model-evidence.json",import.meta.url)));

test("recordings show missing fields and a stopped attempt without invented decisions",()=>{
  const html=modelWeekHTML(data);
  assert.match(html,/Not recorded/);
  assert.match(html,/Gemini via Google/);
  assert.ok(!html.includes("Gemma") && !html.includes("OpenRouter"));
  assert.match(html,/No new week or evaluation has run/);
  assert.match(html,/COMPLETE_WITH_GAPS/);
  assert.ok(!html.includes("Refund was blocked"));
});
test("all recorded free text is escaped and never interpreted as markup",()=>{
  const html=decisionHTML({action:'<img src=x onerror=alert(1)>',rationale:'<script>bad()</script>',
    reads:[{tool:"get_dispute",arguments:{dispute_id:'"<b>unsafe</b>'}}],flags:[]});
  assert.ok(!html.includes("<script>") && !html.includes("<img") && !html.includes("<b>"));
  assert.match(html,/&lt;script&gt;/);
  assert.match(html,/Injection \/ payee-change flags/);
});
test("historical and real dataset labels stay separate; unavailable is never zero",()=>{
  const html=modelResultsHTML(data.datasets[0]);
  assert.match(html,/No score is available/);
  assert.ok(!html.includes("170/240") && !html.includes("215/240"));
  assert.match(datasetPickerHTML(),/Historical · mock · model disabled/);
  assert.match(datasetPickerHTML(),/Real AI agent, mock PayPal/);
  assert.throws(()=>modelResultsHTML({...data.datasets[0],summary:[{pass_rate:0}]}));
});
test("public headings have no model or vendor identity; details remain accurate",()=>{
  const html=modelWeekHTML(data);
  const publicText=html.replace(/<small\b[\s\S]*?<\/small>/g,"");
  assert.ok(!/Gemini|Gemma|OpenRouter|gpt-|codex-cli/.test(publicText));
  assert.match(datasetPickerHTML(),/Real AI agent week/);
});
test("failed stage one remains failed and the canary is never presented as passed",()=>{
  const current=structuredClone(data);
  current.stage_one={...current.stage_one,status:"failed",round_trip:"failed",
    error:"MODEL_NATIVE_TOOL_FORBIDDEN",canary:{status:"not_run"},turns:[{status:"failed"}]};
  const html=modelWeekHTML(current);
  assert.match(html,/Stage 1 stopped: MODEL_NATIVE_TOOL_FORBIDDEN/);
  assert.match(html,/Canary: not_run/);
  assert.ok(!html.includes("Canary: passed"));
  current.stage_one.error='<script>bad()</script>';
  assert.ok(!modelWeekHTML(current).includes("<script>"));
});
test("application attempts are not labelled as successful model turns",()=>{
  const html=modelWeekHTML({...data,stage_one:{...data.stage_one,turns:[{status:"passed"},{status:"failed"}]}});
  assert.match(html,/2 application turn attempts/);
  assert.ok(!html.includes("2 AI agent turns"));
});
test("source validation refuses sandbox, duplicate runs and unsafe links",()=>{
  for(const change of [v=>v.weeks[0].ground="sandbox",v=>v.weeks[0].source="javascript:alert(1)",
    v=>v.weeks.push(v.weeks[0])]){
    const next=structuredClone(data);change(next);assert.throws(()=>parseModelEvidence(next));
  }
});
test("dataset switch toggles whole experiment without changing historical rows",async()=>{
  const picker={value:"real"},target={},historical={hidden:false};
  const host={querySelector:s=>({"[data-c67-dataset]":picker,"[data-c67-results]":target,".c58-explorer":historical}[s])};
  mountDatasetPicker(host,async path=>{assert.equal(path,"results/real-model-evidence.json");return {ok:true,json:async()=>data};});
  await picker.onchange();assert.equal(historical.hidden,true);assert.match(target.innerHTML,/No score is available/);
  picker.value="historical";await picker.onchange();assert.equal(historical.hidden,false);assert.equal(target.hidden,true);
});
