// C50: dependency-free CDP capture of the recorded sandbox proof report.
// Run against the C50 static preview. Sends no owner action or external API request.
import {spawn} from "node:child_process";
import {mkdtemp, readFile, writeFile, rm, mkdir} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";

const [url, destination] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:8193\/#proof$/.test(url || "")) {
  throw new Error("C50_CAPTURE_URL_INVALID");
}
const root = resolve(destination);
await mkdir(root, {recursive: true});
const profile = await mkdtemp(join(tmpdir(), "countersign-c50-browser-"));
const browser = spawn("C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--no-first-run", "--no-default-browser-check", "about:blank",
], {windowsHide: true, stdio: "ignore"});
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
let socket;
try {
  let port;
  for (let attempt = 0; attempt < 200; attempt++) {
    try { port = (await readFile(join(profile, "DevToolsActivePort"), "utf8")).split("\n")[0]; break; }
    catch { await delay(50); }
  }
  if (!port) throw new Error("C50_BROWSER_NOT_STARTED");
  const tab = await (await fetch(`http://127.0.0.1:${port}/json/new?about:blank`, {method: "PUT"})).json();
  socket = new WebSocket(tab.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => {socket.onopen = resolve; socket.onerror = reject;});
  let sequence = 0;
  const pending = new Map(), failures = [];
  socket.onmessage = event => {
    const message = JSON.parse(event.data);
    if (message.id) {
      const task = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) task.reject(new Error("C50_CDP_FAILED"));
      else task.resolve(message.result);
    } else if (message.method === "Runtime.exceptionThrown") {
      failures.push("BROWSER_RUNTIME_EXCEPTION");
    }
  };
  function call(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++sequence;
      pending.set(id, {resolve, reject});
      socket.send(JSON.stringify({id, method, params}));
    });
  }
  const evaluate = async expression => (await call("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  })).result.value;
  await call("Runtime.enable");
  await call("Page.enable");
  await call("Emulation.setEmulatedMedia", {features: [{name: "prefers-reduced-motion", value: "reduce"}]});
  const checks = [];
  for (const [name, width, height, theme] of [
    ["proof-light", 1440, 1080, "light"],
    ["proof-dark", 1440, 1080, "dark"],
    ["proof-phone", 390, 844, "light"],
  ]) {
    await call("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor: 1, mobile: width < 500});
    await call("Page.navigate", {url});
    let visible = false;
    for (let attempt = 0; attempt < 300; attempt++) {
      visible = await evaluate(`Boolean(document.querySelector('#simulation')?.textContent.includes('Simulated: mock PayPal') && document.querySelectorAll('.proof-check').length === 13)`);
      if (visible) break;
      await delay(100);
    }
    if (!visible) throw new Error("C50_PROOF_NOT_RENDERED");
    // Use the product's actual theme toggle, retaining its own persisted behavior.
    await evaluate(`document.documentElement.dataset.theme !== ${JSON.stringify(theme)} && document.querySelector('#theme').click()`);
    await evaluate("document.fonts.ready");
    await delay(350);
    const result = await evaluate(`({theme:document.documentElement.dataset.theme,banner:document.querySelector('#simulation').textContent,bannerVisible:!document.querySelector('#simulation').hidden,overflow:document.documentElement.scrollWidth>innerWidth,route:location.hash,checks:document.querySelectorAll('.proof-check').length})`);
    if (!result.bannerVisible || result.overflow || result.theme !== theme || result.checks !== 13) {
      throw new Error("C50_BROWSER_ASSERTION_FAILED");
    }
    result.visibleTitle = await evaluate("[...document.querySelectorAll('h1')].some(h=>h.getBoundingClientRect().width>0 && h.textContent==='Sandbox proof')");
    if (!result.visibleTitle) throw new Error("C50_VISIBLE_TITLE_MISSING");
    const capture = await call("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
    await writeFile(join(root, name + ".png"), Buffer.from(capture.data, "base64"));
    // Exercise the real hash route and native keyboard disclosure, then capture
    // the changed-behavior evidence and the distinct model/mock report.
    await evaluate("document.querySelector('.proof-jump a[href=\"#proof/D6\"]').click()");
    await delay(250);
    await evaluate("document.querySelector('#proof-D6 summary').focus()");
    await call("Input.dispatchKeyEvent", {type: "keyDown", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13});
    await call("Input.dispatchKeyEvent", {type: "char", text: "\r", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13});
    await call("Input.dispatchKeyEvent", {type: "keyUp", key: "Enter", code: "Enter", windowsVirtualKeyCode: 13});
    const keyboard = await evaluate("document.querySelector('#proof-D6 details').open && document.activeElement.tagName === 'SUMMARY'");
    if (!keyboard) throw new Error("C50_KEYBOARD_DISCLOSURE_FAILED");
    result.bannerUncoveredAfterScroll = await evaluate("Boolean(document.elementFromPoint(innerWidth/2,12)?.closest('#simulation'))");
    if (!result.bannerUncoveredAfterScroll) throw new Error("C50_BANNER_COVERED");
    const detail = await call("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
    await writeFile(join(root, name + "-d6.png"), Buffer.from(detail.data, "base64"));
    await evaluate("document.querySelector('.proof-model').scrollIntoView({block:'start',behavior:'instant'})");
    await delay(100);
    const model = await call("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
    await writeFile(join(root, name + "-model.png"), Buffer.from(model.data, "base64"));
    const links = await evaluate("[...document.querySelectorAll('.proof-page a[href^=\"https://github.com/\"]')].every(a=>a.href.startsWith('https://github.com/Razeefshaik/CounterSign/blob/main/') && a.rel.includes('noopener'))");
    if (!links) throw new Error("C50_SOURCE_LINK_FAILED");
    result.keyboardDisclosure = keyboard;
    result.sourceLinks = links;
    checks.push({name, width, height, ...result});
  }
  if (failures.length) throw new Error("C50_BROWSER_RUNTIME_EXCEPTION");
  await writeFile(join(root, "browser.json"), JSON.stringify({
    evidence: "Actual local dashboard over TCP 8193; committed sandbox proof bundle; fixture workspace labelled simulated", checks,
    runtime_exceptions: failures.length, owner_actions_sent: 0,
  }, null, 2) + "\n");
  console.log("C50: three proof captures and browser assertions passed");
} finally {
  socket?.close();
  browser.kill();
  await delay(500);
  // Delete only the uniquely created browser profile under the OS temp folder.
  if (!profile.startsWith(join(tmpdir(), "countersign-c50-browser-"))) throw new Error("C50_PROFILE_PATH_INVALID");
  await rm(profile, {recursive: true, force: true, maxRetries: 10, retryDelay: 200});
}
