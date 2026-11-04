import test from "node:test";
import assert from "node:assert/strict";
import {savedViews,matchesModel,viewCounts,minorCompare,dateFilterCompare,filteredTotal,csvSafe,layoutKey,readLayout,saveLayout,clearLayout,changedRows} from "../grid-showcase.mjs";
import {columnDefs,gridOptions,incidentRows,auditRows,runRows,makerRows,moneyText,mountGrid} from "../grid.mjs";

test("saved views use Community models and count the same observed rows", () => {
  const rows=[{state:"AWAITING_OWNER",held_minor:5400,verdict:"BLOCK"},{state:"RESOLVED",held_minor:0,verdict:"ALLOW"}];
  assert.deepEqual(viewCounts("incidents",rows),{owner:1,held:1,blocked:1,all:2});
  rows[0]={...rows[0],state:"RESOLVED",held_minor:0};
  assert.deepEqual(viewCounts("incidents",rows),{owner:0,held:0,blocked:1,all:2});
  const audit=[{actor:"shield",verdict:"BLOCK",event_kind:"SHIELD_VERDICT"},{event_kind:"OWNER_COUNTERSIGNED"},{event_kind:"OWNER_APPROVED"},{event_kind:"PAYPAL_RESPONSE"},{actor:"agent",verdict:"BLOCK"}];
  assert.deepEqual(viewCounts("audit",audit),{blocked:1,signed:2,writes:1,all:5});
  assert.deepEqual(viewCounts("runs",[{waiting:"yes"},{state:"RESERVED"},{paid:"yes"}]),{waiting:1,held:1,paid:1,all:3});
  assert.deepEqual(viewCounts("makers",[{held_minor:0,unverified:"no"},{held_minor:20,unverified:"yes"}]),{held:1,unverified:1,all:2});
  assert.throws(() => savedViews("unknown"));
  assert.ok(matchesModel({state:"PAID"},{state:{filter:"paid"}}));
});

test("numeric and UTC day comparators order actual values rather than formatted strings", () => {
  assert.deepEqual([10000,2,900,null,0].sort(minorCompare),[null,0,2,900,10000]);
  assert.equal(minorCompare(Number.MAX_SAFE_INTEGER,Number.MAX_SAFE_INTEGER-1),1);
  const date=new Date(2026,10,6);
  assert.equal(dateFilterCompare(date,"2026-11-06T23:59:59Z"),0);
  assert.equal(dateFilterCompare(date,"2026-11-07T00:00:00Z"),1);
  assert.equal(dateFilterCompare(date,"invalid"),-1);
  for (const view of ["incidents","audit","runs","makers"]) {
    for (const c of columnDefs(view)) assert.equal(c.floatingFilter,true);
    for (const c of columnDefs(view).filter(c => c.field.endsWith("_minor"))) assert.equal(c.comparator(900,10000),-1);
  }
  assert.equal(columnDefs("audit")[0].filter,"agDateColumnFilter");
});

test("filtered totals use only matching records, separate currencies and preserve unavailable money", () => {
  const rows=[{amount_minor:10000,amount_currency:"USD",held_minor:20,held_currency:"USD"},
    {amount_minor:900,amount_currency:"USD"},{amount_minor:2,amount_currency:"EUR"},{amount_minor:null,amount_currency:"USD"}];
  const total=filteredTotal(rows,9,moneyText);
  assert.equal(total.maker,"Showing 4 of 9");assert.equal(total.amount_total,"$109.00 · EUR 0.02");
  assert.equal(total.held_total,"$0.20");
  assert.equal(filteredTotal(rows.slice(1,2),9,moneyText).amount_total,"$9.00");
  assert.equal(filteredTotal([],9,moneyText).amount_total,"—");
  assert.equal(filteredTotal([{amount_minor:Number.MAX_SAFE_INTEGER,amount_currency:"USD"},{amount_minor:1,amount_currency:"USD"}],2,moneyText).amount_total,"Total unavailable");
});

test("CSV neutralises formulas, control prefixes and whitespace while keeping ordinary text", () => {
  for (const value of ["=1+1","+cmd","-2","@SUM(A1)"," =1","\t=1","\rvalue","\nvalue"]) assert.equal(csvSafe(value),"'"+value);
  for (const value of ["Maker A","$4.00",'a,"b"',0,""]) assert.equal(csvSafe(value),String(value));
  for (const view of ["incidents","audit","runs","makers"]) {
    const params=gridOptions(view,[]).defaultCsvExportParams;
    assert.equal(params.exportedRows,"filteredAndSorted");assert.equal(params.skipPinnedBottom,true);
    assert.equal(params.processCellCallback({value:"=evil",column:{getColDef:() => ({})}}),"'=evil");
    assert.equal(params.processCellCallback({value:400,column:{getColDef:() => ({valueFormatter:() => "unused"})},api:{getCellValue:() => "$4.00"}}),"$4.00");
  }
});

test("column layouts are isolated by grid and viewer, validated, and optional", () => {
  const data=new Map(),storage={getItem:k => data.get(k),setItem:(k,v) => data.set(k,v),removeItem:k => data.delete(k)};
  const key=layoutKey("incidents","viewer1");
  assert.notEqual(key,layoutKey("audit","viewer1"));assert.notEqual(key,layoutKey("incidents","viewer2"));
  assert.equal(saveLayout(storage,key,[{colId:"state",width:99999,hide:true,pinned:"script",sort:"desc"}]),true);
  assert.deepEqual(readLayout(storage,key),[{colId:"state",width:700,hide:true,pinned:null,sort:"desc",sortIndex:null}]);
  clearLayout(storage,key);assert.equal(readLayout(storage,key),null);
  data.set(key,"invalid");assert.equal(readLayout(storage,key),null);
  const denied={getItem(){throw Error();},setItem(){throw Error();},removeItem(){throw Error();}};
  assert.equal(readLayout(denied,key),null);assert.equal(saveLayout(denied,key,[]),false);assert.doesNotThrow(() => clearLayout(denied,key));
});

test("live row transactions preserve ids and distinguish adds, changes, removals and unchanged rows", () => {
  const before=[{id:"a",state:"PENDING"},{id:"b",state:"PAID"},{id:"gone"}], after=[{id:"b",state:"PAID"},{id:"a",state:"SUCCESS"},{id:"new"}];
  assert.deepEqual(changedRows(before,after),{add:[after[2]],update:[after[1]],remove:[before[2]]});
  for (const view of ["incidents","audit","runs","makers"]) {
    const options=gridOptions(view,after,{reducedMotion:true});
    assert.equal(options.getRowId({data:after[0]}),"b");assert.equal(options.suppressScrollOnNewData,true);
    assert.equal(options.defaultColDef.enableCellChangeFlash,false);assert.equal(options.cellFlashDuration,0);
    assert.equal(gridOptions(view,after).defaultColDef.enableCellChangeFlash,true);
  }
});

test("held shares, verified status, verdicts and audit types derive from actual API fields", () => {
  const rows=runRows({lines:[{id:"line",state:"SETTLED",maker:{id:"m"},amount:{minor:20,currency:"USD"}}],reserved:[{obligation_id:"o",maker:{id:"m"},amount:{minor:5,currency:"USD"}}]});
  assert.equal(rows[0].paid,"yes");assert.equal(rows[1].id,"held:o");assert.equal(rows[1].state,"RESERVED");
  assert.equal(incidentRows([{id:"i",subject:{dispute_id:"d"},latest_proposal:{verdict:"BLOCK"}}],{disputes:[{id:"d",reserved:{minor:5,currency:"USD"}}]})[0].held_minor,5);
  assert.equal(auditRows([{id:4,kind:"OWNER_APPROVED"}])[0].event_kind,"OWNER_APPROVED");
  assert.equal(makerRows([{id:"m",payout_email_verified:false}],{all:[],makers:[]})[0].unverified,"yes");
});

test("every renderer escapes malicious values, including rule links and pinned total cells", () => {
  const evil='<svg onload="evil()"> private@example.test';
  for (const view of ["incidents","audit","runs","makers"]) for (const c of columnDefs(view)) {
    for (const rowPinned of [null,"bottom"]) {
      const html=c.cellRenderer({value:evil,valueFormatted:evil,colDef:c,node:{rowPinned},data:{kind:evil,rule_code:evil,decision_id:evil,state_label:evil,amount_total:evil,held_total:evil}});
      assert.ok(!html.includes("<svg"));assert.ok(!html.includes("private@example.test"));
    }
  }
});

test("mounted grid refresh uses transactions on the same API with storage denied", async () => {
  let transactions,config,destroyed=false;
  const make=() => ({hidden:false,style:{},dataset:{},children:[],setAttribute(){},append(n){this.children.push(n);},prepend(n){this.children.unshift(n);},remove(){}});
  const scope={};Object.defineProperty(scope,"localStorage",{get(){throw Error("denied");}});
  const host=make();host.ownerDocument={createElement:make,defaultView:scope,body:make()};host.isConnected=true;
  const api={destroy(){destroyed=true;},setGridOption(){},applyTransaction(t){transactions=t;}};
  const first=[{id:"1",state:"PENDING"}],next=[{id:"1",state:"SUCCESS"}];
  const handle=await mountGrid(host,"runs",first,{vendorLoader:async () => ({createGrid:(_,c) => {config=c;return api;}})});
  assert.equal(handle.api,api);assert.equal(handle.update(next),true);assert.equal(destroyed,false);
  assert.deepEqual(transactions,{add:[],update:next,remove:[]});
  let opened;handle.update(next,{onOpen:r => {opened=r;}});
  config.onRowClicked({node:{},data:next[0]});assert.equal(opened,next[0]);
  handle.destroy();assert.equal(destroyed,true);
});
