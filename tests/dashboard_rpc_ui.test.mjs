import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const js = await readFile(new URL("../app/dashboard/static/dashboard.js", import.meta.url), "utf8");
const html = await readFile(new URL("../app/dashboard/static/index.html", import.meta.url), "utf8");

test("RPC diagnostics UI exposes a manual button and exactly one POST call site", () => {
  assert.match(html, /id="rpc-test-button"[^>]*>Test RPC</);
  assert.equal((js.match(/fetch\("\/api\/rpc-diagnostics\/test"/g) || []).length, 1);
  assert.match(js, /method: "POST", cache: "no-store"/);
  assert.match(js, /if \(rpcDiagnosticRunning\) return;/);
  assert.match(js, /rpc-test-button"\)\.addEventListener\("click", testBnbRpc\)/);
});

test("RPC diagnostics status loading is read-only and does not launch a scan", () => {
  assert.match(js, /fetchJson\("\/api\/rpc-diagnostics"\)/);
  assert.doesNotMatch(js, /fetch\("\/api\/scan/);
  assert.match(html, /one read-only eth_chainId request/i);
});
