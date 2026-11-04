// C67: dependency-free browser integration, using the same headless CDP approach as C50.
// Only the isolated fixture preview on 8201 is permitted. No operational POSTs.
import {spawn} from "node:child_process";
import {mkdtemp,readFile,writeFile,mkdir,rm,rename} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join,resolve,dirname,basename} from "node:path";

const origin="http://127.0.0.1:8201", url=origin+"/#model-week";
const destination=resolve(process.argv[2] || "design-demos/reference/c67");
await mkdir(destination,{recursive:true});
const profile=await mkdtemp(join(tmpdir(),"countersign-c67-browser-"));
const browser=spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",[
  "--headless=new","--remote-debugging-port=0",`--user-data-dir=${profile}`,
  "--no-first-run","--no-default-browser-check","about:blank",
],{windowsHide:true,stdio:"ignore"});
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
let socket;
try {
  let port;
  for(let i=0;i<200;i++){try{port=(await readFile(join(profile,"DevToolsActivePort"),"utf8")).split("\n")[0];break;}catch{await delay(50);}}
  if(!port)throw Error("C67_BROWSER_NOT_STARTED");
  const tab=await(await fetch(`http://127.0.0.1:${port}/json/new?about:blank`,{method:"PUT"})).json();
  socket=new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  let sequence=0;const pending=new Map(),exceptions=[],warnings=[],requests=[],downloads=[];
  socket.onmessage=event=>{
    const message=JSON.parse(event.data);
    if(message.id){const p=pending.get(message.id);pending.delete(message.id);message.error?p.reject(Error("C67_CDP_ERROR")):p.resolve(message.result);}
    else if(message.method==="Runtime.exceptionThrown")exceptions.push("runtime exception");
    else if(message.method==="Runtime.consoleAPICalled" && ["error","warning"].includes(message.params.type))warnings.push(message.params.args.map(a=>a.value||a.description||"").join(" "));
    else if(message.method==="Network.requestWillBeSent")requests.push({method:message.params.request.method,url:message.params.request.url});
    else if(message.method==="Browser.downloadProgress" && message.params.state==="completed")downloads.push(message.params.guid);
  };
  const call=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  const evaluate=async expression=>{const r=await call("Runtime.evaluate",{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error("C67_PAGE_EVALUATION_FAILED: "+r.exceptionDetails.text);return r.result?.value;};
  const wait=async(expression,label)=>{for(let i=0;i<250;i++){if(await evaluate(expression))return;await delay(80);}throw Error("C67_WAIT_FAILED: "+label);};
  const check=(value,label)=>{if(!value)throw Error("C67_ASSERTION_FAILED: "+label);};
  const capture=async name=>{const shot=await call("Page.captureScreenshot",{format:"png",captureBeyondViewport:false});await writeFile(join(destination,name+".png"),Buffer.from(shot.data,"base64"));};
  await call("Runtime.enable");await call("Page.enable");await call("Network.enable");
  await call("Emulation.setEmulatedMedia",{features:[{name:"prefers-reduced-motion",value:"reduce"}]});

  const checks=[];
  for(const [name,width,height,theme] of [["light",1440,1080,"light"],["dark",1440,1080,"dark"],["phone",390,844,"light"]]){
    await call("Emulation.setDeviceMetricsOverride",{width,height,deviceScaleFactor:1,mobile:width<500});
    await call("Page.navigate",{url:`${origin}/?c67=${name}#model-week`});
    await wait("document.querySelector('.c67-model-page')","model recordings ready");
    await evaluate(`document.documentElement.dataset.theme !== ${JSON.stringify(theme)} && document.querySelector('#theme').click()`);
    await evaluate("document.fonts.ready");await delay(200);
    const state=await evaluate(`({theme:document.documentElement.dataset.theme,overflow:document.documentElement.scrollWidth>innerWidth,
      banner:document.querySelector('#simulation').textContent,text:document.querySelector('.c67-model-page').textContent})`);
    check(state.theme===theme && !state.overflow && state.banner.includes("Simulated: mock PayPal"),"theme/viewport/banner");
    check(state.text.includes("MODEL_QUOTA_EXHAUSTED") && state.text.includes("Gemma via OpenRouter") && state.text.includes("Gemini via Google"),"separate real provider evidence");
    check(state.text.includes("No model week decisions were recorded") && state.text.includes("Not recorded"),"no invented decisions or missing fields");
    await capture(name+"-model-status");
    await evaluate("{const d=document.querySelector('[data-week=gemini-2026-10-27] .c67-decision'); d.open=true; d.scrollIntoView({block:'center',behavior:'instant'});}");
    await capture(name+"-earlier-decision");
    check(await evaluate("Boolean(document.elementFromPoint(innerWidth/2,12)?.closest('#simulation'))"),"simulation banner uncovered after scroll");
    check(await evaluate("document.documentElement.scrollWidth<=innerWidth"),"expanded decision no overflow");
    await evaluate("location.hash='replay'");
    await wait("document.querySelector('[data-c67-dataset]')","dataset picker");
    await evaluate("{const s=document.querySelector('[data-c67-dataset]');s.value='real';s.dispatchEvent(new Event('change',{bubbles:true}));}");
    await wait("document.querySelector('[data-c67-results]')?.textContent.includes('No score is available')","honest unavailable comparison");
    check(await evaluate("document.querySelector('.c58-explorer').hidden && !document.querySelector('[data-c67-results]').textContent.includes('170/240')"),"historical numbers not relabelled");
    await evaluate("document.querySelector('.c67-datasets').scrollIntoView({block:'start',behavior:'instant'})");
    await capture(name+"-dataset-unavailable");
    await evaluate("{const s=document.querySelector('[data-c67-dataset]');s.value='historical';s.dispatchEvent(new Event('change',{bubbles:true}));}");
    await wait("!document.querySelector('.c58-explorer').hidden","historical restored");
    await evaluate("location.hash='decision'");
    await wait("document.querySelector('#view-body a[href=\"#model-week\"]')","decision link");
    checks.push({name,width,height,theme,overflow:false,banner_visible:true,missing_fields_honest:true,datasets_separate:true,decision_link:true});
  }
  check(exceptions.length===0,"zero runtime exceptions");
  check(!requests.some(r=>r.method!=="GET"),"read-only browser run");
  await writeFile(join(destination,"browser.json"),JSON.stringify({origin,evidence:"Actual built C67 container; recorded model evidence and honest prerequisite stop",checks,runtime_exceptions:exceptions,console_warnings:warnings,non_get_requests:requests.filter(r=>r.method!=="GET")},null,2)+"\n");
  console.log("C67: light, dark, phone; nine captures; missing-field and dataset checks passed");
}finally{
  socket?.close();browser.kill();await delay(500);
  if(dirname(resolve(profile))!==resolve(tmpdir()) || !basename(profile).startsWith("countersign-c67-browser-"))throw Error("C67_PROFILE_PATH_INVALID");
  await rm(profile,{recursive:true,force:true,maxRetries:10,retryDelay:200});
}
