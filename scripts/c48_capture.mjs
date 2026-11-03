// C48: dependency-free CDP capture of a real, isolated dashboard decision.
// Invoked only by the opt-in gateway evidence test. Never sends an owner action.
import {spawn} from "node:child_process";
import {mkdtemp, readFile, writeFile, rm, mkdir} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join, resolve} from "node:path";

const [url, destination] = process.argv.slice(2);
if (!/^http:\/\/127\.0\.0\.1:8191\/#decision\/[a-f0-9-]+$/.test(url || "")) {
  throw new Error("C48_CAPTURE_URL_INVALID");
}
const root = resolve(destination);
await mkdir(root, {recursive: true});
const profile = await mkdtemp(join(tmpdir(), "countersign-c48-browser-"));
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
  if (!port) throw new Error("C48_BROWSER_NOT_STARTED");
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
      if (message.error) task.reject(new Error("C48_CDP_FAILED"));
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
    ["decision-light", 1440, 1080, "light"],
    ["decision-dark", 1440, 1080, "dark"],
    ["decision-phone", 390, 844, "light"],
  ]) {
    await call("Emulation.setDeviceMetricsOverride", {width, height, deviceScaleFactor: 1, mobile: width < 500});
    await call("Page.navigate", {url});
    let visible = false;
    for (let attempt = 0; attempt < 300; attempt++) {
      visible = await evaluate(`Boolean(document.querySelector('#simulation')?.textContent.includes('Simulated: mock PayPal') && Boolean(document.querySelector('#approve')) && document.body.innerText.includes('540'))`);
      if (visible) break;
      await delay(100);
    }
    if (!visible) throw new Error("C48_DECISION_NOT_RENDERED");
    // Use the product's actual theme toggle, retaining its own persisted behavior.
    await evaluate(`document.documentElement.dataset.theme !== ${JSON.stringify(theme)} && document.querySelector('#theme').click()`);
    await evaluate("document.fonts.ready");
    await delay(350);
    const result = await evaluate(`({theme:document.documentElement.dataset.theme,banner:document.querySelector('#simulation').textContent,bannerVisible:!document.querySelector('#simulation').hidden,overflow:document.documentElement.scrollWidth>innerWidth,decision:location.hash,ownerAction:Boolean(document.querySelector('#approve'))})`);
    if (!result.bannerVisible || result.overflow || result.theme !== theme || !result.ownerAction) {
      throw new Error("C48_BROWSER_ASSERTION_FAILED");
    }
    const capture = await call("Page.captureScreenshot", {format: "png", captureBeyondViewport: false});
    await writeFile(join(root, name + ".png"), Buffer.from(capture.data, "base64"));
    checks.push({name, width, height, ...result});
  }
  if (failures.length) throw new Error("C48_BROWSER_RUNTIME_EXCEPTION");
  await writeFile(join(root, "browser.json"), JSON.stringify({
    evidence: "Actual local dashboard over TCP 8191; recorded test proposal; simulated PayPal", checks,
    runtime_exceptions: failures.length, owner_actions_sent: 0,
  }, null, 2) + "\n");
  console.log("C48: three decision captures and browser assertions passed");
} finally {
  socket?.close();
  browser.kill();
  await delay(500);
  // Delete only the uniquely created browser profile under the OS temp folder.
  if (!profile.startsWith(join(tmpdir(), "countersign-c48-browser-"))) throw new Error("C48_PROFILE_PATH_INVALID");
  await rm(profile, {recursive: true, force: true, maxRetries: 10, retryDelay: 200});
}
