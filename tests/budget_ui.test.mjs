import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const html = readFileSync(new URL("../app/dashboard/static/index.html", import.meta.url), "utf8");
const js = readFileSync(new URL("../app/dashboard/static/dashboard.js", import.meta.url), "utf8");

test("dashboard renders a live read-only budget summary and unknown available amount", () => {
  assert.match(html, /id="budget-state"/);
  assert.match(html, /id="budget-unknown"/);
  assert.match(js, /fetchJson\("\/api\/budget"\)/);
  assert.match(js, /data\.available_usd == null/);
  assert.match(js, /Available amount is unknown/);
});
