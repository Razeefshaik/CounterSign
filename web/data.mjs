export const label = value => String(value ?? "").toLowerCase().replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase());
export function filterIncidents(rows, {state = "", kind = "", query = ""} = {}) {
  const term = query.trim().toLowerCase();
  return rows.filter(row => (!state || row.state === state) && (!kind || row.kind === kind) &&
    (!term || [row.title, row.subject?.paypal_dispute_id, row.subject?.payout_item_id, row.kind].some(value => String(value ?? "").toLowerCase().includes(term))));
}
export function summary(rows) {
  return {open: rows.filter(row => row.state !== "RESOLVED").length,
    owner: rows.filter(row => row.state === "AWAITING_OWNER").length};
}
export function money(value) {
  if (!value || !Number.isSafeInteger(value.minor)) throw new Error("Invalid money");
  const sign = value.minor < 0 ? "-" : "";
  const n = Math.abs(value.minor);
  return sign + "$" + Math.trunc(n / 100).toLocaleString("en-US") + "." + String(n % 100).padStart(2, "0");
}
// Reconciliation incidents carry no amount at risk; the grid shows a dash, not an error.
export const atRisk = value => value == null ? "—" : money(value);
export function isStaged(row, disputes) {
  return disputes.some(d => d.id === row.subject?.dispute_id && d.staged === true);
}
export function modeLabel(meta) {
  if (meta.mode === "demo" && meta.paypal_env === "mock" && meta.simulated === true) return "Demo · mock PayPal";
  if (meta.mode === "live" && meta.paypal_env === "sandbox" && meta.simulated === false) return "Live · PayPal sandbox";
  throw new Error("Unrecognized workspace mode");
}
export async function loadWorkspace(fetcher = fetch) {
  const read = async url => {const response = await fetcher(url); if (!response.ok) throw new Error("Cannot load " + url); return response.json();};
  let fixture = false, meta;
  try { meta = await read("/api/v1/meta"); }
  catch {fixture = true; meta = await read("fixtures/meta.json");}
  modeLabel(meta);
  const source = name => fixture ? "fixtures/" + name + ".json" : "/api/v1/" + name;
  const all = async name => {
    let url = source(name), rows = [];
    do {
      const page = await read(url); rows.push(...page.items);
      url = !fixture && page.next_cursor ? source(name) + "?cursor=" + encodeURIComponent(page.next_cursor) : null;
    } while (url);
    return rows;
  };
  const [incidents, disputes] = await Promise.all([all("incidents"), all("disputes")]);
  return {meta, incidents, disputes, fixture};
}
