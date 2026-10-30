// C37 command center controller: loads the read-only dashboard API, draws the
// money constellation and explains whatever the viewer selects. No writes.
import {sceneModel, focusModel, focusSvg, defaultCategory, nodePanel, makerPanel, itemPanel, storyPanel, kpis, short} from "./scene.mjs";
import {plotSvg, scanSvg, scanTally, folios, stands, activity, progress, makerAt, PW, PH} from "./plot.mjs";
import {weekDays} from "./words.mjs";
import {esc} from "./views.mjs";
import {weekTimeline, trailRows, trailDetail} from "./trail.mjs";

const optional = promise => promise.catch(() => null);

export function createCommand(document, fetcher = fetch) {
  const $ = id => document.getElementById(id);
  const view = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)");
  let keyboard = false;
  const motion = () => !view?.matches && !keyboard;
  let data = null, model = null, previous = null, selected = "", focus = "", category = "", item = "", fm = null, shown = false, serial = 0;
  let trailFilter = "all", trailSelected = "";

  async function json(url) {
    const response = await fetcher(url, {cache: "no-store"});
    if (!response.ok) throw Object.assign(new Error("Unavailable"), {status: response.status});
    return response.json();
  }

  async function load(workspace, client) {
    const live = !workspace.fixture;
    const [makers, proposals, cash, demo, comparison] = await Promise.all([
      client.list("makers"), client.list("proposals"), optional(client.singleton("cash")),
      live ? optional(json("/api/v1/demo/state")) : null, live ? optional(json("/api/v1/demo/comparison")) : null,
    ]);
    const graphs = {};
    await Promise.all((makers || []).slice(0, 6).map(async m => {
      graphs[m.id] = await optional(client.singleton("money-graph", {maker_id: m.id}));
    }));
    return {makers: makers || [], proposals: proposals || [], cash, demo, comparison, graphs, disputes: workspace.disputes || []};
  }

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
    fill("trail-week-slot", weekTimeline(data.demo));
    fill("trail-body", trailRows(model, trailFilter, trailSelected));
    fill("trail-detail", trailDetail(model, trailSelected));
    fill("trail-count", `${model.agent.proposals} recorded`);
    fill("trail-attention", model.owner.waiting.length ? `<a href="#decision/${esc(model.owner.waiting[0].id)}">${esc(model.owner.waiting.length)} waiting for you <span aria-hidden="true">↗</span></a>` : 'Nothing needs your signature');
    fill("ov-finding", esc(model.owner.waiting.length ? `${model.owner.waiting.length} decision${model.owner.waiting.length === 1 ? '' : 's'} waiting for you. Follow the checks before signing.` : model.empty ? 'Run the first staged step to open the week.' : 'Follow the recorded checks, signatures and PayPal operations.'));
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
    // Financial values remain exact; motion never interpolates an amount.
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
    $("trail-body").onclick = event => {
      const button = event.target.closest?.("[data-proposal]");
      if (!button || !model) return;
      trailSelected = button.dataset.proposal;
      // Preserve the clicked control's focus while updating selection.
      for (const el of document.querySelectorAll("[data-proposal]")) {
        el.setAttribute("aria-pressed", String(el.dataset.proposal === trailSelected));
        el.closest(".trail-row").classList.toggle("selected", el.dataset.proposal === trailSelected);
      }
      fill("trail-detail", trailDetail(model, trailSelected));
    };
    $("trail-filters").onclick = event => {
      const button = event.target.closest?.("[data-trail-filter]");
      if (!button || !model) return;
      trailFilter = button.dataset.trailFilter;
      for (const el of $("trail-filters").querySelectorAll("button")) el.setAttribute("aria-pressed", String(el === button));
      fill("trail-body", trailRows(model, trailFilter, trailSelected));
    };
    const stageEl = $("stage");
    stageEl.onclick = event => activate(event.target);
    stageEl.onkeydown = event => {
      keyboard = true;
      try {if ((event.key === "Enter" || event.key === " ") && activate(event.target)) event.preventDefault();}
      finally {keyboard = false;}
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
      keyboard = true;
      try {if (item) {item = ""; stage(); panel();} else if (focus) closeFocus(); else if (selected) {selected = ""; stage(); panel();}}
      finally {keyboard = false;}
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
      if (!shown) $("stage").innerHTML = '<p class="scene-loading">Loading the money map…</p>';
      const notice = $("overview-notice");
      let next;
      try {next = await load(workspace, client);}
      catch {
        if (ticket !== serial) return;
        notice.hidden = false;
        notice.innerHTML = '<p>Could not load the recorded proposals. Previous values, if shown, have not refreshed.</p><button type="button" class="secondary" id="overview-retry">Try again</button>';
        $("overview-retry").onclick = () => this.show(workspace, client);
        if (!shown) $("trail-body").innerHTML = '<p class="trail-empty">The proposal trail is unavailable.</p>';
        return;
      }
      if (ticket !== serial) return;
      notice.hidden = true;
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
