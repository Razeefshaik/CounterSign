import test from "node:test";
import assert from "node:assert/strict";
import {readFile, stat} from "node:fs/promises";
const source = async path => readFile(new URL("../" + path, import.meta.url), "utf8");

test("the chosen mark is the favicon, sidebar mark and hero title highlight", async () => {
  const html = await source("index.html"), mark = await source("assets/countersign-mark.svg");
  assert.match(html, /rel="icon"[^>]*href="assets\/countersign-mark.svg"/);
  const path = 'M4 12V4h11M28 20v8H17M4 21h9m3-5 4 4L28 8';
  assert.ok(mark.includes(path));
  assert.equal(html.split(path).length - 1, 2);
  assert.match(html, /id="command-title" data-brand="true">CounterSign/);
});

test("the hero contains identity copy and decorative material assets without duplicate payout amounts", async () => {
  const html = await source("index.html"), css = await source("hero.css");
  const hero = html.slice(html.indexOf('class="cc-hero'), html.indexOf('class="cc-next'));
  assert.ok(!hero.includes("$"));
  assert.match(hero, /Disputes and payouts, managed together/);
  for (const name of ["hero-blue", "hero-pink", "hero-loop-blue", "hero-loop-pearl"]) {
    assert.ok(css.includes("assets/" + name + ".webp"));
    const asset = await stat(new URL("../assets/" + name + ".webp", import.meta.url));
    assert.ok(asset.size > 1000 && asset.size < 250000);
  }
  assert.match(css, /padding:3px/);
  assert.match(css, /prefers-reduced-motion:reduce/);
  assert.match(css, /data-paused="true"/);
});

test("owner attention uses matte rose tokens without the former hot-pink halo", async () => {
  const css = await source("styles.css");
  assert.match(css, /--highlight:#c18da5/i);
  assert.match(css, /--highlight-ink:#80415f/);
  assert.ok(!css.includes("--highlight:#ec4899"));
  assert.ok(!css.includes("rgb(236 72 153 / .16)"));
  assert.ok(!css.includes("rgb(236 72 153 / .3)"));
});
