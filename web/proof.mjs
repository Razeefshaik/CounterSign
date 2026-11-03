// C50: recorded evidence, shipped as a static bundle. No live PayPal/model calls.
import {esc} from "./views.mjs";

const GITHUB = "https://github.com/Razeefshaik/CounterSign/blob/main/";
export function sourceURL(path, pointer = "") {
  if (typeof path !== "string" || (!/^(recordings|docs|scripts)\/[A-Za-z0-9_./-]+$/.test(path) && path !== "web/results/sandbox-proof.json") ||
      path.split("/").some(part => !part || part === "." || part === "..")) return null;
  const line = /^line:(\d+)/.exec(pointer);
  return GITHUB + path.split("/").map(encodeURIComponent).join("/") + (line ? `#L${line[1]}` : "");
}
const value = fact => fact?.value ?? null;
const text = fact => value(fact) == null ? "—" : esc(value(fact));
const inline = fact => text(fact).replace(/`([^`]+)`/g, "<code>$1</code>");
const number = fact => Number.isSafeInteger(value(fact)) && value(fact) >= 0
  ? value(fact).toLocaleString("en-US") : "—";
export function sourceLink(fact, label = "Recording") {
  // An aggregate's complete source list is in the committed bundle, rather than
  // presenting one sample exchange as proof of an entire count.
  const url = sourceURL(fact?.source || (fact?.sources?.length ? "web/results/sandbox-proof.json" : null), fact?.pointer);
  return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}<span class="sr-only"> (opens GitHub)</span></a>` : "";
}
const date = fact => typeof value(fact) === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value(fact))
  ? value(fact).slice(0, 10) : "—";

function checkView(check) {
  const changed = value(check.verdict)?.includes("DIFFERENT");
  const different = changed && !value(check.verdict)?.startsWith("CONFIRMED");
  const rows = (check.resources || []).map(resource => `<tr>
    <td>${esc(resource.kind?.replaceAll("_", " ") || "—")}</td>
    <td><code>${text(resource.id)}</code>${sourceLink(resource.id, "Source")}</td>
    <td><span class="proof-state">${text(resource.status)}</span>${resource.outcome ? `<code class="proof-outcome">${text(resource.outcome)}</code>` : ""}</td>
  </tr>`).join("");
  const verification = check.verification_counts ? `<p class="proof-signatures">Signature responses:
    ${Object.entries(value(check.verification_counts) || {}).map(([status, count]) => `${esc(status)} <strong>${esc(count)}</strong>`).join(" · ")}
    ${sourceLink(check.verification_counts, "Verification recording")}</p>` : "";
  return `<article class="proof-check view-card" id="proof-${esc(check.id)}" aria-labelledby="proof-title-${esc(check.id)}">
    <header class="proof-check-head"><h3 id="proof-title-${esc(check.id)}"><span>${esc(check.id)}</span> ${text(check.topic)}</h3>
      <span class="proof-stamp ${different ? "proof-different" : "proof-confirmed"}">${text(check.verdict)}</span></header>
    <p class="proof-finding">${inline(check.finding)} ${sourceLink(check.finding, "Finding")}</p>
    <p class="proof-meta">${number(check.exchanges)} exchange attempts <span aria-hidden="true">·</span> ${esc(date(check.date_start))} to ${esc(date(check.date_end))} (UTC)
      ${sourceLink(check.debug_ids?.[0] || check.exchanges)}</p>
    ${changed ? `<div class="proof-impact"><strong>What we changed</strong><p>${inline(check.design_impact)} ${sourceLink(check.design_impact, "Design impact")}</p></div>` : ""}
    ${verification}<p class="proof-debug">Sample PayPal debug IDs ${check.debug_ids?.length ? check.debug_ids.map(id => `<code>${text(id)}</code> ${sourceLink(id, "Source")}`).join(" ") : "—"}</p>
    ${rows ? `<details class="proof-resources"><summary>${check.resources.length} recorded resource IDs and latest observed statuses${check.id === "D7" ? " (negative-test fixtures)" : ""}</summary>
      <div class="proof-table"><table><thead><tr><th scope="col">Resource</th><th scope="col">PayPal ID</th><th scope="col">Recorded status / outcome</th></tr></thead><tbody>${rows}</tbody></table></div></details>` : ""}
  </article>`;
}

export function proofView(data = {}) {
  const summary = data.summary || {}, checks = Array.isArray(data.checks) ? data.checks : [];
  const start = checks.map(c => date(c.date_start)).filter(d => d !== "—").sort()[0] || "—";
  const end = checks.map(c => date(c.date_end)).filter(d => d !== "—").sort().at(-1) || "—";
  const metrics = [["checks_run", "Checks run"], ["http_exchanges", "HTTP exchange attempts"],
    ["signatures_verified", "Signatures verified"], ["payouts_succeeded", "Payouts reached SUCCESS"],
    ["disputes_settled", "Disputes settled"]];
  const model = data.real_model || {};
  return `<div class="proof-page">
    <header class="proof-heading"><h2>Verified on the real PayPal sandbox</h2>
      <p>Recorded sandbox runs, <time>${esc(start)}</time> to <time>${esc(end)}</time>. These are committed recordings, not live calls from this demo.</p>
      <a href="results/sandbox-proof.json" target="_blank" rel="noopener">Inspect the source-linked data bundle</a></header>
    <dl class="proof-summary glass">${metrics.map(([key, label]) => `<div><dt>${label}</dt><dd>${number(summary[key])}</dd><span>${sourceLink(summary[key], "Sources")}</span></div>`).join("")}</dl>
    <p class="proof-scope">${number(summary.http_responses)} HTTP responses; ${number(summary.transport_errors)} transport errors.
      Signature checks include one simulator event. Payout totals count unique successful items, excluding negative-test fixtures. Settlement includes buyer cancellation.</p>
    <nav class="proof-jump" aria-label="Sandbox checks">${checks.map(c => `<a href="#proof/${esc(c.id)}">${esc(c.id)}</a>`).join("")}</nav>
    <div class="proof-checks">${checks.length ? checks.map(checkView).join("") : '<p class="empty">No recorded sandbox checks are available.</p>'}</div>
    <section class="proof-model view-card" aria-labelledby="proof-model-title">
      <header><h3 id="proof-model-title">Real model week</h3><span>Real model · mock PayPal</span></header>
      <p><code>${text(model.model)}</code> ${sourceLink(model.model, "Run log")} <span class="proof-model-status">${text(model.status)}</span></p>
      <p>PayPal ground: <strong>${text(model.ground)}</strong>. This is separate from the sandbox recordings above.</p>
      <ol>${(model.decisions || []).map(row => `<li><div><strong>${inline(row.decision)}</strong><p>${inline(row.outcome)}</p></div>
        <div class="proof-model-result">${row.shield_outcome ? `<span>${text(row.shield_outcome)}</span>` : row.observed_action ? `<span>Chose ${text(row.observed_action)}</span>` : ""}
        ${sourceLink(row.shield_outcome || row.observed_action || row.outcome, "Evidence")}</div></li>`).join("")}</ol>
      <p class="proof-scope">The model chose WAIT for the injection and settled-retry prompts; those two shield-block moments were not demonstrated in this real-model run. ${sourceLink(model.status, "Completion status")}</p>
    </section>
    <footer class="proof-footer">Rerun the checks with <code>scripts/sandbox_checks</code> in your own sandbox.
      ${sourceLink({source: "docs/SANDBOX_FINDINGS.md"}, "Findings and limitations")} · ${sourceLink({source: "docs/PLAN.md"}, "Sandbox check plan")}
      <p>Tracking was merchant-uploaded. Disputes on already-available captures remain untested; transaction-search lag was blocked.</p></footer>
  </div>`;
}

export function createProof(document, fetcher = fetch) {
  let data;
  return {
    async load() {
      if (data) return;
      const response = await fetcher("results/sandbox-proof.json", {cache: "no-store"});
      if (!response.ok) throw new Error("PROOF_UNAVAILABLE");
      const next = await response.json();
      if (next.schema_version !== 1 || !Array.isArray(next.checks) || !next.summary) throw new Error("PROOF_INVALID");
      data = next;
    },
    draw(id = "") {
      document.getElementById("view-body").innerHTML = proofView(data);
      if (/^D(?:[1-9]|1[0-3])$/.test(id)) {
        document.getElementById("proof-" + id)?.scrollIntoView({block: "start", behavior: "instant"});
      }
    },
  };
}
