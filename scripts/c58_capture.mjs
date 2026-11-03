// C58: dependency-free browser integration, using the same headless CDP approach as C50.
// Only the isolated fixture preview on 8197 is permitted. No operational POSTs.
import {spawn} from "node:child_process";
import {mkdtemp,readFile,writeFile,mkdir,rm,rename} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve,dirname,basename} from "node:path";

const origin="http://127.0.0.1:8197", url=origin+"/#replay";
const destination=resolve(process.argv[2] || "design-demos/reference/c58");
await mkdir(destination,{recursive:true});
const profile=await mkdtemp(join(tmpdir(),"countersign-c58-browser-"));
const browser=spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",[
  "--headless=new","--remote-debugging-port=0",`--user-data-dir=${profile}`,
  "--no-first-run","--no-default-browser-check","about:blank",
],{windowsHide:true,stdio:"ignore"});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let socket;
try {
  let port;
  for(let i=0;i<200;i++){try{port=(await readFile(join(profile,"DevToolsActivePort"),"utf8")).split("\n")[0];break;}catch{await delay(50);}}
  if(!port)throw Error("C58_BROWSER_NOT_STARTED");
  const tab=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:"PUT"})).json();
  socket=new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  let sequence=0;const pending=new Map(),exceptions=[],warnings=[],requests=[],downloads=[];
  socket.onmessage=event=>{
    const message=JSON.parse(event.data);
    if(message.id){const p=pending.get(message.id);pending.delete(message.id);message.error?p.reject(Error("C58_CDP_ERROR")):p.resolve(message.result);}
    else if(message.method==="Runtime.exceptionThrown")exceptions.push("runtime exception");
    else if(message.method==="Runtime.consoleAPICalled" && ["error","warning"].includes(message.params.type))warnings.push(message.params.args.map(a=>a.value||a.description||"").join(" "));
    else if(message.method==="Network.requestWillBeSent")requests.push({method:message.params.request.method,url:message.params.request.url});
    else if(message.method==="Browser.downloadProgress" && message.params.state==="completed")downloads.push(message.params.guid);
  };
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error("C58_PAGE_EVALUATION_FAILED: "+r.exceptionDetails.text);return r.result?.value;};
  const wait=async(expression,label)=>{for(let i=0;i<250;i++){if(await evaluate(expression))return;await delay(80);}throw Error("C58_WAIT_FAILED: "+label);};
  const check=(value,label)=>{if(!value)throw Error("C58_ASSERTION_FAILED: "+label);};
  const capture=async name=>{const shot=await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});await writeFile(join(destination,name+".png"),Buffer.from(shot.data,"base64"));};
  await call("Runtime.enable");await call("Page.enable");await call("Network.enable");
  await call("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"reduce"}]});
  const checks=[];
  for(const [name,width,height,theme] of [["light",1440,1080,"light"],["dark",1440,1080,"dark"],["phone",390,844,"light"]]){
    await call("Emulation.setDeviceMetricsOverride",{width,height,deviceScaleFactor:1,mobile:width<500});
    await call("Page.navigate",{url:`${origin}/?c58=${name}#replay`});
    await wait("document.querySelector('.c58-grid .ag-root-wrapper') && document.querySelector('.c58-count')?.textContent.startsWith('480 of 480')","grid ready");
    await evaluate(`document.documentElement.dataset.theme !== ${JSON.stringify(theme)} && document.querySelector('#theme').click()`);
    await evaluate("document.fonts.ready");await delay(200);
    const initial=await evaluate(`({theme:document.documentElement.dataset.theme,overflow:document.documentElement.scrollWidth>innerWidth,
      banner:document.querySelector('#simulation').textContent,summary:document.querySelector('.c58-summary').textContent,
      columns:document.querySelectorAll('.c58-grid .ag-header-cell').length,reduced:matchMedia('(prefers-reduced-motion: reduce)').matches,
      label:document.querySelector('.c58-explorer').textContent.includes('Not a real-model or sandbox run')})`);
    check(initial.theme===theme && !initial.overflow && initial.banner.includes("Simulated: mock PayPal") && initial.reduced && initial.label,"theme/phone/banner/disclosure");
    check(initial.summary.includes("170/240") && initial.summary.includes("215/240") && initial.summary.includes("34/48") && initial.summary.includes("43/48"),"computed scoreboard");
    check(await evaluate("document.querySelector('.c58-grid .ag-paging-panel').textContent.includes('25')"),"pagination25");
    await capture(name+"-all");
    for(const [id,count] of [["failed",70],["comparison",30],["invariants",0]]){
      await evaluate(`document.querySelector('[data-c58-preset="${id}"]').click()`);
      await wait(`document.querySelector('.c58-count').textContent.startsWith('${count} of 480')`,id+" count");
      await wait(`document.querySelector('.c58-grid .ag-floating-bottom [col-id="scenario_id"]')?.textContent==='${count} runs'`,id+" filtered pinned total");
      if(!count)await wait("document.querySelector('.c58-grid .cs-grid-empty')?.textContent.includes('No run violated an invariant')","honest empty state");
      await evaluate("document.querySelector('.c58-presets').scrollIntoView({block:'center',behavior:'instant'})");
      await capture(name+"-"+id);
      await evaluate("document.querySelector('.c58-grid').scrollIntoView({block:'center',behavior:'instant'})");
      check(await evaluate("Boolean(document.elementFromPoint(innerWidth/2,12)?.closest('#simulation'))"),"banner uncovered after scroll");
      await capture(name+"-"+id+"-grid");
    }
    await evaluate("document.querySelector('[data-c58-preset=all]').click();document.querySelector('[data-c58-system=countersign]').click()");
    await wait("document.querySelector('.c58-count').textContent.startsWith('240 of 480')","external chip");
    await evaluate("{const input=document.querySelector('[data-c58-search]');input.value='F01_timeout_after_payout_created';input.dispatchEvent(new Event('input',{bubbles:true}));}");
    await wait("document.querySelector('.c58-count').textContent.startsWith('5 of 480')","quick filter");
    await evaluate("document.querySelector('[data-c58-preset=failed]').click()");
    const downloadPath=join(destination,"downloads",name);await mkdir(downloadPath,{recursive:true});
    await call("Browser.setDownloadBehavior",{behavior:"allowAndName",downloadPath,eventsEnabled:true});
    const previous=downloads.length;await evaluate("document.querySelector('[data-c58-export]').click()");
    for(let i=0;i<100 && downloads.length===previous;i++)await delay(80);
    check(downloads.length>previous,"genuine Community CSV download");
    check(downloads.length===previous+1,"one CSV download");
    const downloaded=join(downloadPath,downloads.at(-1));
    const csv=await readFile(downloaded,"utf8");
    await rename(downloaded,join(downloadPath,"export.csv"));
    check(csv.trim().split(/\r?\n/).length===71 && !csv.includes("Filtered totals"),"CSV 70 filtered rows, not pinned totals");
    // A sample without an action trace opens only a metrics panel.
    await evaluate("document.querySelector('.c58-grid .ag-center-cols-container .ag-row .ag-cell').click()");
    await wait("!document.querySelector('.c58-detail').hidden","metrics detail");
    check(await evaluate("document.querySelector('.c58-detail').textContent.includes('No action trace is bundled')"),"no invented trace");
    await capture(name+"-details");
    // F01 exists in the separate, already-bundled action evidence.
    await evaluate("document.querySelector('[data-c58-preset=all]').click();{const input=document.querySelector('[data-c58-search]');input.value='F01_timeout_after_payout_created';input.dispatchEvent(new Event('input',{bubbles:true}));}");
    await wait("document.querySelector('.c58-count').textContent.startsWith('10 of 480')","trace selection filter");
    await evaluate("document.querySelector('.c58-grid .ag-center-cols-container .ag-row .ag-cell').click()");
    await wait("document.querySelector('#view-notice').textContent.includes('Loaded the available')","existing trace selected");
    check(await evaluate("document.querySelector('#replay-scenario').value==='F01_timeout_after_payout_created'"),"existing replay selection");
    checks.push({name,width,height,...initial,presets:{failed:70,comparison:30,invariants:0},csv_rows:70,metrics_detail:true,existing_trace:true});
  }
  check(exceptions.length===0,"zero runtime exceptions");
  check(warnings.length===0,"zero console errors or warnings");
  check(!requests.some(r=>r.method!=="GET"),"read-only preview");
  await writeFile(join(destination,"browser.json"),JSON.stringify({origin,evidence:"Actual fixture dashboard with committed 480-run historical bundle; reduced motion; no operational writes",checks,runtime_exceptions:exceptions,console_warnings:warnings,non_get_requests:requests.filter(r=>r.method!=="GET")},null,2)+"\n");
  console.log("C58: three themes/viewports, presets, real CSV downloads, details and replay selection passed");
}finally{
  socket?.close();browser.kill();await delay(500);
  if(dirname(resolve(profile))!==resolve(tmpdir()) || !basename(profile).startsWith("countersign-c58-browser-"))throw Error("C58_PROFILE_PATH_INVALID");
  await rm(profile,{recursive:true,force:true,maxRetries:10,retryDelay:200});
}
