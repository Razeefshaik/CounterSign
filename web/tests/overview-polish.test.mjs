import test from "node:test";
import assert from "node:assert/strict";
import {compareBars, journey} from "../plot.mjs";
import {weekDays} from "../words.mjs";

const usd = minor => ({minor, currency: "USD"});
const comparison = {naive: {payout: usd(150000), disputed_shares: usd(41400)}, countersign: {total: usd(108600)}};

test("Friday comparison distinguishes simulated sending from the recorded plan on one scale", () => {
  const html = compareBars({comparison});
  assert.match(html, /Naive agent <small>Simulated/);
  assert.match(html, /CounterSign <small>Recorded plan/);
  assert.match(html, /\$1,500<small>would send/);
  assert.match(html, /\$1,086<small>planned payout/);
  assert.match(html, /class="ours" style="width:72\.4%"/);
  assert.match(html, /class="naive-held" style="width:27\.6%"/);
  assert.match(html, /\$414 in disputed shares stays reserved/);
  assert.ok(!html.includes('class="kept"'));
});

test("missing or invalid comparison amounts stay unavailable instead of becoming zero", () => {
  assert.match(compareBars({comparison: null}), /Recorded on Friday/);
  for (const partial of [{}, {...comparison, countersign: {}}, {...comparison, naive: {...comparison.naive, disputed_shares: usd(150001)}}, {...comparison, countersign: {total: {minor: 100, currency: "EUR"}}}]) {
    const html = compareBars({comparison: partial});
    assert.match(html, /unavailable/);
    assert.ok(!html.includes("$0"));
  }
});

test("zero and unusually large recorded plans never create invalid bar widths", () => {
  for (const [naive, ours, held] of [[0, 0, 0], [100, 200, 10]]) {
    const html = compareBars({comparison: {naive: {payout: usd(naive), disputed_shares: usd(held)}, countersign: {total: usd(ours)}}});
    for (const match of html.matchAll(/width:([\d.]+)%/g)) assert.ok(Number(match[1]) >= 0 && Number(match[1]) <= 100);
    assert.ok(!/NaN|Infinity/.test(html));
  }
});

test("shiny week milestones remain display only and derive their state from actual steps", () => {
  const demo = {step: 1, total: 2, steps: [{label: "Monday: orders arrived", done: true}, {label: "Friday: plan payout", done: false}]};
  const html = journey(demo, weekDays);
  assert.match(html, /class="done"/);
  assert.match(html, /class="current"/);
  assert.match(html, /Staged week: step 1 of 2/);
  assert.ok(!/<button|<a |tabindex|onclick/.test(html));
  assert.equal(journey(null, weekDays), "");
});
