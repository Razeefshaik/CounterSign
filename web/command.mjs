// C37 command center controller: loads the read-only dashboard API, draws the
// money constellation and explains whatever the viewer selects. No writes.
import {sceneModel, focusModel, focusSvg, defaultCategory, nodePanel, makerPanel, itemPanel, storyPanel, kpis, short} from "./scene.mjs";
import {plotSvg, scanSvg, scanTally, folios, stands, activity, finding, progress, makerAt, PW, PH} from "./plot.mjs";
import {weekDays} from "./words.mjs";
import {esc} from "./views.mjs";

const optional = promise => promise.catch(() => null);

export function createCommand(document, fetcher = fetch) {
  const $ = id => document.getElementById(id);
  const view = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  const motion = () => !view?.matches;
  let data = null, model = null, previous = null, selected = "", focus = "", category = "", item = "", fm = null, shown = false, serial = 0;

  async function json(url) {
    const response = await fetcher(url, {cache: "no-store"});
    if (!response.ok) throw Object.assign(new Error("Unavailable"), {status: response.status});
    return response.json();
  }

  async function load(workspace, client) {
    const live = !workspace.fixture;
    const [makers, proposals, cash, demo, comparison] = await Promise.all([
      optional(client.list("makers")), optional(client.list("proposals")), optional(client.singleton("cash")),
      live ? optional(json("/api/v1/demo/state")) : null, live ? optional(json("/api/v1/demo/comparison")) : null,
    ]);
    const graphs = {};
    await Promise.all((makers || []).slice(0, 6).map(async m => {
      graphs[m.id] = await optional(client.singleton("money-graph", {maker_id: m.id}));
    }));
    return {makers: makers || [], proposals: proposals || [], cash, demo, comparison, graphs, disputes: workspace.disputes || []};
  }

  function countUp() {
    const from = new Map((previous ? kpiValues(previous) : []).map(([k, v]) => [k, v]));
    for (const dd of document.querySelectorAll("#kpis dd[data-count]")) {
      const target = Number(dd.dataset.count), key = dd.closest(".kpi").classList[1];
      const start = from.has(key) ? from.get(key) : 0;
      if (!dd.dataset.count || !Number.isSafeInteger(target) || start === target || !motion()) continue;
      const t0 = performance.now(), dur = 900;
      const tick = now => {
        const p = Math.min(1, (now - t0) / dur), eased = 1 - Math.pow(1 - p, 3);
        dd.textContent = short(Math.round(start + (target - start) * eased));
        if (p < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      dd.closest(".kpi").classList.add("bumped");
    }
  }
  const kpiValues = m => [["paypal", m.paypal.available], ["paid", m.totals.paid], ["paying", m.totals.paying], ["held", m.held.total]];

  function changed() {
    if (!previous) return;
    for (const m of model.makers) {
      const before = previous.makers.find(x => x.id === m.id);
      if (before && (before.paid !== m.paid || before.paying !== m.paying || before.held !== m.held)) {
        document.querySelector(`[data-node="maker:${CSS.escape(m.id)}"]`)?.classList.add("changed");
      }
    }
    if (previous.agent.blocked !== model.agent.blocked || previous.agent.proposals !== model.agent.proposals) document.querySelector('[data-node="shield"]')?.classList.add("changed");
  }

  function panel() {
    const back = `<button type="button" class="panel-back" data-action="story">← Back to the week</button>`;
    let html;
    if (focus && fm) html = (item ? `<button type="button" class="panel-back" data-action="maker">← ${esc(fm.maker.name)}</button>` + itemPanel(fm, item) : back + makerPanel(fm.maker));
    else if (selected) html = back + nodePanel(model, selected, {cash: data.cash});
    else html = storyPanel({demo: data.demo, comparison: model.comparison, model});
    $("panel").innerHTML = `<div class="panel-inner${motion() ? " reveal" : ""}">${html}</div>`;
  }

  function stage({entrance = false, swap = ""} = {}) {
    const stageEl = $("stage");
    if (focus && fm) {
      stageEl.innerHTML = focusSvg(fm, {category, item, motion: motion()});
      $("scene-back").hidden = false;
    } else {
      stageEl.innerHTML = plotSvg(model, {selected, motion: motion()});
      $("scene-back").hidden = true;
    }
    stageEl.classList.toggle("entering", entrance && motion());
    stageEl.classList.remove("zoom-in", "zoom-out");
    if (swap && motion()) {void stageEl.offsetWidth; stageEl.classList.add(swap);}
    const caption = $("scene-caption");
    caption.hidden = !model.empty;
    caption.textContent = data.demo ? "The marketplace is empty. Run the first staged step to open the week." : "No makers yet. The map fills in as orders arrive.";
  }

  // Optional pieces: a layout that leaves one out simply has no element for it.
  const fill = (id, html) => {const el = $(id); if (el) el.innerHTML = html;};
  function pieces() {
    fill("ov-finding", finding(model));
    fill("scan", scanSvg(model));
    fill("scan-tally", scanTally(model));
    fill("folios", folios(model, focus));
    fill("stands", stands(model));
    fill("activity", activity(model));
  }

  function draw({entrance = false, swap = ""} = {}) {
    $("kpis").innerHTML = kpis(model);
    $("rail-slot").innerHTML = progress(data.demo, weekDays);
    pieces();
    stage({entrance, swap});
    panel();
    countUp();
    changed();
  }

  function openMaker(id) {
    const maker = model.makers.find(m => "maker:" + m.id === id);
    if (!maker) return;
    const [x, y] = makerAt(model.makers.indexOf(maker), model.makers.length);
    $("stage").style.setProperty("--ox", (x / PW * 100).toFixed(1) + "%");
    $("stage").style.setProperty("--oy", (y / PH * 100).toFixed(1) + "%");
    focus = maker.id; selected = ""; item = "";
    fm = focusModel(data.graphs[maker.id], maker);
    category = defaultCategory(fm);
    draw({swap: "zoom-in"});
  }

  function closeFocus() {
    if (!focus) return;
    focus = ""; fm = null; item = ""; category = "";
    draw({swap: "zoom-out"});
  }

  function activate(target) {
    const node = target.closest?.("[data-node]"), card = target.closest?.("[data-category]"), it = target.closest?.("[data-item]");
    if (it && fm) {item = it.dataset.item; stage(); panel(); return true;}
    if (card && fm) {category = card.dataset.category; item = ""; stage(); panel(); return true;}
    if (node && !focus) {
      const id = node.dataset.node;
      if (id.startsWith("maker:")) openMaker(id);
      else {selected = selected === id ? "" : id; stage(); panel();}
      return true;
    }
    return false;
  }

  function bind() {
    const stageEl = $("stage");
    stageEl.onclick = event => activate(event.target);
    stageEl.onkeydown = event => {
      if ((event.key === "Enter" || event.key === " ") && activate(event.target)) event.preventDefault();
    };
    $("scene-back").onclick = closeFocus;
    const folioEl = $("folios");
    if (folioEl) folioEl.onclick = event => {
      const id = event.target.closest?.("[data-node]")?.dataset.node;
      if (!id) return;
      if (focus === id.slice(6)) {closeFocus(); return;}
      focus = ""; fm = null;
      openMaker(id);
      if (!$("stage").getBoundingClientRect || $("stage").getBoundingClientRect().top < 0) $("stage").scrollIntoView({behavior: motion() ? "smooth" : "auto", block: "center"});
    };
    $("command").onkeydown = event => {
      if (event.key !== "Escape") return;
      if (item) {item = ""; stage(); panel();} else if (focus) closeFocus(); else if (selected) {selected = ""; stage(); panel();}
    };
    $("panel").onclick = event => {
      const action = event.target.closest?.("[data-action]")?.dataset.action;
      if (action === "story") {selected = ""; if (focus) closeFocus(); else {stage(); panel();}}
      if (action === "maker") {item = ""; stage(); panel();}
    };
  }

  return {
    async show(workspace, client) {
      const ticket = ++serial;
      bind();
      if (!shown) $("stage").innerHTML = '<p class="scene-loading">Drawing the money constellation…</p>';
      const next = await load(workspace, client);
      if (ticket !== serial) return;
      data = next;
      previous = model;
      model = sceneModel(data);
      if (focus) {
        const maker = model.makers.find(m => m.id === focus);
        if (maker) fm = focusModel(data.graphs[maker.id], maker); else {focus = ""; fm = null;}
      }
      if (selected && !nodePanel(model, selected, {cash: data.cash})) selected = "";
      draw({entrance: !shown});
      shown = true;
    },
  };
}
