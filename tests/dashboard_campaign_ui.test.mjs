import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const js = await readFile(new URL("../app/dashboard/static/dashboard.js", import.meta.url), "utf8");
const html = await readFile(new URL("../app/dashboard/static/index.html", import.meta.url), "utf8");
const css = await readFile(new URL("../app/dashboard/static/dashboard.css", import.meta.url), "utf8");

test("campaign control posts one in-process API job and polls the real job status", () => {
  assert.match(html, /id="run-campaign-scan"[^>]*>.*Find new campaigns/s);
  assert.match(js, /fetch\("\/api\/campaign-cycle", \{ method: "POST", cache: "no-store" \}\)/);
  assert.match(js, /\/api\/campaign-cycle\/jobs\//);
  assert.match(js, /\/api\/campaign-cycle\/status/);
  assert.match(js, /if \(campaignStartInFlight\) return;/);
  assert.match(js, /status === "running"/);
  assert.doesNotMatch(html, /id="cancel-campaign-scan"/);
});

test("campaign table renders unknown states and uses no demo campaign data", () => {
  assert.match(html, /id="campaign-results-body"/);
  assert.match(html, /id="campaign-chain-filter"/);
  assert.match(html, /id="campaign-status-filter"/);
  assert.match(js, /No scan running/);
  assert.match(js, /Unknown|UNKNOWN/);
  assert.match(css, /--bg: #FCE7F3/);
  assert.match(css, /--accent: #BE185D/);
  assert.match(html, /id="campaign-source-metrics"/);
  assert.match(js, /job\?\.source_metrics/);
  assert.match(js, /items_received/);
  assert.match(js, /new_leads_saved/);
});

test("OpenSea campaign eligibility action revalidates and pins the selected Ethereum wallet", () => {
  assert.match(js, /campaign-leads\/\$\{encodeURIComponent\(String\(item\.id\)\.replace\("source-lead-", ""\)\)\}\/opensea-wallet-check/);
  assert.match(js, /await wallets\.refreshMetaMask\(wallets\.metamaskProvider\)/);
  assert.match(js, /current\.address\.toLowerCase\(\) !== selectedAddress\.toLowerCase\(\)/);
  assert.match(js, /provider_id: providerId, wallet_address: selectedAddress, chain_id: "1"/);
  assert.match(js, /checked\.selected_wallet !== `\$\{selectedAddress\.slice\(0, 6\)\}…\$\{selectedAddress\.slice\(-4\)\}`/);
  assert.match(js, /item\.wallet_verifications/);
  assert.match(html, /Free-mint claims remain unverified until contract and wallet checks pass/);
});

test("OpenSea wallet selection survives campaign reload only for the same provider address", () => {
  assert.match(js, /const campaignWalletSelections = new Map\(\)/);
  assert.match(js, /campaignWalletSelections\.set\(campaignKey, \{\s*providerId: selection\.value,\s*address: option\.dataset\.address,\s*\}\)/);
  assert.match(js, /candidate\.address\.toLowerCase\(\) === remembered\.address\.toLowerCase\(\)/);
  assert.match(js, /if \(sameWallet\) selection\.value = remembered\.providerId;\s*else campaignWalletSelections\.delete\(campaignKey\)/);
});
