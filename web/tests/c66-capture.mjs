// C66: dependency-free browser integration, using the same headless CDP approach as C50.
// Only the isolated preview on 8200 is permitted. --week steps its disposable mock demo.
import {spawn} from "node:child_process";
import {mkdtemp,readFile,writeFile,mkdir,rm,rename} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve,dirname,basename} from "node:path";

const origin="http://127.0.0.1:8200", url=origin+"/#incidents";
const destination=resolve(process.argv[2] || "design-demos/reference/c66");
const week=process.argv.includes("--week");
await mkdir(destination,{recursive:true});
const profile=await mkdtemp(join(tmpdir(),"countersign-c66-browser-"));
const browser=spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",[
  "--headless=new","--remote-debugging-port=0",`--user-data-dir=${profile}`,
  "--no-first-run","--no-default-browser-check","about:blank",
],{windowsHide:true,stdio:"ignore"});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let socket;
try {
  let port;
  for(let i=0;i<200;i++){try{port=(await readFile(join(profile,"DevToolsActivePort"),"utf8")).split("\n")[0];break;}catch{await delay(50);}}
  if(!port)throw Error("C66_BROWSER_NOT_STARTED");
  const tab=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:"PUT"})).json();
  socket=new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  let sequence=0;const pending=new Map(),exceptions=[],warnings=[],requests=[],downloads=[];
  socket.onmessage=event=>{
    const message=JSON.parse(event.data);
    if(message.id){const p=pending.get(message.id);pending.delete(message.id);message.error?p.reject(Error("C66_CDP_ERROR")):p.resolve(message.result);}
    else if(message.method==="Runtime.exceptionThrown")exceptions.push("runtime exception");
    else if(message.method==="Runtime.consoleAPICalled" && ["error","warning"].includes(message.params.type))warnings.push(message.params.args.map(a=>a.value||a.description||"").join(" "));
    else if(message.method==="Network.requestWillBeSent")requests.push({method:message.params.request.method,url:message.params.request.url});
    else if(message.method==="Browser.downloadProgress" && message.params.state==="completed")downloads.push(message.params.guid);
  };
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error("C66_PAGE_EVALUATION_FAILED: "+(r.exceptionDetails.exception?.description || r.exceptionDetails.text));return r.result?.value;};
  const wait=async(expression,label)=>{for(let i=0;i<250;i++){if(await evaluate(expression))return;await delay(80);}throw Error("C66_WAIT_FAILED: "+label);};
  const check=(value,label)=>{if(!value)throw Error("C66_ASSERTION_FAILED: "+label);};
  const capture=async name=>{const shot=await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});await writeFile(join(destination,name+".png"),Buffer.from(shot.data,"base64"));};
  await call("Runtime.enable");await call("Page.enable");await call("Network.enable");
  await call("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"reduce"}]});
  // Test-only interception retains real Community APIs; production adds no debug hooks.
  await call("Page.addScriptToEvaluateOnNewDocument",{source:`(() => {
    window.__c66Grids=new Map();let library;
    Object.defineProperty(window,"agGrid",{configurable:true,get:()=>library,set:value => {
      library=new Proxy(value,{get:(target,key) => key === "createGrid" ? (host,options) => {
        const api=target.createGrid(host,options);window.__c66Grids.set(host.getAttribute("aria-label"),{api,host,options});return api;
      } : target[key]});
    }});
  })()`});
  await call("Emulation.setDeviceMetricsOverride",{width:1440,height:1080,deviceScaleFactor:1,mobile:false});
  const checks=[];
  const go=async (view,route) => {
    await evaluate(`location.hash=${JSON.stringify(route)}`);
    await wait(`document.body.dataset.route === ${JSON.stringify(route.replace("#","").split("/")[0])} && document.querySelector('[data-c49-grid="${view}"]')?.dataset.gridState === 'ready'`,view+" ready");
    await evaluate(`document.querySelector('[data-c49-grid="${view}"]').scrollIntoView({block:'center',behavior:'instant'})`);
    await delay(150);
  };
  await call("Page.navigate",{url});
  await wait("document.querySelector('[data-c49-grid=incidents]')?.dataset.gridState === 'ready'","initial incidents");
  await evaluate("document.fonts.ready");
  for(const [view,route,label] of [["incidents","#incidents","Incident desk"],["audit","#audit","Execution history"],["runs","#runs","Payout run lines"],["makers","#overview","Makers"]]){
    await go(view,route);
    await evaluate("document.documentElement.dataset.theme='light'");
    const initial=await evaluate(`(() => {const g=window.__c66Grids.get(${JSON.stringify(label)});return {
      rows:g.api.getDisplayedRowCount(),cols:g.api.getColumnDefs().filter(c=>!c.hide).length,
      floating:document.querySelector('[data-c49-grid="${view}"] .ag-floating-filter') !== null,
      total:document.querySelector('[data-c49-grid="${view}"] [data-c66-count]').textContent,
      overflow:document.documentElement.scrollWidth>innerWidth};})()`);
    check(initial.rows>0 && initial.floating && !initial.overflow,view+" rows/floating filters/overflow");
    await capture(view+"-light");
    // Compare every saved model count with the real filtered Community row model.
    const chips=await evaluate(`[...document.querySelectorAll('[data-c49-grid="${view}"] [data-c66-view]')].map(b=>({id:b.dataset.c66View,count:Number(b.textContent.split('(').at(-1).split(')')[0])}))`);
    for(const chip of chips){
      await evaluate(`document.querySelector('[data-c49-grid="${view}"] [data-c66-view="${chip.id}"]').click()`);
      await wait(`window.__c66Grids.get(${JSON.stringify(label)}).api.getDisplayedRowCount() === ${chip.count}`,view+" "+chip.id+" count");
      check(await evaluate(`document.querySelector('[data-c49-grid="${view}"] [data-c66-view="${chip.id}"]').getAttribute('aria-pressed') === 'true'`),"active chip");
      check(await evaluate(`document.querySelector('[data-c49-grid="${view}"] [data-c66-count]').textContent.startsWith('Showing ${chip.count} of ')`),"filtered pinned summary");
    }
    await evaluate(`document.querySelector('[data-c49-grid="${view}"] [data-c66-view=all]').click()`);
    // Real native column models, numeric ordering, retained layout and CSV formatting.
    await evaluate(`(() => {const api=window.__c66Grids.get(${JSON.stringify(label)}).api;api.applyColumnState({state:[{colId:'amount_minor',sort:'desc',width:167}],defaultState:{sort:null}});api.dispatchEvent({type:'columnResized',finished:true});})()`);
    check(await evaluate(`localStorage.getItem('countersign:c66:layout:local-viewer:${view}')?.includes('167')`),view+" layout stored");
    await evaluate(`window.__c66Grids.get(${JSON.stringify(label)}).api.setFilterModel({amount_minor:{filterType:'number',type:'greaterThan',filter:0}})`);
    const exportedRows=await evaluate(`window.__c66Grids.get(${JSON.stringify(label)}).api.getDisplayedRowCount()`);
    const downloadPath=join(destination,"downloads",view);await mkdir(downloadPath,{recursive:true});
    await call("Browser.setDownloadBehavior",{behavior:"allowAndName",downloadPath,eventsEnabled:true});
    const before=downloads.length;
    await evaluate(`document.querySelector('[data-c49-grid="${view}"] [data-c66-export]').click()`);
    for(let i=0;i<100 && downloads.length===before;i++)await delay(80);
    check(downloads.length===before+1,view+" real CSV");
    const csv=await readFile(join(downloadPath,downloads.at(-1)),"utf8");
    await rename(join(downloadPath,downloads.at(-1)),join(downloadPath,"export.csv"));
    check(!csv.includes("Showing "),view+" export excludes totals");
    check(csv.trim().split(/\r?\n/).length===exportedRows+1,view+" CSV rows");
    await evaluate(`document.querySelector('[data-c49-grid="${view}"] [data-c66-view=all]').click()`);
    // Enter uses the existing record surface; actual key dispatch, not calling a handler.
    await evaluate(`(() => {const api=window.__c66Grids.get(${JSON.stringify(label)}).api;let index=0;
      if(${JSON.stringify(view)}==='audit'){api.forEachNodeAfterFilterAndSort(n => {if(n.data.decision_id && !api.getDisplayedRowAtIndex(index)?.data.decision_id)index=n.rowIndex;});}
      api.paginationGoToPage(Math.floor(index/10));api.ensureIndexVisible(index);api.setFocusedCell(index,api.getAllDisplayedColumns()[0]);})()`);
    await call("Input.dispatchKeyEvent",{type:"keyDown",key:"Enter",code:"Enter",windowsVirtualKeyCode:13});
    await call("Input.dispatchKeyEvent",{type:"keyUp",key:"Enter",code:"Enter",windowsVirtualKeyCode:13});
    await delay(150);
    const entered=await evaluate(view==="makers" ? "document.querySelector('#maker-dialog').open" : view==="runs" ? "document.querySelector('#view-body details.tech').open" : view==="audit" ? "location.hash.startsWith('#decision/')" : "document.querySelector('#detail-title').textContent.length>0");
    check(entered,view+" Enter opens existing details");
    if(view==="makers")await evaluate("document.querySelector('#maker-dialog').close()");
    await go(view,route);
    // Refresh must retain the exact grid and native filter/sort models.
    await evaluate(`window.__c66Before=window.__c66Grids.get(${JSON.stringify(label)}).api;window.__c66Before.applyColumnState({state:[{colId:'amount_minor',sort:'desc'}]});`);
    await evaluate(view==="incidents" || view==="makers" ? "document.querySelector('#refresh').click()" : "document.querySelector('#view-refresh').click()");
    await delay(400);
    check(await evaluate(`window.__c66Grids.get(${JSON.stringify(label)}).api===window.__c66Before && window.__c66Before.getColumnState().find(c=>c.colId==='amount_minor').sort==='desc'`),view+" refresh retains API and sort");
    await evaluate(`document.querySelector('[data-c49-grid="${view}"] [data-c66-reset]').click()`);
    checks.push({view,...initial,saved_views:chips,csv:true,exported_rows:exportedRows,enter:true,refresh:true});
  }
  await go("incidents","#incidents");
  await evaluate("document.documentElement.dataset.theme='dark'");await capture("incidents-dark");
  await call("Emulation.setDeviceMetricsOverride",{width:390,height:844,deviceScaleFactor:1,mobile:true});
  await evaluate("document.documentElement.dataset.theme='light';document.querySelector('[data-c49-grid=incidents]').scrollIntoView({block:'center',behavior:'instant'})");await delay(150);
  check(await evaluate("document.documentElement.scrollWidth<=innerWidth"),"390px no page overflow");
  check(await evaluate("document.querySelector('[data-c49-grid=incidents] .ag-pinned-left-cols-container .ag-cell') !== null"),"important column pinned");
  await capture("incidents-phone");
  for(const [view,route] of [["audit","#audit"],["runs","#runs"],["makers","#overview"]]){
    await go(view,route);
    check(await evaluate("document.documentElement.scrollWidth<=innerWidth"),view+" 390px no overflow");
    check(await evaluate(`document.querySelector('[data-c49-grid="${view}"] .ag-pinned-left-cols-container .ag-cell') !== null`),view+" important column pinned on phone");
    checks.push({view,phone_no_overflow:true,pinned:true});
  }
  await go("incidents","#incidents");
  if(week){
    // The only mutations are the existing scripted demo controls on our disposable container.
    const progress=await evaluate("fetch('/api/v1/demo/state').then(r=>r.json())");check((progress.step_index ?? progress.step)===6,"Friday boot step six");
    await call("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"no-preference"}]});
    await evaluate(`window.__c66Flash=false;window.__c66Observer=new MutationObserver(() => {if(document.querySelector('.ag-cell-data-changed'))window.__c66Flash=true;});window.__c66Observer.observe(document.body,{attributes:true,subtree:true,attributeFilter:['class']});window.__c66Live=window.__c66Grids.get('Incident desk').api;`);
    for(let step=7;step<=11;step++){
      await evaluate("document.querySelector('#demo-step').click()");
      await wait(`(${step}===11 || !document.querySelector('#demo-step')?.disabled) && document.querySelector('#demo-bar').textContent.includes('${step}/11')`,"week step "+step);
      await wait("!document.querySelector('#refresh').disabled", "week refresh "+step);await delay(300);
    }
    check(await evaluate("window.__c66Grids.get('Incident desk').api===window.__c66Live"),"week retains incident grid");
    check(await evaluate("window.__c66Flash"),"real cell change flash during week");
    checks.push({friday_to_end:true,incident_grid_retained:true,change_flash:true});
  }
  // Storage denial must not prevent a fresh grid or its layout reset from rendering.
  await call("Page.addScriptToEvaluateOnNewDocument",{source:"Object.defineProperty(window,'localStorage',{get(){throw new DOMException('Denied','SecurityError');}})"});
  await call("Page.navigate",{url:origin+"/?c66=storage-denied#incidents"});
  await wait("document.querySelector('[data-c49-grid=incidents]')?.dataset.gridState==='ready'","storage denied still renders");
  await evaluate("document.querySelector('[data-c49-grid=incidents] [data-c66-reset]').click()");
  checks.push({storage_denied_render_and_reset:true});
  check(exceptions.length===0,"zero runtime exceptions");
  check(warnings.length===0,"zero console errors/warnings");
  await writeFile(join(destination,"browser.json"),JSON.stringify({origin,week,checks,runtime_exceptions:exceptions,console_warnings:warnings,non_get_requests:requests.filter(r=>r.method!=="GET")},null,2)+"\n");
  console.log("C66 browser checks passed: four Community grids, saved views, totals, CSV, Enter, refresh, light/dark/phone");
}finally{
  socket?.close();browser.kill();await delay(500);
  if(dirname(resolve(profile))!==resolve(tmpdir()) || !basename(profile).startsWith("countersign-c66-browser-"))throw Error("C66_PROFILE_PATH_INVALID");
  await rm(profile,{recursive:true,force:true,maxRetries:10,retryDelay:200});
}
