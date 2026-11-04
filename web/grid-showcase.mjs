// C66: Community-only operational views. No inferred payments or API writes.
const text = (filter, type = "equals") => ({filterType:"text", type, filter});
const positive = {filterType:"number", type:"greaterThan", filter:0};
const sort = (colId, direction = "asc") => [{colId, sort:direction}];
export function savedViews(view) {
  const all = {id:"all", label:"All", filters:null, sort:[]};
  const spec = (id, label, filters, order = []) => ({id,label,filters,sort:order});
  if (view === "incidents") return [spec("owner","Needs owner",{state:text("AWAITING_OWNER")},sort("updated","desc")),
    spec("held","Money held",{held_minor:positive},sort("held_minor","desc")),
    spec("blocked","Blocked",{verdict:text("BLOCK")},sort("amount_minor","desc")),all];
  if (view === "audit") return [spec("blocked","Blocked by shield",{verdict:text("BLOCK"),actor:text("shield")},sort("at","desc")),
    spec("signed","Owner signed",{event_kind:{filterType:"text",operator:"OR",conditions:[text("OWNER_APPROVED"),text("OWNER_COUNTERSIGNED")]}},sort("at","desc")),
    spec("writes","PayPal writes",{event_kind:text("PAYPAL_RESPONSE")},sort("at","desc")),all];
  if (view === "runs") return [spec("waiting","Waiting",{waiting:text("yes")},sort("maker")),
    spec("held","Held back",{state:text("RESERVED")},sort("amount_minor","desc")),
    spec("paid","Paid",{paid:text("yes")},sort("amount_minor","desc")),all];
  if (view === "makers") return [spec("held","Has held share",{held_minor:positive},sort("held_minor","desc")),
    spec("unverified","Unverified payee",{unverified:text("yes")},sort("maker")),all];
  throw Error("Unknown grid view");
}
export function matchesModel(row, model) {
  return Object.entries(model || {}).every(([field,m]) => {
    const one = condition => {
      if (condition.type === "greaterThan") return Number.isSafeInteger(row[field]) && row[field] > condition.filter;
      const value = String(row[field] ?? "").toLowerCase(), expected = String(condition.filter).toLowerCase();
      return condition.type === "contains" ? value.includes(expected) : value === expected;
    };
    return m.conditions ? (m.operator === "OR" ? m.conditions.some(one) : m.conditions.every(one)) : one(m);
  });
}
export const viewCounts = (view,rows) => Object.fromEntries(savedViews(view).map(v => [v.id, rows.filter(r => matchesModel(r,v.filters)).length]));
export const minorCompare = (a,b) => a === b ? 0 : a == null ? -1 : b == null ? 1 : a < b ? -1 : 1;
export function dateFilterCompare(localMidnight,value) {
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) return -1;
  return Math.sign(Date.UTC(parsed.getUTCFullYear(),parsed.getUTCMonth(),parsed.getUTCDate()) -
    Date.UTC(localMidnight.getFullYear(),localMidnight.getMonth(),localMidnight.getDate()));
}
// Keep each currency separate, never add absent amounts or invent USD for an empty sum.
export function filteredTotal(rows, allCount, format) {
  const sums = {};
  for (const row of rows) for (const field of ["amount","held"]) {
    const n = row[field+"_minor"], c = row[field+"_currency"];
    if (!Number.isSafeInteger(n) || !/^[A-Z]{3}$/.test(c)) continue;
    const key = field+"_"+c, sum = (sums[key] ?? 0) + n;
    sums[key] = Number.isSafeInteger(sum) ? sum : NaN;
  }
  const text = field => Object.entries(sums).filter(([k]) => k.startsWith(field+"_"))
    .map(([k,v]) => Number.isSafeInteger(v) ? format(v,k.slice(field.length+1)) : "Total unavailable").join(" · ") || "—";
  return {id:"c66-total", incident:`Showing ${rows.length} of ${allCount}`,maker:`Showing ${rows.length} of ${allCount}`,
    at:null,actor:`Showing ${rows.length} of ${allCount}`, amount_total:text("amount"), held_total:text("held")};
}
// Neutralise leading controls/whitespace as well as the four spreadsheet operators.
export const csvSafe = value => /^[\s\u0000-\u001f]*[=+\-@]|^[\t\r\n]/.test(String(value ?? "")) ? "'"+value : String(value ?? "");
export function layoutKey(view, viewer = "local-viewer") {return `countersign:c69:layout:${viewer}:${view}`;}
export function readLayout(storage,key) {
  try {
    const value = JSON.parse(storage?.getItem(key) || "null");
    if (!Array.isArray(value) || value.length > 40) return null;
    return value.filter(c => c && typeof c.colId === "string").map(c => ({colId:c.colId,
      ...(Number.isFinite(c.width) ? {width:Math.max(80,Math.min(700,c.width))} : {}),
      hide:c.hide === true,pinned:["left","right"].includes(c.pinned) ? c.pinned : null,
      sort:["asc","desc"].includes(c.sort) ? c.sort : null,sortIndex:Number.isInteger(c.sortIndex) ? c.sortIndex : null}));
  } catch {return null;}
}
export function saveLayout(storage,key,value) {try {storage?.setItem(key,JSON.stringify(value)); return Boolean(storage);} catch {return false;}}
export function clearLayout(storage,key) {try {storage?.removeItem(key);} catch {}}
export function changedRows(previous,next) {
  const before = new Map(previous.map(r => [String(r.id),r])), after = new Set(next.map(r => String(r.id)));
  return {add:next.filter(r => !before.has(String(r.id))),
    update:next.filter(r => before.has(String(r.id)) && JSON.stringify(before.get(String(r.id))) !== JSON.stringify(r)),
    remove:previous.filter(r => !after.has(String(r.id)))};
}
