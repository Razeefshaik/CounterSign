// C65: two explicit sources. This screen makes only GET requests.
import {esc} from "./views.mjs";
import {loadVendor} from "./grid.mjs";
import {sourceURL} from "./proof.mjs";

export const MOCK_SOURCE = "Simulated: PayPal mock";
export const RECORDED_SOURCE = "Recorded: real PayPal sandbox";
export const PRODUCTS = Object.freeze({
  Payouts:"Create, inspect and cancel payouts", Disputes:"Read cases, submit evidence and settle",
  Orders:"Create and capture buyer orders", Payments:"Check captured payments",
  Tracking:"Attach and inspect shipment tracking", Reporting:"Read balances and transaction history",
  Webhooks:"Register, verify and receive events", OAuth:"Get an access token",
});
const hash = value => typeof value === "string" && /^[a-f0-9]{12}$/.test(value) ? value : null;
const key = row => hash(row.sender_batch_id_hash) || hash(row.request_id_hash);
const number = value => Number.isSafeInteger(value) && value >= 0 ? value.toLocaleString("en-US") : "—";
const textCell = p => esc(p.valueFormatted ?? p.value ?? "—");
export const csvCell = value => /^[=+\-@\t\r]/.test(String(value ?? "")) ? "'" + value : value ?? "";

export function operation(row) {
  if (row.direction === "PayPal to app") return "Webhook delivery";
  if (row.product === "OAuth") return "Authentication";
  if (row.route === "/v1/notifications/verify-webhook-signature") return "Verification";
  return ["GET", "HEAD", "OPTIONS"].includes(row.method) ? "Read" : "Write";
}

export function annotate(rows) {
  const groups = new Map();
  for (const row of rows) if (row.method === "POST" && row.route === "/v1/payments/payouts" && key(row)) {
    const id = key(row);
    if (!groups.has(id)) groups.set(id, []);
    groups.get(id).push(row);
  }
  return rows.map(row => {
    const repeated = key(row) && groups.get(key(row))?.length > 1;
    const linked = row.status === 400 && row.original_batch === true;
    return {...row, key_hash:key(row), operation:operation(row), retry_chain:Boolean(repeated || linked),
      evidence:linked ? "400 · original batch linked" : row.incomplete ? "Response lost" : repeated ? "Same payout key" : "—",
      status_text:row.status == null ? "No response" : `${row.status}${row.incomplete ? " · response lost" : ""}`};
  });
}

export function tiles(log) {
  return `<div class="calls-products" aria-label="PayPal products used this week">${Object.entries(PRODUCTS).map(([product, purpose]) =>
    `<article><h3>${esc(product)}</h3><strong>${esc(log ? number(log.counts?.[product] ?? 0) : "—")}</strong><p>${esc(purpose)}</p></article>`).join("")}</div>`;
}

export function parseCoverage(value) {
  if (value?.source !== RECORDED_SOURCE || value.total_attempts !== 1041 || !Array.isArray(value.rows)) throw Error("Invalid coverage");
  const paths = new Set();
  let total = 0;
  for (const row of value.rows) {
    if (!Number.isSafeInteger(row.id) || row.source !== RECORDED_SOURCE || !Object.hasOwn(PRODUCTS, row.product) ||
        !Number.isSafeInteger(row.attempts) || row.attempts < 1 || row.attempts !== row.recordings?.length ||
        !Array.isArray(row.status_codes) || row.status_codes.some(n => !Number.isInteger(n) || n < 100 || n > 599)) throw Error("Invalid coverage");
    for (const recording of row.recordings) {
      if (!sourceURL(recording.path) || !/^recordings\/sandbox\/\d{4}-\d{2}-\d{2}\/D(?:[1-9]|1[0-3])\/\d+_[A-Za-z0-9_.-]+\.json$/.test(recording.path) || paths.has(recording.path)) throw Error("Invalid source");
      paths.add(recording.path);
    }
    total += row.attempts;
  }
  if (total !== value.total_attempts) throw Error("Invalid total");
  return value;
}

export function tableHTML(rows, recorded = false) {
  const headings = recorded ? ["Product", "Method", "Route", "Attempts", "Statuses", "Recordings"] : ["Method", "Route", "Status", "Product", "Direction", "Key hash", "Evidence"];
  return `<div class="calls-fallback"><table><thead><tr>${headings.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.map(row => `<tr>${(recorded
    ? [row.product, row.method, row.route, row.attempts, row.status_codes.join(", ")]
    : [row.method, row.route, row.status_text, row.product, row.direction, row.key_hash || "—", row.evidence])
    .map(v => `<td>${esc(v ?? "—")}</td>`).join("")}${recorded ? `<td><button type="button" data-calls-recordings="${esc(row.id)}">View ${esc(row.attempts)} recordings</button></td>` : ""}</tr>`).join("")}</tbody></table></div>`;
}

export function pageHTML() {
  return `<div class="calls-page"><p class="calls-intro">Inspect the PayPal calls behind this week, then compare them with the committed sandbox recordings.</p>
    <section class="calls-summary glass" aria-labelledby="calls-products-title"><header><h2 id="calls-products-title">This week on the mock</h2><span class="calls-source">${esc(MOCK_SOURCE)}</span></header><div data-calls-tiles>${tiles(null)}</div></section>
    <section class="calls-log glass" aria-labelledby="calls-log-title"><header><div><h2 id="calls-log-title">Live call log</h2><p>${esc(MOCK_SOURCE)} · staged time, newest first</p></div><button type="button" data-calls-export="live" disabled>Export calls CSV</button></header>
      <div class="calls-tools"><label>Search calls<input type="search" data-calls-search="live" placeholder="Route, product or key hash"></label><label class="calls-retry"><input type="checkbox" data-calls-retry> Show payout retry chains</label><button type="button" data-calls-refresh>Refresh calls</button></div>
      <p class="calls-status" data-calls-status role="status">Reading the mock call log…</p><p class="calls-help">A lost response and a retry share a key hash. “400 · original batch linked” means the duplicate response points to the first batch.</p>
      <div data-calls-live aria-label="Simulated PayPal calls"><p class="empty">Loading calls…</p></div></section>
    <section class="calls-boundary glass"><h2>The write boundary</h2><p>Write marks non-read API operations; authentication, signature verification and delivered webhooks are labelled separately. For financial proposals, only the executor may call the PayPal write adapter, after shield checks and any required owner approval. <a href="https://github.com/Razeefshaik/CounterSign/blob/main/pyproject.toml#L69" target="_blank" rel="noopener">Read the import contract</a>.</p></section>
    <section class="calls-coverage glass" aria-labelledby="calls-coverage-title"><header><div><h2 id="calls-coverage-title">Real sandbox coverage</h2><p>${esc(RECORDED_SOURCE)} · <span data-calls-total>reading committed evidence</span></p></div><button type="button" data-calls-export="coverage" disabled>Export coverage CSV</button></header>
      <div class="calls-tools"><label>Search recorded routes<input type="search" data-calls-search="coverage" placeholder="Product, route or check"></label><button type="button" data-calls-coverage-retry hidden>Retry coverage</button></div>
      <div data-calls-coverage><p class="empty">Loading committed sandbox coverage…</p></div><aside class="calls-sources" data-calls-sources tabindex="-1" hidden></aside></section></div>`;
}

export function liveColumns() {
  const col = (field, headerName, extra = {}) => ({field, headerName, minWidth:145, filter:"agTextColumnFilter", cellRenderer:textCell, ...extra});
  return [col("at", "Staged time", {minWidth:210}), col("method", "Method", {minWidth:100}),
    col("route", "Route", {minWidth:350}), col("status_text", "Status", {minWidth:170}),
    col("product", "Product"), col("operation", "Operation"), col("direction", "Direction", {minWidth:170}),
    col("key_hash", "Key hash"), col("evidence", "Retry evidence", {minWidth:240}), col("source", "Source", {minWidth:235})];
}

export function coverageColumns() {
  return [{field:"product",headerName:"Product",minWidth:140}, {field:"method",headerName:"Method",minWidth:100},
    {field:"route",headerName:"Route",minWidth:350}, {field:"attempts",headerName:"Attempts",minWidth:120,filter:"agNumberColumnFilter",cellClass:"cs-grid-money"},
    {field:"status_codes",headerName:"Statuses seen",minWidth:160,valueFormatter:p=>(p.value || []).join(", ")},
    {field:"transport_errors",headerName:"Transport errors",minWidth:165,cellClass:"cs-grid-money"},
    {field:"check_ids",headerName:"Checks",minWidth:220,valueFormatter:p=>(p.value || []).join(", ")},
    {field:"recordings",headerName:"Source recordings",minWidth:200,cellRenderer:p=>`<button type="button" class="calls-source-button" data-calls-recordings="${esc(p.data.id)}">View ${esc(p.data.attempts)} recordings</button>`},
    {field:"source",headerName:"Source",minWidth:270}].map(c=>({filter:"agTextColumnFilter",cellRenderer:textCell,...c}));
}

export function createPaypalCalls(document, fetcher = fetch) {
  let active = null;
  const view = document.defaultView || globalThis;
  function dispose() {active?.destroy(); active = null;}
  async function mount(host) {
    dispose();
    if (!host?.isConnected) return;
    let timer, observer, liveApi, coverageApi, log = null, bundle = null, liveRows = [], disposed = false, polling = false, library = null;
    const abort = new AbortController();
    active = {destroy() {disposed = true; abort.abort(); view.clearTimeout(timer); observer?.disconnect(); liveApi?.destroy(); coverageApi?.destroy();}};
    const valid = () => !disposed && host.isConnected && (!document.body?.dataset?.route || document.body.dataset.route === "paypal-calls");
    host.innerHTML = pageHTML();
    const $ = selector => host.querySelector(selector);
    async function get(url) {const r=await fetcher(url,{cache:"no-store",signal:abort.signal}); if(!r.ok) throw Error("Unavailable"); return r.json();}
    function updateStatus() {
      if (!valid() || !log) return;
      let shown=liveRows.length;
      if (liveApi) {shown=0;liveApi.forEachNodeAfterFilterAndSort(()=>shown++);}
      $("[data-calls-status]").textContent = `${shown} calls shown · ${log.total} calls this week${log.total>log.rows.length ? ` · latest ${log.rows.length} retained` : ""}. Updates every 3 seconds while this page is open.`;
    }
    async function readLive() {
      if (polling || !valid()) return;
      polling = true;
      try {
        const next = await get("/api/v1/demo/paypal-calls");
        if (!valid()) return;
        if (next.source !== MOCK_SOURCE || !Array.isArray(next.rows) || !Number.isSafeInteger(next.total)) throw Error("Invalid log");
        log = next; liveRows = annotate(next.rows);
        $("[data-calls-tiles]").innerHTML = tiles(log);
        if (liveApi) liveApi.setGridOption("rowData",liveRows);
        else $("[data-calls-live]").innerHTML = liveRows.length ? tableHTML(liveRows) : '<p class="empty">No PayPal calls yet. Start the staged week to record its calls.</p>';
        installGrids(); updateStatus();
      } catch {
        if (!valid()) return;
        // Do not leave stale counts or rows looking like a current read.
        log = null; liveRows = []; liveApi?.setGridOption("rowData", []);
        $("[data-calls-tiles]").innerHTML = tiles(null);
        $("[data-calls-status]").textContent = "The demo call log is unavailable. It is served only in demo mode. Refresh calls to retry; recorded sandbox coverage is separate.";
        if (!liveApi) $("[data-calls-live]").innerHTML = '<p class="empty">No current call log is available.</p>';
      } finally {polling = false;}
    }
    async function readCoverage() {
      try {
        const next = parseCoverage(await get("results/paypal-coverage.json"));
        if (!valid()) return;
        bundle = next; $("[data-calls-total]").textContent = `${next.total_attempts.toLocaleString("en-US")} recorded attempts`;
        if (coverageApi) coverageApi.setGridOption("rowData", next.rows);
        else $("[data-calls-coverage]").innerHTML = tableHTML(next.rows, true);
        $("[data-calls-coverage-retry]").hidden = true; installGrids();
      } catch {
        if (!valid()) return;
        $("[data-calls-total]").textContent = "coverage unavailable";
        $("[data-calls-coverage]").innerHTML = '<p class="empty">The committed sandbox coverage could not be loaded. Retry coverage.</p>';
        $("[data-calls-coverage-retry]").hidden = false;
      }
    }
    $("[data-calls-refresh]").onclick = readLive;
    $("[data-calls-coverage-retry]").onclick = readCoverage;
    host.onclick = event => {
      const id = Number(event.target.closest?.("[data-calls-recordings]")?.dataset.callsRecordings);
      const row = bundle?.rows.find(row => row.id === id);
      if (!row) return;
      const sources = $("[data-calls-sources]");
      sources.innerHTML = `<h3>${esc(row.method)} ${esc(row.route)}</h3><p>${esc(RECORDED_SOURCE)} · ${esc(row.attempts)} attempts</p>${row.check_ids.map(check=>`<details><summary>${esc(check)} recordings</summary><ul>${row.recordings.filter(r=>r.check_id===check).map(r=>`<li><a href="${esc(sourceURL(r.path))}" target="_blank" rel="noopener">${esc(r.path.split("/").at(-1))}</a> · ${esc(r.status ?? "transport error")}</li>`).join("")}</ul></details>`).join("")}`;
      sources.hidden = false; sources.focus({preventScroll:true}); sources.scrollIntoView({block:"nearest",behavior:"instant"});
    };
    function installGrids() {
      if (!valid() || !library?.createGrid) return;
      const options = {defaultColDef:{sortable:true,resizable:true,suppressMovable:false,cellRenderer:textCell},
        rowHeight:42,headerHeight:44,pagination:true,paginationPageSize:10,paginationPageSizeSelector:false,
        animateRows:false,ensureDomOrder:true,enableCellTextSelection:true,getRowId:p=>String(p.data.id),
        defaultCsvExportParams:{exportedRows:"filteredAndSorted",processCellCallback:p=>csvCell(
          p.column.getColId()==="recordings" ? (p.value || []).map(r=>sourceURL(r.path)).join(" | ")
          : Array.isArray(p.value) ? p.value.join(", ") : p.value)}};
      for (const name of ["live","coverage"]) {
        if ((name==="live" && (!log || liveApi)) || (name==="coverage" && (!bundle || coverageApi))) continue;
        const grid=$(name==="live"?"[data-calls-live]":"[data-calls-coverage]");
        const fallback=grid.innerHTML;grid.innerHTML="";grid.className="ag-theme-quartz cs-grid calls-grid";
        try {
          const api=library.createGrid(grid,{...options,columnDefs:name==="live"?liveColumns():coverageColumns(),rowData:name==="live"?liveRows:bundle.rows,
            overlayNoRowsTemplate:'<span>No matching calls. Clear filters, or play the staged week.</span>',
            ...(name==="live"?{rowClassRules:{"calls-retry-row":p=>p.data?.retry_chain},
              isExternalFilterPresent:()=>$("[data-calls-retry]").checked,
              doesExternalFilterPass:n=>n.data.retry_chain,onFilterChanged:updateStatus,onSortChanged:updateStatus}:{})});
          if (name==="live") liveApi=api; else coverageApi=api;
          $("[data-calls-export="+name+"]").disabled=false;
        } catch {grid.className="";grid.innerHTML=fallback+'<p class="empty">The Community grid could not start. Reload to retry; the table remains readable.</p>';}
      }
    }
    for (const name of ["live","coverage"]) {
      const api = () => name==="live" ? liveApi : coverageApi;
      $("[data-calls-search="+name+"]").oninput=e=>api()?.setGridOption("quickFilterText",e.target.value);
      $("[data-calls-export="+name+"]").onclick=()=>api()?.exportDataAsCsv({fileName:name==="live"?"simulated-paypal-calls.csv":"recorded-paypal-coverage.csv"});
    }
    await Promise.allSettled([readLive(), readCoverage()]);
    if (!valid()) return;
    library = await loadVendor(document);
    if (!valid()) return;
    installGrids();
    $("[data-calls-retry]").onchange=()=>liveApi?.onFilterChanged();
    updateStatus();
    const poll=async()=>{if(!valid()) return; if(!document.hidden) await readLive(); if(valid()) timer=view.setTimeout(poll,3000);};
    timer=view.setTimeout(poll,3000);
    if (view.MutationObserver) {observer=new view.MutationObserver(()=>{if(!host.isConnected) dispose();});observer.observe(document.body,{childList:true,subtree:true});}
  }
  return {mount,dispose};
}
