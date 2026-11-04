import test from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {annotate, operation, tiles, tableHTML, parseCoverage, pageHTML, liveColumns, coverageColumns, createPaypalCalls, csvCell, MOCK_SOURCE} from "../paypal-calls.mjs";

const coverage=JSON.parse(await readFile(new URL("../results/paypal-coverage.json",import.meta.url),"utf8"));
const row=(id,extra={})=>({id,at:"2026-11-06T09:00:00Z",method:"POST",route:"/v1/payments/payouts",product:"Payouts",direction:"App to PayPal",status:201,source:MOCK_SOURCE,request_id_hash:"abcdef123456",sender_batch_id_hash:"112233445566",...extra});

test("tiles derive counts from the mock document and distinguish missing from zero",()=>{
  assert.match(tiles({counts:{Payouts:7,OAuth:2}}),/>7<\/strong>/);
  assert.match(tiles({counts:{}}),/>0<\/strong>/);
  assert.doesNotMatch(tiles(null),/>0<\/strong>/);
  assert.match(tiles(null),/—/);
  assert.equal((tiles(null).match(/<article>/g)||[]).length,8);
});
test("retry chain joins the lost response with the real duplicate-400 link shape",()=>{
  const result=annotate([row(2,{status:400,original_batch:true,error:"USER_BUSINESS_ERROR"}),row(1,{incomplete:true})]);
  assert.ok(result.every(r=>r.retry_chain));
  assert.equal(result[0].key_hash,result[1].key_hash);
  assert.equal(result[0].evidence,"400 · original batch linked");
  assert.equal(result[1].evidence,"Response lost");
  assert.equal(annotate([row(1)])[0].retry_chain,false);
  assert.equal(operation(row(1,{product:"OAuth"})),"Authentication");
  assert.equal(operation(row(1,{direction:"PayPal to app"})),"Webhook delivery");
});
test("renderers escape text, never render a raw key, and guard CSV formulas",()=>{
  const value=annotate([row(1,{route:'<img onerror="attack()">',product:'<script>bad()</script>',sender_batch_id_hash:"private-key",request_id_hash:null})]);
  const html=tableHTML(value);
  assert.doesNotMatch(html,/<img|<script>|private-key/);assert.match(html,/&lt;img/);
  assert.equal(csvCell("=attack()"),"'=attack()");
  assert.equal(liveColumns().find(c=>c.field==="route").cellRenderer({value:"<svg>"}),"&lt;svg&gt;");
  assert.ok(coverageColumns().every(c=>c.cellRenderer));
});
test("coverage preserves all 1041 source files and refuses invented totals or unsafe links",()=>{
  assert.equal(parseCoverage(coverage).total_attempts,1041);
  assert.throws(()=>parseCoverage({...coverage,total_attempts:1042}));
  const bad=structuredClone(coverage);bad.rows[0].recordings[0].path="javascript:attack()";
  assert.throws(()=>parseCoverage(bad));
  const html=pageHTML(); assert.match(html,/Simulated: PayPal mock/);assert.match(html,/Recorded: real PayPal sandbox/);
  assert.match(html,/Loading calls/);assert.match(html,/Loading committed sandbox coverage/);
});

function dom() {
  const elements=new Map(), timers=[], grids=[];
  const element=()=>({innerHTML:"",textContent:"",hidden:false,disabled:false,checked:false,value:"",className:""});
  const host={...element(),isConnected:true,querySelector(selector){if(!elements.has(selector))elements.set(selector,element());return elements.get(selector);}};
  const document={body:{dataset:{route:"paypal-calls"}},hidden:false,defaultView:{
    setTimeout(fn){timers.push(fn);return timers.length;},clearTimeout(){},agGrid:{createGrid(_host,options){
      const api={rows:options.rowData,destroyed:false,setGridOption(key,value){if(key==="rowData")this.rows=value;},
        forEachNodeAfterFilterAndSort(fn){this.rows.forEach(data=>fn({data}));},destroy(){this.destroyed=true;},exportDataAsCsv(){}};
      grids.push(api);return api;
    }}}};
  return {document,host,elements,timers,grids};
}
test("a failed log read stays unavailable, retry builds its grid, and disposal stops polling",async()=>{
  const d=dom(),requests=[];let unavailable=true;
  const fetcher=async(url,options)=>{requests.push([url,options]); if(url.startsWith("results/"))return{ok:true,json:async()=>coverage}; if(unavailable)return{ok:false};return{ok:true,json:async()=>({source:MOCK_SOURCE,counts:{Payouts:1},total:1,rows:[row(1)]})};};
  const page=createPaypalCalls(d.document,fetcher);await page.mount(d.host);
  assert.match(d.elements.get("[data-calls-status]").textContent,/unavailable/);
  assert.match(d.elements.get("[data-calls-tiles]").innerHTML,/—/);
  unavailable=false;await d.elements.get("[data-calls-refresh]").onclick();
  assert.equal(d.grids.length,2);assert.equal(d.elements.get("[data-calls-export=live]").disabled,false);
  const count=requests.length;page.dispose();await d.timers[0]();assert.equal(requests.length,count);
  assert.ok(d.grids.every(g=>g.destroyed));assert.ok(requests.every(([,options])=>!options.method||options.method==="GET"));
});
