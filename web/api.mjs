// All requests stay on the dashboard's origin. No PayPal URLs or credentials.
export class ApiError extends Error {
  constructor(status, code) { super(code || `Request failed (${status})`); this.status = status; }
}

export function createClient({fixture = false, fetcher = fetch} = {}) {
  async function read(url) {
    const response = await fetcher(url, {cache: "no-store"});
    if (!response.ok) throw new ApiError(response.status);
    return response.json();
  }
  return {
    async list(resource) {
      const base = `/api/v1/${resource}`;
      let url = fixture ? `fixtures/${resource}.json` : base;
      const items = [], cursors = new Set();
      while (url) {
        const page = await read(url); items.push(...page.items);
        if (!fixture && page.next_cursor) {
          if (cursors.has(page.next_cursor)) throw new Error("Repeated pagination cursor");
          cursors.add(page.next_cursor);
          url = `${base}?cursor=${encodeURIComponent(page.next_cursor)}`;
        } else url = null;
      }
      return items;
    },
    async get(resource, id) {
      if (!fixture) return read(`/api/v1/${resource}/${encodeURIComponent(id)}`);
      const data = await read(resource === "payout-runs" ? "fixtures/run-details.json" : `fixtures/${resource}.json`);
      const item = (Array.isArray(data) ? data : data.items).find(row => row.id === id);
      if (!item) throw new ApiError(404, "NOT_FOUND");
      return item;
    },
    async singleton(resource, params = {}) {
      const query = new URLSearchParams(params).toString();
      const data = await read(fixture ? `fixtures/${resource}.json` : `/api/v1/${resource}${query ? "?" + query : ""}`);
      if (fixture && resource === "money-graph" && params.dispute_id &&
          !data.nodes.some(n => n.id === `dispute:${params.dispute_id}`)) return {nodes: [], edges: []};
      return data;
    },
    async decide(proposal, decision, {reason = "", token = ""} = {}) {
      if (!["approve", "reject"].includes(decision)) throw new Error("Invalid decision");
      const headers = {"Content-Type": "application/json"};
      if (token) headers["X-Owner-Token"] = token;
      const body = decision === "approve"
        ? {payload_hash: proposal.approval_request?.payload_hash, approved_by: "owner"}
        : {reason: reason.trim()};
      if (decision === "approve" && !body.payload_hash) throw new Error("Reload the proposal before approving");
      if (decision === "reject" && !body.reason) throw new Error("Enter a reason for rejection");
      const response = await fetcher(`/api/v1/proposals/${encodeURIComponent(proposal.id)}/${decision}`, {
        method: "POST", headers, body: JSON.stringify(body),
      });
      if (!response.ok) {
        // Do not surface raw server messages: they may contain private data.
        throw new ApiError(response.status);
      }
      return response.json();
    },
  };
}

// A 409 always invalidates the displayed document, even if its replacement GET fails.
// No automatic retry: a fresh proposal requires a new owner decision.
export async function submitDecision(client, proposal, decision, options, invalidate, reload) {
  try {
    const result = await client.decide(proposal, decision, options);
    invalidate();
    await reload();
    return {stale: false, result};
  } catch (error) {
    if (error.status !== 409) throw error;
    invalidate();
    await reload();
    return {stale: true};
  }
}
