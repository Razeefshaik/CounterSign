// C49: Community-only data grids. API projections and columns are pure; DOM work
// is confined to the loader/mount. The existing HTML remains the failure path.
import {esc, incidentTitle} from "./views.mjs";
import {money} from "./data.mjs";
import {action, kind, rule, state, when} from "./words.mjs";
import {makerState} from "./plot.mjs";

import {savedViews, viewCounts, filteredTotal, minorCompare, dateFilterCompare, csvSafe, layoutKey, readLayout, saveLayout, clearLayout, changedRows} from "./grid-showcase.mjs"; // C66

export const VENDOR = "vendor/ag-grid-community.min.js";
const stampTones = {AWAITING_OWNER:"owner", NEEDS_OWNER:"owner", APPROVED:"good", ALLOW:"good", PASS:"good", SUCCESS:"good", PAID:"good", DONE:"good", BLOCK:"bad", BLOCKED:"bad", FAILED:"bad", REJECTED:"bad", ask:"owner", paid:"good"};
export const stateTone = value => stampTones[value] || "quiet";
const safeText = p => esc(p.valueFormatted ?? p.value ?? "—");
const stamp = p => p.value ? `<span class="stamp ${stateTone(p.value)}" title="${esc(p.value)}">${esc(p.data?.state_label && p.colDef.field === "state" ? p.data.state_label : state(p.value))}</span>` : "—";
const moneyFields = (value, key = "amount") => ({
  [key + "_minor"]: Number.isSafeInteger(value?.minor) && /^[A-Z]{3}$/.test(value?.currency) ? value.minor : null,
  [key + "_currency"]: /^[A-Z]{3}$/.test(value?.currency) ? value.currency : null,
});
export function moneyText(minor, currency) {
  if (!Number.isSafeInteger(minor) || !/^[A-Z]{3}$/.test(currency)) return "—";
  const formatted = money({minor, currency});
  return currency === "USD" ? formatted : currency + " " + formatted.replace("$", "");
}
const dateText = p => p.value && Number.isFinite(Date.parse(p.value)) ? when(p.value) : "—";
const dateCompare = (a, b) => (Date.parse(a) || 0) - (Date.parse(b) || 0);
const textCol = (field, headerName, extra = {}) => ({field, headerName, minWidth:140, flex:1, floatingFilter:true, filter:"agTextColumnFilter", cellRenderer:safeText, ...extra});
const moneyCol = (field = "amount_minor", headerName = "Amount") => ({field, headerName, minWidth:125, width:135, flex:0, floatingFilter:true, comparator:minorCompare, filter:"agNumberColumnFilter", cellClass:"cs-grid-money", headerClass:"cs-grid-number", valueFormatter:p => moneyText(p.value, p.data?.[field.replace(/_minor$/, "_currency")]), cellRenderer:p => p.node?.rowPinned ? esc(p.data?.[field === "held_minor" ? "held_total" : "amount_total"]) : safeText(p)});
const timeCol = (field, headerName) => textCol(field, headerName, {minWidth:195, filter:"agDateColumnFilter", filterParams:{comparator:dateFilterCompare}, valueFormatter:dateText, comparator:dateCompare});

export function incidentRows(incidents, {makers = [], disputes = [], staged = () => false, proposals = []} = {}) {
  const names = new Map(makers.map(m => [m.id, m.display_name]));
  const paypalIds = new Map(disputes.map(d => [d.id, d.paypal_dispute_id]));
  // C69: a decision with no incident of its own, such as the weekly run, still needs the owner.
  const linked = new Set(incidents.map(row => row.latest_proposal?.id));
  const waiting = proposals.filter(p => p.state === "NEEDS_OWNER" && !linked.has(p.id)).map(p => ({
    id:"decision:"+p.id, decision_id:p.id, incident:action(p.action), kind:"Decision", state:"AWAITING_OWNER",
    staged:false, goal:false, maker:null, dispute:null, updated:p.created_at || null, verdict:"NEEDS_OWNER", ...moneyFields(null,"held"), ...moneyFields(null),
  }));
  return [...waiting, ...incidents.map(row => ({
    id:row.id, incident:incidentTitle(row).text, kind:kind(row.kind), state:row.state,
    staged:Boolean(staged(row)), goal:incidentTitle(row).goal,
    maker:names.get(row.subject?.maker_id) || null,
    dispute:row.subject?.paypal_dispute_id || paypalIds.get(row.subject?.dispute_id) || null,
    updated:row.updated_at, verdict:row.latest_proposal?.verdict || null,
    ...moneyFields(disputes.find(d => d.id === row.subject?.dispute_id || d.paypal_dispute_id === row.subject?.paypal_dispute_id)?.reserved,"held"),
    ...moneyFields(row.state === "RESOLVED" ? null : row.amount_at_risk), // C69: resolved money is no longer at risk
  }))];
}

export function auditRows(entries, proposals = []) {
  const operationOwners = new Map();
  for (const e of [...entries, ...proposals.map(p => ({operation_id:p.operation?.id, proposal_id:p.id}))]) {
    if (e.operation_id && e.proposal_id) operationOwners.set(e.operation_id, e.proposal_id);
  }
  return entries.map(e => ({
    id:String(e.id), event_kind:e.kind, at:e.at, actor:e.actor, action:e.details?.action ? action(e.details.action) : kind(e.kind),
    rule:e.rule ? rule(e.rule) : null, rule_code:e.rule || null, verdict:e.verdict || null,
    proposal_id:e.proposal_id || null, operation_id:e.operation_id || null,
    decision_id:e.proposal_id || operationOwners.get(e.operation_id) || null,
    ...moneyFields(e.details?.amount || e.details?.total || e.details?.available_change),
  }));
}

export function runRows(run) {
  const rows = (run?.lines || []).map(line => {
    const latest = [...(line.attempts || [])].sort((a, b) => b.attempt_no - a.attempt_no)[0];
    const paypalState = latest?.paypal_item_id && ["SUCCESS", "FAILED", "PENDING", "UNCLAIMED", "RETURNED", "ONHOLD", "BLOCKED", "REFUNDED", "REVERSED"].includes(latest.state) ? latest.state : null;
    return {id:line.id, maker_id:line.maker?.id, decision_id:run.proposal_id || null, maker:line.maker?.display_name, state:line.state, attempt:latest?.attempt_no ?? null,
      waiting:["APPROVED","SCHEDULED","IN_FLIGHT","PENDING","UNKNOWN"].includes(line.state) ? "yes" : "no", paid:line.state === "SETTLED" || paypalState === "SUCCESS" ? "yes" : "no",
      item_status:paypalState, item_id:latest?.paypal_item_id || null, replacement:false, ...moneyFields(line.amount)};
  });
  for (const replacement of run?.replacements || []) {
    // The replacement summary describes the PREVIOUS attempt, not the new item's status.
    // Do not duplicate a line already included in this run's lines.
    if (rows.some(row => row.id === replacement.line_id)) continue;
    rows.push({id:replacement.line_id, maker:replacement.maker?.display_name, state:null,
      attempt:replacement.attempt_no ?? null, item_status:null, item_id:null, replacement:true,
      ...moneyFields(replacement.amount)});
  }
  // C66: reserved shares are separate observed obligations, never paid lines.
  for (const held of run?.reserved || []) rows.push({id:"held:"+held.obligation_id, maker_id:held.maker?.id,
    maker:held.maker?.display_name, state:"RESERVED", waiting:"no",paid:"no",held:true,
    ...moneyFields(held.amount)});
  return rows;
}
export function runTotal(run) {
  return {id:"run-total", maker:"Run total", state:null, attempt:null, item_status:null, ...moneyFields(run?.total)};
}

export function makerRows(makers, model) {
  const lookup = new Map((model.all || model.makers).map(m => [m.id, m]));
  return makers.map(m => {
    const projection = lookup.get(m.id), s = projection ? makerState(projection, model) : {key:"idle", label:"Unavailable", what:"State unavailable"};
    const amount = s.key === "sending" ? m.in_flight : ["ask", "scheduled"].includes(s.key) ? m.scheduled
      : s.key === "held" ? m.reserved : s.key === "owed" ? m.owed_open : s.key === "paid" ? m.settled_last_30d : null;
    return {id:m.id, unverified:m.payout_email_verified === false ? "yes" : "no", maker:m.display_name, state:s.key, state_label:s.label, note:s.what,
      ...moneyFields(amount), ...moneyFields(m.reserved, "held")};
  });
}

export const makerFilter = (rows, requested) => rows.some(row => row.state === requested) ? requested : "all";

const incidentCell = p => p.node?.rowPinned ? safeText(p) : `<strong>${esc(p.value)}</strong><span class="cs-grid-sub">${esc(p.data.kind)}${p.data.goal ? ' <span class="staged">demo goal</span>' : ""}${p.data.staged ? ' <span class="staged">staged</span>' : ""}</span>`;
const decisionLink = p => {
  const id = p.data?.decision_id;
  return id && p.value ? `<a class="cs-grid-link" href="#decision/${esc(encodeURIComponent(id))}">${esc(p.value)}</a>` : safeText(p);
};
export function columnDefs(view) {
  if (view === "incidents") return [textCol("incident", "Incident", {minWidth:185, width:300, flex:0, pinned:"left", cellRenderer:incidentCell}), textCol("state", "State", {minWidth:140, cellRenderer:stamp}), moneyCol("amount_minor", "At risk"), textCol("maker", "Maker", {minWidth:110}), textCol("dispute", "Dispute", {minWidth:160}), timeCol("updated", "Updated")];
  if (view === "audit") return [timeCol("at", "Time"), textCol("actor", "Actor", {minWidth:110}), textCol("action", "Action", {pinned:"left",minWidth:150,width:180,flex:0}), textCol("rule", "Rule", {minWidth:170,cellRenderer:p => {
    const code=p.data?.rule_code, id=p.data?.decision_id;
    return id && code ? `<a class="cs-rule-chip" href="#decision/${esc(encodeURIComponent(id))}" title="Read ${esc(code)} in this decision">${safeText(p)}</a>` : safeText(p);
  }}), textCol("verdict", "Verdict", {minWidth:150, cellRenderer:stamp}), moneyCol(), textCol("proposal_id", "Proposal", {minWidth:220, cellRenderer:decisionLink}), textCol("operation_id", "Operation", {minWidth:220, cellRenderer:decisionLink})];
  if (view === "runs") return [textCol("maker", "Maker", {pinned:"left", width:155, flex:0, minWidth:140, cellRenderer:p => safeText(p) + (p.data?.replacement ? '<span class="cs-grid-sub">Resend · prior run line</span>' : "")}), moneyCol(), textCol("state", "State", {cellRenderer:stamp}), textCol("attempt", "Attempt", {minWidth:100, width:100, flex:0, filter:"agNumberColumnFilter"}), textCol("item_status", "PayPal item status", {minWidth:180, cellRenderer:stamp}), textCol("item_id", "PayPal item", {minWidth:200})];
  if (view === "makers") return [textCol("maker", "Maker", {pinned:"left", width:155, flex:0, minWidth:140}), textCol("state", "State", {minWidth:175, cellRenderer:stamp, valueFormatter:p => p.data?.state_label || "—"}), moneyCol(), moneyCol("held_minor", "Held"), textCol("note", "Note", {minWidth:270, flex:2})];
  throw Error("Unknown grid view");
}

export function gridOptions(view, rows, {onOpen = () => {}, onFilterChanged = () => {}, total = null, quickFilterText = "", reducedMotion = false} = {}) {
  return {
    columnDefs:[...columnDefs(view), ...({incidents:["held_minor","verdict"],audit:["event_kind"],runs:["waiting","paid"],makers:["unverified"]}[view] || []).map(field => ({field,hide:true,filter:field.endsWith("_minor") ? "agNumberColumnFilter" : "agTextColumnFilter"}))],
    rowData:rows, suppressMaintainUnsortedOrder:false, defaultColDef:{sortable:true, resizable:true, suppressMovable:false,cellDataType:false,enableCellChangeFlash:!reducedMotion},
    getRowId:p => String(p.data.id), rowHeight:view === "incidents" ? 64 : 52, headerHeight:44,
    animateRows:false, suppressScrollOnNewData:true, cellFlashDuration:reducedMotion ? 0 : 700,cellFadeDuration:reducedMotion ? 0 : 500, enableCellTextSelection:true, ensureDomOrder:true,
    pagination:true, paginationPageSize:10, paginationPageSizeSelector:false,
    quickFilterText, includeHiddenColumnsInQuickFilter:true, domLayout:"normal",
    overlayNoRowsTemplate:'<span class="cs-grid-empty">No matching records. Clear a filter to see all records.</span>',
    pinnedBottomRowData:total ? [total] : [],
    onFilterChanged,
    defaultCsvExportParams:{fileName:`countersign-${view}.csv`, exportedRows:"filteredAndSorted",skipPinnedBottom:true,
      processCellCallback:p => csvSafe(p.column.getColDef().valueFormatter ? p.api.getCellValue({rowNode:p.node,colKey:p.column,useFormatter:true}) : p.value)},
    onRowClicked:e => {if (!e.node?.rowPinned && !e.event?.target?.closest?.("a,button")) onOpen(e.data);},
    onCellKeyDown:e => {if (e.event?.key === "Enter" && !e.node?.rowPinned && !e.event?.target?.closest?.("a,button")) {e.event.preventDefault(); onOpen(e.data);}},
    getRowClass:p => p.data?.state === "AWAITING_OWNER" || p.data?.verdict === "NEEDS_OWNER" || p.data?.state === "ask" ? "cs-grid-needs-owner" : "",
  };
}

// C69: rows, the pinned total row, header with floating filters (88) and paging (56).
const gridHeight = (view,count) => {const row = view === "incidents" ? 64 : 52; return Math.min(584, Math.max(244, (Math.min(count, 10) + 1) * row + 144));};

const loads = new WeakMap();
export function loadVendor(document) {
  if (document?.defaultView?.agGrid?.createGrid) return Promise.resolve(document.defaultView.agGrid);
  if (!document?.createElement || !document?.head) return Promise.resolve(null);
  if (loads.has(document)) return loads.get(document);
  const load = new Promise(resolve => {
    for (const href of ["vendor/ag-grid.css", "vendor/ag-theme-quartz.css", "grid.css"]) {
      const link = document.createElement("link"); link.rel = "stylesheet"; link.href = href; document.head.append(link);
    }
    const script = document.createElement("script"); script.src = VENDOR; script.async = true;
    let finished = false;
    const done = value => {if (!finished) {finished = true; clearTimeout(timer); resolve(value);}};
    const timer = setTimeout(() => done(null), 8000);
    script.onload = () => done(document.defaultView?.agGrid?.createGrid ? document.defaultView.agGrid : null);
    script.onerror = () => done(null);
    document.head.append(script);
  });
  loads.set(document, load);
  return load;
}

const mounted = new WeakMap();
// The fallback HTML is already rendered by the existing view. It stays visible
// during loading and on failure; it is hidden only after createGrid succeeds.
export async function mountGrid(host, view, rows, options = {}) {
  if (!host?.ownerDocument || !host?.isConnected) return null;
  const {vendorLoader = loadVendor, ...settings} = options;
  const document = host.ownerDocument;
  const token = {};
  mounted.get(host)?.destroy();
  const fallback = [...host.children];
  let api = null, viewport = null, note = null, disposed = false, observer = null, motionQuery = null, motionChange = null;
  let currentRows=rows, controls=null, active="all", storage=null, key=null, initialized=false;
  const summarize = () => {
    if (!api?.forEachNodeAfterFilterAndSort) return;
    const visible=[]; api.forEachNodeAfterFilterAndSort(n => {if (!n.rowPinned) visible.push(n.data);});
    api.setGridOption("pinnedBottomRowData",[filteredTotal(visible,currentRows.length,moneyText)]);
    const counts=viewCounts(view,currentRows);
    const canonical=value => value && typeof value === "object" ? (Array.isArray(value) ? value.map(canonical) : Object.fromEntries(Object.keys(value).sort().map(k => [k,canonical(value[k])]))) : value;
    const model=JSON.stringify(canonical(api.getFilterModel?.() || {}));
    active=savedViews(view).find(v => JSON.stringify(canonical(v.filters || {})) === model)?.id || "custom";
    for (const chip of controls?.querySelectorAll?.("[data-c66-view]") || []) {
      chip.textContent=savedViews(view).find(v => v.id === chip.dataset.c66View).label+" ("+counts[chip.dataset.c66View]+")";
      chip.setAttribute("aria-pressed",String(chip.dataset.c66View === active));
    }
    const count=controls?.querySelector?.("[data-c66-count]");
    if (count) count.textContent=`Showing ${visible.length} of ${currentRows.length} \u00b7 ${filteredTotal(visible,currentRows.length,moneyText).amount_total}`;
  };
  // C69: only a viewer's own drag saves a width; stretching columns keep filling the grid at any window size.
  const persist = event => {if (initialized && event?.finished !== false) saveLayout(storage,key,api.getColumnState().map(({width,...column}) => column.flex ? column : {...column,width}));};
  const persistResize = event => {if (event?.source === "uiColumnResized") persist(event);};
  const fitHeight = count => {if (viewport) viewport.style.height = gridHeight(view,count)+"px";};
  const handle = {
    get api() {return api;},
    setQuickFilter(text) {
      api?.setGridOption("quickFilterText", text);
      const input=controls?.querySelector?.("[data-c66-search]");if(input)input.value=text;
    },
    update(next, nextSettings = {}) {
      Object.assign(settings,nextSettings);
      if (!api) return false;
      const delta=changedRows(currentRows,next); currentRows=next;
      api.applyTransaction(delta); fitHeight(next.length); summarize();
      if(settings.onFilterChanged) settings.onFilterChanged({api});
      return true;
    },
    destroy() {
      disposed = true; observer?.disconnect(); motionQuery?.removeEventListener?.("change", motionChange);
      api?.destroy(); viewport?.remove(); note?.remove(); controls?.remove();
      for (const child of fallback) child.hidden = false;
      if (mounted.get(host) === handle) mounted.delete(host);
    }, token,
  };
  mounted.set(host, handle);
  host.dataset.gridState = "loading";
  const library = await vendorLoader(document);
  if (disposed || !host.isConnected || mounted.get(host)?.token !== token) return null;
  if (settings.isActive?.() === false) {handle.destroy(); host.dataset.gridState = "idle"; return null;}
  if (!library?.createGrid) {
    host.dataset.gridState = "fallback";
    note = document.createElement("p"); note.className = "source-note";
    note.textContent = "Interactive grid unavailable. Showing the standard table."; host.append(note);
    return handle;
  }
  try {
    viewport = document.createElement("div"); viewport.className = "ag-theme-quartz cs-grid";
    fitHeight(rows.length);
    viewport.setAttribute("aria-label", {incidents:"Incident desk", audit:"Execution history", runs:"Payout run lines", makers:"Makers"}[view]);
    host.append(viewport);
    motionQuery = document.defaultView.matchMedia?.("(prefers-reduced-motion: reduce)");
    api = library.createGrid(viewport, {...gridOptions(view, rows, {...settings,
      onOpen:row => settings.onOpen?.(row),
      onFilterChanged:event => {active="custom"; summarize();settings.onFilterChanged?.(event);},
      reducedMotion:Boolean(motionQuery?.matches)}),
      onModelUpdated:summarize,onSortChanged:event => {active="custom";summarize();persist(event);},
      onColumnMoved:persist,onColumnResized:persistResize,onColumnPinned:persist,onColumnVisible:persist});
    // C66: viewer means this browser's local profile; no account/email is persisted.
    try {storage=document.defaultView.localStorage;} catch {}
    key=layoutKey(view,settings.viewer || "local-viewer");
    const layout=readLayout(storage,key);
    if (layout) api.applyColumnState?.({state:layout,applyOrder:true});
    controls=document.createElement("div");controls.className="cs-grid-showcase";
    controls.innerHTML=`<div class="cs-grid-views" role="group" aria-label="Saved views">${savedViews(view).map(v => `<button type="button" data-c66-view="${v.id}" aria-pressed="${v.id === active}">${esc(v.label)}</button>`).join("")}</div>
      <div class="cs-grid-tools">${settings.search === false ? "" : '<label>Search records<input type="search" data-c66-search></label>'}<button type="button" data-c66-export>Export view CSV</button><button type="button" data-c66-reset>Reset layout</button></div>
      <p data-c66-count role="status"></p><p class="cs-grid-help">Shift-click to sort by several columns. Arrow keys move; Enter opens records. Money totals reflect visible loaded records, not a balance.${view === "runs" ? " All includes paid, waiting and held shares; use a saved view to separate them." : ""}</p>`;
    host.prepend(controls);
    for (const chip of controls.querySelectorAll?.("[data-c66-view]") || []) chip.onclick=() => {
      const chosen=savedViews(view).find(v => v.id === chip.dataset.c66View);
      api.setGridOption("quickFilterText","");
      const search=controls.querySelector("[data-c66-search]");if(search)search.value="";
      api.setFilterModel(chosen.filters);
      api.applyColumnState({state:chosen.sort,defaultState:{sort:null}});
      active=chosen.id;api.paginationGoToFirstPage();summarize();settings.onViewChosen?.();
    };
    const search=controls.querySelector?.("[data-c66-search]");
    if(search){search.value=settings.quickFilterText || "";search.oninput=e => handle.setQuickFilter(e.target.value);}
    const exportButton=controls.querySelector?.("[data-c66-export]");if(exportButton)exportButton.onclick=() => api.exportDataAsCsv();
    const reset=controls.querySelector?.("[data-c66-reset]");if(reset)reset.onclick=() => {initialized=false;clearLayout(storage,key);api.resetColumnState();initialized=true;summarize();};
    initialized=true;summarize();
    for (const child of fallback) child.hidden = true;
    host.dataset.gridState = "ready";
    motionChange = () => {
      api?.setGridOption("cellFlashDuration",motionQuery.matches ? 0 : 700);
      api?.setGridOption("cellFadeDuration",motionQuery.matches ? 0 : 500);
      api?.setGridOption("defaultColDef",{sortable:true,resizable:true,suppressMovable:false,cellDataType:false,enableCellChangeFlash:!motionQuery.matches});
    };
    motionQuery?.addEventListener?.("change", motionChange);
    // Grid resources are destroyed when a route/refresh replaces its host.
    const Observer = document.defaultView.MutationObserver;
    if (Observer) {observer = new Observer(() => {if (!host.isConnected) handle.destroy();}); observer.observe(document.body, {childList:true, subtree:true});}
    return handle;
  } catch {
    handle.destroy(); host.dataset.gridState = "fallback";
    return null;
  }
}

export function auditToolbar(entries) {
  const actors = [...new Set(entries.map(e => e.actor).filter(Boolean))].sort();
  return `<div class="cs-grid-tools"><label>Verdict<select data-audit-verdict><option value="">All verdicts</option><option value="ALLOW">Allowed</option><option value="BLOCK">Stopped</option><option value="NEEDS_OWNER">Needs owner</option></select></label><label>Actor<select data-audit-actor><option value="">All actors</option>${actors.map(a => `<option value="${esc(a)}">${esc(a)}</option>`).join("")}</select></label><label>Search history<input type="search" data-grid-search placeholder="Action, rule or reference"></label></div>`;
}

export async function mountAuditGrid(host, entries, options = {}) {
  const handle = await mountGrid(host, "audit", auditRows(entries), options);
  if (!handle?.api || !host.isConnected) return handle;
  return handle;
}
