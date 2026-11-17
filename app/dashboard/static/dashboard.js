"use strict";

import { SUPPORTED_WALLET_NETWORKS, WalletRegistry, shortWalletAddress } from "./wallet_registry.js";
import { formatDashboardTime } from "./dashboard_format.js";

const hasMetaMask = () => globalThis.ethereum?.isMetaMask === true
  || [...(wallets.eip6963Providers?.values?.() || [])].some(({ info }) => info?.rdns?.toLowerCase() === "io.metamask");
const state = {
  chain: "", status: "", priceStatus: "", upcomingOnly: false, search: "", discoveries: [], activeDiscovery: null,
  campaignChain: "", campaignStatus: "",
  binanceStatus: "checking", walletStatuses: {
    metamask: "unavailable", phantom: globalThis.phantom?.solana ? "disconnected" : "unavailable", binance_agentic: "checking",
  }, walletErrors: {},
};
const wallets = new WalletRegistry();
const campaignWalletSelections = new Map();
const RESULT_BATCH_SIZE = 6;
let campaignVisibleCount = RESULT_BATCH_SIZE;
let campaignResultTotal = 0;
let discoveryVisibleCount = RESULT_BATCH_SIZE;
const $ = (id) => document.getElementById(id);
let currentCorePreview = null;
let latestCoreTransaction = null;
state.walletStatuses.metamask = hasMetaMask() ? "disconnected" : "unavailable";

async function fetchJson(path) {
  let response;
  try {
    response = await fetch(path, { cache: "no-store" });
  } catch (_) {
    throw new Error(`Network request failed for ${path}`);
  }
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`);
  try {
    return await response.json();
  } catch (_) {
    throw new Error(`${path} returned invalid JSON`);
  }
}

function priceLabel(item) {
  const status = String(item.mint_price_status || "UNKNOWN").toUpperCase();
  const price = item.mint_price_claim ?? item.mint_price;
  const token = item.native_gas_token || "";
  const amount = price == null || price === "" ? "Unknown" : `${price}${token ? ` ${token}` : ""}`;
  if (["VERIFIED_ZERO", "PRICE_VERIFIED_FREE"].includes(status)) return `${amount} · verified free`;
  if (["PRICE_VERIFIED_PAID", "VERIFIED_PAID", "PAID"].includes(status)) return `${amount} · verified paid`;
  return `${amount} · ${status === "UNKNOWN" || status === "PRICE_UNKNOWN" ? "unknown" : "unverified"}`;
}

function infrastructureLabel(item) {
  if (item.mint_infrastructure_verified) return "Verified mint infrastructure";
  const status = String(item.mint_infrastructure_status || "UNKNOWN").toUpperCase();
  const labels = {
    REJECTED_HISTORICAL_NFT_MINT: "Historical NFT mint · rejected",
    COLLECTION_ONLY_NOT_MINT_INFRASTRUCTURE: "Collection only · not mint infrastructure",
    PENDING_ONCHAIN_CLASSIFICATION: "Pending on-chain classification",
  };
  return labels[status] || status.replaceAll("_", " ");
}

function createExternalLink(url, label = null) {
  if (typeof url !== "string") return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return null;
    const link = document.createElement("a");
    link.href = parsed.href;
    link.textContent = label || parsed.hostname;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    return link;
  } catch (_) { return null; }
}

function costLabel(cost, fallbackToken = "") {
  if (!cost || typeof cost !== "object") return "Unknown";
  const amount = cost.amount ?? cost.amount_sol ?? cost.amount_native;
  const token = cost.token || cost.native_token || fallbackToken;
  if (amount == null) return String(cost.status || "Unknown").replaceAll("_", " ");
  return `${amount}${token ? ` ${token}` : ""}${cost.status ? ` · ${String(cost.status).replaceAll("_", " ")}` : ""}`;
}

function addCell(row, value, className = "") {
  const cell = document.createElement("td");
  if (className) cell.className = className;
  cell.textContent = value == null || value === "" ? "Unknown" : String(value);
  row.appendChild(cell);
  return cell;
}

function campaignStatusLabel(value, type) {
  const raw = String(value || "UNKNOWN").toUpperCase();
  const labels = {
    BLOCKED_UNSUPPORTED: "Not supported yet",
    DEFERRED_MISSING_EVIDENCE: "Needs more evidence",
    VERIFIED_PREVIEWABLE: "Ready for careful review",
    CLAIMED_FREE_UNVERIFIED: "Free claim · unverified",
    VERIFIED_FREE: "Verified free",
    VERIFIED_PAID: "Verified paid",
    HIGH: "High risk",
    MEDIUM: "Medium risk",
    LOW: "Low risk",
  };
  if (labels[raw]) return labels[raw];
  if (type === "risk-status") return raw.charAt(0) + raw.slice(1).toLowerCase() + " risk";
  return raw.replaceAll("_", " ").toLowerCase();
}

function friendlyWalletCheck(check) {
  const evidence = check?.evidence && typeof check.evidence === "object" ? check.evidence : {};
  const failureReason = evidence.failure_reason || check?.failure_reason;
  const httpStatus = evidence.http_status ?? check?.http_status;
  if (check?.status === "BLOCKED_WALLET_IDENTITY_MISMATCH") {
    return "Wallet doesn’t match the saved OpenSea address. Select the matching account.";
  }
  if (check?.status === "BLOCKED_WALLET_AUTH_NOT_CONFIGURED") {
    return "OpenSea wallet access isn’t set up yet.";
  }
  if (failureReason === "HTTP_ERROR" && Number(httpStatus) === 403) {
    return "OpenSea returned 403. Eligibility is still unknown; check API access.";
  }
  if (failureReason === "TRANSPORT_ERROR") {
    return "Couldn’t reach OpenSea from this session. Eligibility is still unknown.";
  }
  if (check?.status === "INELIGIBLE") return "OpenSea reports this wallet isn’t eligible for the saved stage.";
  if (check?.status === "UPCOMING") return "This wallet may be eligible; the stage isn’t open yet.";
  if (check?.status === "ELIGIBLE_ACTIVE_UNVERIFIED_PRICE") {
    return "Eligibility found. Price and mint safety still need independent checks.";
  }
  if (check?.status === "VERIFIED_PREVIEWABLE") return "Checks passed for review. No mint has been sent.";
  return "Eligibility is still unknown. More evidence is needed.";
}

function shortCampaignTimestamp(value) {
  if (!value) return "Not checked";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not checked";
  return new Intl.DateTimeFormat(undefined, {
    month: "short", day: "numeric", hour: "numeric", minute: "2-digit",
  }).format(date);
}

function updateCampaignResultWindow() {
  const rows = [...$("campaign-results-body").children];
  const visible = Math.min(campaignVisibleCount, rows.length);
  rows.forEach((row, index) => { row.hidden = index >= campaignVisibleCount; });
  $("campaign-results-count").textContent = `Showing ${visible} of ${campaignResultTotal || rows.length} campaigns`;
  const more = $("campaign-results-more");
  const remaining = rows.length - visible;
  more.hidden = rows.length <= RESULT_BATCH_SIZE;
  more.textContent = remaining === 0 && campaignVisibleCount > RESULT_BATCH_SIZE
    ? "Show fewer" : `Show ${Math.min(RESULT_BATCH_SIZE, remaining)} more campaigns`;
}

function updateDiscoveryResultWindow(matchingCount, totalCount) {
  const rows = [...$("results-body").children];
  const visible = Math.min(discoveryVisibleCount, rows.length);
  rows.forEach((row, index) => { row.hidden = index >= discoveryVisibleCount; });
  $("result-count").textContent = rows.length
    ? `Showing ${visible} of ${matchingCount} matches · ${totalCount} saved` : "";
  const more = $("discoveries-more");
  const remaining = rows.length - visible;
  more.hidden = rows.length <= RESULT_BATCH_SIZE;
  more.textContent = remaining === 0 && discoveryVisibleCount > RESULT_BATCH_SIZE
    ? "Show fewer" : `Show ${Math.min(RESULT_BATCH_SIZE, remaining)} more discoveries`;
}

function renderDiscoveries(items, total) {
  const body = $("results-body");
  body.replaceChildren();
  const filteredItems = state.search
    ? items.filter((item) => `${item.project_name || ""} ${item.address || ""} ${item.network || ""}`.toLowerCase().includes(state.search))
    : items;
  $("empty-state").hidden = filteredItems.length !== 0;
  if (filteredItems.length === 0) {
    const filtered = Boolean(state.chain || state.status || state.priceStatus || state.upcomingOnly);
    const empty = $("empty-state");
    empty.querySelector("strong").textContent = filtered || state.search ? "No opportunities match these filters" : "No discoveries saved yet";
    empty.querySelector("span:not(.state-symbol)").textContent = filtered || state.search
      ? "Try a different search or clear a filter to see other saved observations."
      : "No records are stored in SQLite yet. This dashboard never creates demo opportunities.";
  }
  for (const item of filteredItems) {
    const row = document.createElement("tr");
    const projectCell = document.createElement("td");
    const project = document.createElement("button");
    project.type = "button";
    project.className = "project-button";
    project.textContent = item.project_name || "Unnamed project";
    project.setAttribute("aria-label", `View details for ${project.textContent}`);
    project.addEventListener("click", () => showDetails(item.id));
    projectCell.appendChild(project);
    row.appendChild(projectCell);

    const networkCell = document.createElement("td");
    const network = document.createElement("span");
    network.className = "network-pill";
    network.textContent = item.network;
    networkCell.appendChild(network);
    row.appendChild(networkCell);

    projectCell.appendChild(Object.assign(document.createElement("small"), { className: "project-address", textContent: item.address || "Address unavailable" }));
    projectCell.appendChild(Object.assign(document.createElement("small"), {
      className: "project-status",
      textContent: String(item.opportunity_status || "STATUS UNKNOWN").replaceAll("_", " "),
    }));
    const dateCell = addCell(row, formatDashboardTime(item.mint_date));
    if (item.mint_date) dateCell.title = `${item.mint_date} (stored schedule; displayed in your local timezone)`;
    const priceCell = document.createElement("td");
    const price = document.createElement("span");
    const priceStatus = String(item.mint_price_status || "UNKNOWN").toUpperCase();
    price.className = `price-pill ${["VERIFIED_ZERO", "PRICE_VERIFIED_FREE"].includes(priceStatus) ? "free" : (["PRICE_VERIFIED_PAID", "VERIFIED_PAID", "PAID"].includes(priceStatus) ? "paid" : "unknown")}`;
    price.textContent = priceLabel(item);
    priceCell.appendChild(price);
    row.appendChild(priceCell);
    const evidenceCell = document.createElement("td");
    const evidenceBadge = document.createElement("span");
    const verifiedFreePrice = ["VERIFIED_ZERO", "PRICE_VERIFIED_FREE"].includes(priceStatus);
    const verifiedPaidPrice = ["PRICE_VERIFIED_PAID", "VERIFIED_PAID", "PAID"].includes(priceStatus);
    evidenceBadge.className = `status-pill ${verifiedFreePrice ? "good" : (verifiedPaidPrice ? "paid" : "warning")}`;
    evidenceBadge.textContent = priceStatus.replaceAll("_", " ");
    evidenceCell.appendChild(evidenceBadge);
    row.appendChild(evidenceCell);
    const infrastructure = addCell(row, infrastructureLabel(item));
    if (!item.mint_infrastructure_verified) infrastructure.classList.add("uncertain-cell");
    const eligibilityCell = document.createElement("td");
    const eligibility = document.createElement("span");
    const eligibilityValue = String(item.eligibility || "UNKNOWN").toLowerCase();
    eligibility.className = `status-pill${eligibilityValue === "eligible" ? " good" : (eligibilityValue === "ineligible" ? " bad" : " warning")}`;
    eligibility.textContent = item.eligibility || "Unknown";
    eligibilityCell.appendChild(eligibility);
    row.appendChild(eligibilityCell);
    const sourceCell = document.createElement("td");
    sourceCell.className = "source-cell";
    const source = createExternalLink(item.source_url);
    if (source) source.textContent = new URL(source.href).hostname;
    if (source) source.className = "source-link";
    sourceCell.appendChild(source || document.createTextNode("Source unavailable"));
    const checked = document.createElement("small");
    checked.textContent = `Checked ${formatDashboardTime(item.source_last_checked_at || item.discovery_time)}`;
    sourceCell.appendChild(checked);
    row.appendChild(sourceCell);
    row.addEventListener("click", (event) => { if (!event.target.closest("a,button")) void showDetails(item.id); });
    body.appendChild(row);
  }
  updateDiscoveryResultWindow(filteredItems.length, total);
}

async function loadSummary() {
  $("summary-error").hidden = true;
  $("upcoming-note").textContent = "Future active or announced";
  let data;
  try {
    data = await fetchJson("/api/dashboard");
  } catch (error) {
    for (const id of ["total-count", "new-count", "verified-count", "free-count", "unverified-count"]) $(id).textContent = "Unavailable";
    $("upcoming-count").textContent = "Unavailable";
    $("scanner-status").textContent = "Status unavailable";
    $("scanner-indicator").className = "indicator error";
    $("last-updated").textContent = "Unavailable";
    $("last-scan-activity").textContent = "Unavailable";
    $("next-scan").textContent = "Unavailable";
    $("scan-result").textContent = "Could not read scanner status";
    $("summary-error-text").textContent = `Overview request failed: ${error.message}.`;
    $("summary-error").hidden = false;
    return;
  }
  try {
    $("scanner-status").textContent = String(data.status || "unknown").replaceAll("_", " ");
    $("scanner-indicator").className = `indicator ${String(data.status || "unknown")}`;
    $("last-updated").textContent = formatDashboardTime(new Date().toISOString());
    $("last-scan-activity").textContent = formatDashboardTime(data.last_scan_time);
    $("next-scan").textContent = formatDashboardTime(data.next_scheduled_scan);
    $("scan-result").textContent = data.last_scan_status ? `Last result: ${data.last_scan_status}` : "No scan history";
    $("total-count").textContent = data.total_discovered;
    $("new-count").textContent = data.new_discoveries_24h;
    $("verified-count").textContent = data.verified_opportunities;
    $("free-count").textContent = data.free_mint_opportunities;
    $("unverified-count").textContent = data.unverified_opportunities;
  } catch (error) {
    $("summary-error-text").textContent = `Overview data could not be displayed: ${error.message}.`;
    $("summary-error").hidden = false;
    return;
  }
  try {
    const upcomingData = await fetchJson("/api/discoveries?status=UPCOMING&limit=1");
    $("upcoming-count").textContent = upcomingData.total;
  } catch (error) {
    $("upcoming-count").textContent = "Unavailable";
    $("upcoming-note").textContent = `Count unavailable: ${error.message}`;
  }
}

let campaignPollTimer = null;
let campaignPollInFlight = false;
let campaignStartInFlight = false;
let lastCampaignTerminalJob = null;

function renderCampaignJob(job) {
  const status = String(job?.status || "idle").toLowerCase();
  const label = status === "idle" ? "Idle" : status[0].toUpperCase() + status.slice(1);
  $("campaign-job-status").textContent = label;
  $("campaign-job-indicator").className = `indicator ${status}`;
  $("campaign-job-id").textContent = job?.job_id ? `Job ${job.job_id}` : "No campaign cycle has run yet.";
  $("campaign-job-started").textContent = job?.started_at ? formatDashboardTime(job.started_at) : "Not available";
  const elapsed = job?.duration_seconds != null
    ? Number(job.duration_seconds)
    : (status === "running" && job?.started_at ? Math.max(0, (Date.now() - Date.parse(job.started_at)) / 1000) : null);
  $("campaign-job-duration").textContent = elapsed == null || !Number.isFinite(elapsed)
    ? "Not available" : `${elapsed.toFixed(1)} sec${status === "running" ? " · running" : ""}`;
  const counts = job?.counts || {};
  $("campaign-leads-found").textContent = String(counts.leads_discovered ?? 0);
  $("campaign-new-leads").textContent = String(counts.new_leads_saved ?? 0);
  $("campaign-verified").textContent = String(counts.verified_candidates ?? 0);
  const rejected = Number(counts.rejected_paid || 0) + Number(counts.rejected_sold_out || 0);
  $("campaign-deferred").textContent = `${counts.deferred ?? 0} / ${rejected}`;
  const message = $("campaign-job-message");
  const errors = [...(job?.source_errors || [])];
  if (job?.failure_reason) errors.push({ source: "campaign-cycle", message: job.failure_reason });
  const newDeferred = (job?.leads || []).filter((item) => item.new && item.status === "DEFERRED_MISSING_EVIDENCE").length;
  if (status === "idle") message.textContent = "No scan running. Starting a scan will not invoke the historical event scanner or scheduler.";
  else if (status === "running") message.textContent = "Campaign source request is running. Counts update when the bounded cycle returns.";
  else if (status === "completed") message.textContent = newDeferred
    ? `Cycle completed. ${newDeferred} new lead${newDeferred === 1 ? " remains" : "s remain"} deferred for source or verifier evidence.`
    : "Campaign cycle completed. Results below reflect saved SQLite records.";
  else message.textContent = job?.failure_reason || `Campaign cycle ${status}.`;
  const metricsBox = $("campaign-source-metrics");
  metricsBox.replaceChildren();
  const sourceMetrics = Array.isArray(job?.source_metrics) ? job.source_metrics : [];
  if (sourceMetrics.length) {
    metricsBox.hidden = false;
    const heading = document.createElement("strong");
    heading.textContent = "Source results";
    metricsBox.appendChild(heading);
    for (const metric of sourceMetrics) {
      const line = document.createElement("div");
      line.className = "campaign-source-metric";
      const source = document.createElement("strong");
      source.textContent = metric.source || "Unknown source";
      const statusText = document.createElement("span");
      statusText.textContent = String(metric.status || "unknown").toUpperCase();
      const countsText = document.createElement("span");
      const received = metric.items_received ?? "Not reported";
      const found = metric.leads_found ?? 0;
      const saved = metric.new_leads_saved ?? 0;
      const requests = metric.requests ?? 0;
      countsText.textContent = `${received} items · ${found} leads · ${saved} new · ${requests} request${requests === 1 ? "" : "s"}`;
      line.append(source, statusText, countsText);
      if (metric.expired) {
        const expired = document.createElement("span");
        expired.textContent = `${metric.expired} expired`;
        line.appendChild(expired);
      }
      if (metric.error_type) {
        const error = document.createElement("span");
        error.className = "source-error";
        const cause = metric.error_reason ? ` · ${metric.error_reason}` : "";
        error.textContent = `Error: ${metric.error_type}${metric.http_status ? ` (HTTP ${metric.http_status})` : ""}${cause}`;
        line.appendChild(error);
      }
      metricsBox.appendChild(line);
    }
  } else metricsBox.hidden = true;
  const errorBox = $("campaign-source-errors");
  errorBox.replaceChildren();
  if (errors.length) {
    errorBox.hidden = false;
    const heading = document.createElement("strong");
    heading.textContent = "Source / cycle errors";
    errorBox.appendChild(heading);
    for (const error of errors) {
      const line = document.createElement("p");
      const source = error.source || "source";
      const reason = error.message || error.error_type || "Request failed";
      line.textContent = `${source}: ${reason}`;
      errorBox.appendChild(line);
    }
  } else errorBox.hidden = true;
  $("run-campaign-scan").disabled = status === "running" || campaignStartInFlight;
}

async function loadCampaignResults() {
  const params = new URLSearchParams();
  if (state.campaignChain) params.set("chain", state.campaignChain);
  if (state.campaignStatus) params.set("status", state.campaignStatus);
  $("campaign-results-loading").hidden = false;
  $("campaign-results-error").hidden = true;
  $("campaign-results-empty").hidden = true;
  const body = $("campaign-results-body");
  body.replaceChildren();
  $("campaign-results-more").hidden = true;
  try {
    const suffix = params.size ? `?${params}` : "";
    const data = await fetchJson(`/api/campaigns${suffix}`);
    campaignResultTotal = data.count;
    for (const item of data.items || []) {
      const row = document.createElement("tr");
      const project = document.createElement("td");
      const name = document.createElement("strong");
      name.className = "campaign-project-name";
      name.textContent = item.project_name || "Unnamed campaign";
      const sourceStatus = document.createElement("small");
      sourceStatus.textContent = campaignStatusLabel(item.source_verification || "UNKNOWN", "source-status");
      project.append(name, sourceStatus); row.appendChild(project);
      addCell(row, item.network || "Unknown");
      const contractCell = document.createElement("td");
      contractCell.className = "campaign-source-info";
      const address = document.createElement("span");
      address.className = "campaign-address";
      const fullAddress = String(item.contract_address || "");
      address.textContent = fullAddress.length > 14
        ? `${fullAddress.slice(0, 8)}…${fullAddress.slice(-6)}` : fullAddress || "Address unavailable";
      address.title = fullAddress;
      contractCell.appendChild(address);
      const source = createExternalLink(item.source_url, "Open source");
      if (source) contractCell.appendChild(source);
      else contractCell.appendChild(Object.assign(document.createElement("small"), { textContent: "Source unavailable" }));
      row.appendChild(contractCell);
      const statusCell = document.createElement("td");
      const statusStack = document.createElement("div");
      statusStack.className = "campaign-status-stack";
      appendCampaignBadge(statusStack, item.mint_status || "UNKNOWN", "mint-status", "Mint");
      appendCampaignBadge(statusStack, item.price_status || "UNKNOWN", "price-status", "Price");
      appendCampaignBadge(statusStack, item.risk_status || "UNKNOWN", "risk-status", "Risk");
      statusCell.appendChild(statusStack);
      row.appendChild(statusCell);
      const walletCell = document.createElement("td");
      if (String(item.id).startsWith("source-lead-") && String(item.chain_id) === "1"
          && String(item.source_url || "").includes("opensea.io/collection/")) {
        const controls = document.createElement("div");
        controls.className = "campaign-wallet-check";
        const selection = document.createElement("select");
        selection.setAttribute("aria-label", `Wallet for ${item.project_name}`);
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = "Select connected wallet";
        selection.appendChild(placeholder);
        const available = wallets.providersForChain("1");
        for (const candidate of available) {
          const option = document.createElement("option");
          option.value = candidate.providerId;
          option.textContent = `${candidate.name} · ${shortWalletAddress(candidate.address)}`;
          option.dataset.address = candidate.address;
          selection.appendChild(option);
        }
        const campaignKey = String(item.id);
        const remembered = campaignWalletSelections.get(campaignKey);
        if (remembered) {
          const sameWallet = available.find((candidate) => candidate.providerId === remembered.providerId
            && candidate.address.toLowerCase() === remembered.address.toLowerCase());
          if (sameWallet) selection.value = remembered.providerId;
          else campaignWalletSelections.delete(campaignKey);
        }
        selection.addEventListener("change", () => {
          const option = selection.selectedOptions[0];
          if (!option?.value || !option.dataset.address) {
            campaignWalletSelections.delete(campaignKey);
            return;
          }
          campaignWalletSelections.set(campaignKey, {
            providerId: selection.value,
            address: option.dataset.address,
          });
        });
        const check = document.createElement("button");
        check.type = "button";
        check.className = "text-action";
        check.textContent = "Check eligibility";
        check.disabled = available.length === 0 || !item.contract_address;
        const result = document.createElement("small");
        result.className = "wallet-check-result";
        const previous = Array.isArray(item.wallet_verifications) ? item.wallet_verifications[0] : null;
        result.textContent = previous
          ? `Last check · ${friendlyWalletCheck(previous)}`
          : "Ready when you are · no check yet";
        result.title = previous?.checked_at ? `Checked ${formatDashboardTime(previous.checked_at)}` : "";
        check.addEventListener("click", async () => {
          const providerId = selection.value;
          let selectedAddress = selection.selectedOptions[0]?.dataset.address || "";
          if (!providerId || !selectedAddress) { result.textContent = "Select a connected Ethereum wallet."; return; }
          check.disabled = true;
          result.textContent = "Checking the stage and this wallet…";
          try {
            if (providerId === "metamask") {
              await wallets.refreshMetaMask(wallets.metamaskProvider);
              const current = wallets.providersForChain("1").find((candidate) => candidate.providerId === "metamask");
              if (!current || current.address.toLowerCase() !== selectedAddress.toLowerCase()) {
                throw new Error("MetaMask account or network changed. Refresh and select the current wallet again.");
              }
              selectedAddress = current.address;
            }
            const response = await fetch(`/api/campaign-leads/${encodeURIComponent(String(item.id).replace("source-lead-", ""))}/opensea-wallet-check`, {
              method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
              body: JSON.stringify({ provider_id: providerId, wallet_address: selectedAddress, chain_id: "1" }),
            });
            const checked = await response.json();
            if (!response.ok) throw new Error(checked.detail || "Wallet check failed");
            if (checked.provider_id !== providerId || checked.chain_id !== "1"
                || checked.selected_wallet !== `${selectedAddress.slice(0, 6)}…${selectedAddress.slice(-4)}`) {
              throw new Error("Wallet check identity did not match the selected provider.");
            }
            result.textContent = friendlyWalletCheck(checked);
            await loadCampaignResults();
          } catch (error) {
            result.textContent = error.message || "Wallet check unavailable.";
          } finally { check.disabled = false; }
        });
        controls.append(selection, check, result);
        walletCell.appendChild(controls);
      } else walletCell.textContent = item.compatible_wallet || "Not checked";
      row.appendChild(walletCell);
      const updated = addCell(row, shortCampaignTimestamp(item.last_checked));
      if (item.last_checked) updated.title = formatDashboardTime(item.last_checked);
      body.appendChild(row);
    }
    updateCampaignResultWindow();
    $("campaign-results-empty").hidden = Boolean(data.items?.length);
  } catch (error) {
    $("campaign-results-error-detail").textContent = `Request failed: ${error.message}.`;
    $("campaign-results-error").hidden = false;
    $("campaign-results-count").textContent = "Campaign records unavailable";
    $("campaign-results-more").hidden = true;
  } finally { $("campaign-results-loading").hidden = true; }
}

function appendCampaignBadge(container, value, type, label) {
  const item = document.createElement("div");
  item.className = "campaign-status-item";
  const caption = document.createElement("small");
  caption.textContent = label;
  const badge = document.createElement("span");
  const raw = String(value || "UNKNOWN").toUpperCase();
  badge.className = `campaign-badge ${type} ${raw.includes("VERIFIED_PREVIEWABLE") || raw === "VERIFIED_FREE" ? "positive" : raw.includes("BLOCKED") || raw.includes("REJECTED") || raw === "HIGH" ? "negative" : "uncertain"}`;
  badge.textContent = campaignStatusLabel(raw, type);
  badge.title = raw;
  item.append(caption, badge);
  container.appendChild(item);
}

async function loadCampaignJob(jobId = null) {
  if (campaignPollInFlight) return;
  campaignPollInFlight = true;
  if (campaignPollTimer) window.clearTimeout(campaignPollTimer);
  try {
    const path = jobId ? `/api/campaign-cycle/jobs/${encodeURIComponent(jobId)}` : "/api/campaign-cycle/status";
    const job = await fetchJson(path);
    renderCampaignJob(job);
    if (job.status === "running" && job.job_id) {
      campaignPollTimer = window.setTimeout(() => void loadCampaignJob(job.job_id), 1500);
    } else if (job.job_id && lastCampaignTerminalJob !== job.job_id) {
      lastCampaignTerminalJob = job.job_id;
      await Promise.allSettled([loadSummary(), loadCampaignResults()]);
    }
  } catch (error) {
    $("campaign-job-message").textContent = `Campaign status is unavailable: ${error.message}.`;
  } finally { campaignPollInFlight = false; }
}

async function startCampaignCycle() {
  if (campaignStartInFlight) return;
  campaignStartInFlight = true;
  const button = $("run-campaign-scan");
  button.disabled = true;
  button.innerHTML = '<span aria-hidden="true">◌</span>Starting…';
  try {
    const response = await fetch("/api/campaign-cycle", { method: "POST", cache: "no-store" });
    let job;
    try { job = await response.json(); } catch (_) { throw new Error(`HTTP ${response.status} returned invalid JSON`); }
    if (!response.ok) {
      if (response.status === 409) {
        $("campaign-job-message").textContent = "A campaign cycle is already active; showing its current status.";
        await loadCampaignJob();
        return;
      }
      throw new Error(job.detail || `HTTP ${response.status}`);
    }
    renderCampaignJob(job);
    if (job.status === "running") void loadCampaignJob(job.job_id);
    else await loadCampaignJob(job.job_id);
  } catch (error) {
    $("campaign-job-message").textContent = `Could not start campaign cycle: ${error.message}.`;
    $("run-campaign-scan").disabled = false;
  } finally {
    campaignStartInFlight = false;
    button.innerHTML = '<span aria-hidden="true">↻</span>Run Discovery Scan';
    void loadCampaignJob();
  }
}

async function loadDiscoveries() {
  const params = new URLSearchParams({ limit: "200" });
  if (state.chain) params.set("chain", state.chain);
  if (state.status) params.set("status", state.status);
  if (state.priceStatus) params.set("price_status", state.priceStatus);
  if (state.upcomingOnly) params.set("upcoming", "true");
  $("results-loading").hidden = false;
  $("load-error").hidden = true;
  $("empty-state").hidden = true;
  $("results-body").replaceChildren();
  $("filter-summary").textContent = "Updating from SQLite…";
  try {
    const data = await fetchJson(`/api/discoveries?${params}`);
    state.discoveries = data.items;
    state.lastDiscoveryTotal = data.total;
    renderDiscoveries(data.items, data.total);
    $("filter-summary").textContent = data.is_demo_data ? "Demo data" : "Saved SQLite observations · no demo records";
  } catch (error) {
    state.discoveries = [];
    $("results-body").replaceChildren();
    $("empty-state").hidden = true;
    $("load-error-detail").textContent = `Request failed: ${error.message}.`;
    $("load-error").hidden = false;
    $("result-count").textContent = "";
    $("filter-summary").textContent = "Could not load current results";
  } finally { $("results-loading").hidden = true; }
}

async function refresh() {
  const button = $("refresh-button");
  if (button) button.disabled = true;
  await Promise.allSettled([loadSummary(), loadBudget(), loadDiscoveries(), loadSolanaWatchlist(), loadSourceHealth(), loadRpcDiagnostics(), loadCampaignResults(), loadCampaignJob()]);
  if (button) button.disabled = false;
}

async function loadBudget() {
  try {
    const data = await fetchJson("/api/budget");
    $("budget-state").textContent = String(data.state || "UNKNOWN").replaceAll("_", " ");
    $("budget-cap").textContent = `$${data.cap_usd} USD`;
    $("budget-committed").textContent = `$${data.confirmed_spent_usd} / $${data.reserved_usd} USD`;
    $("budget-unknown").textContent = String(data.unknown_liability_count);
    $("budget-note").textContent = data.available_usd == null
      ? `${data.note} Available amount is unknown.`
      : `${data.note} Accounting available: $${data.available_usd} USD.`;
  } catch (error) {
    for (const id of ["budget-state", "budget-cap", "budget-committed", "budget-unknown"]) $(id).textContent = "Unavailable";
    $("budget-note").textContent = `Budget status unavailable: ${error.message}`;
  }
}

function renderRpcDiagnostic(data) {
  $("rpc-endpoint").textContent = data.endpoint || "Configured endpoint (details hidden)";
  const state = String(data.state || "NOT_TESTED").toUpperCase();
  $("rpc-state").textContent = state.replaceAll("_", " ");
  $("rpc-state").dataset.state = state;
  $("rpc-checked").textContent = data.last_checked_at ? formatDashboardTime(data.last_checked_at) : "Not tested yet";
  const details = data.error_details || (state === "CONNECTED"
    ? `Read-only eth_chainId succeeded (chain ID ${data.chain_id}). No scan was started.`
    : "Use Test RPC to send one read-only eth_chainId request. This does not start a scan.");
  $("rpc-error-details").textContent = details;
  $("rpc-error-details").dataset.error = state !== "CONNECTED" && state !== "NOT_TESTED" && state !== "NOT_CONFIGURED";
}

async function loadRpcDiagnostics() {
  try {
    renderRpcDiagnostic(await fetchJson("/api/rpc-diagnostics"));
  } catch (error) {
    $("rpc-state").textContent = "Status unavailable";
    $("rpc-state").dataset.state = "NETWORK_BLOCKED";
    $("rpc-error-details").textContent = `Could not load RPC diagnostic status: ${error.message}`;
    $("rpc-error-details").dataset.error = "true";
  }
}

let rpcDiagnosticRunning = false;
async function testBnbRpc() {
  if (rpcDiagnosticRunning) return;
  rpcDiagnosticRunning = true;
  const button = $("rpc-test-button");
  button.disabled = true;
  button.textContent = "Testing…";
  $("rpc-state").textContent = "Request in progress";
  $("rpc-state").dataset.state = "NOT_TESTED";
  $("rpc-error-details").textContent = "Sending exactly one read-only eth_chainId request. No scan will start.";
  $("rpc-error-details").dataset.error = "false";
  try {
    const response = await fetch("/api/rpc-diagnostics/test", { method: "POST", cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
    renderRpcDiagnostic(data);
  } catch (error) {
    $("rpc-error-details").textContent = `Diagnostic request failed: ${error.message}`;
    $("rpc-error-details").dataset.error = "true";
  } finally {
    rpcDiagnosticRunning = false;
    button.disabled = false;
    button.textContent = "Test RPC";
  }
}

async function loadSolanaWatchlist() {
  $("watchlist-loading").hidden = false;
  $("watchlist-error").hidden = true;
  const container = $("watchlist-items");
  try {
  const { items } = await fetchJson("/api/solana/watchlist");
  container.replaceChildren();
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state";
    empty.textContent = "No Solana watchlist candidates yet.";
    container.appendChild(empty);
    return;
  }
  for (const item of items) {
      const card = document.createElement("article");
    card.className = "watchlist-item";
    const heading = document.createElement("div");
    heading.className = "watchlist-item-heading";
    const title = document.createElement("h3");
    title.textContent = item.project_name;
    const badge = document.createElement("span");
    badge.className = "status-pill warning";
    badge.textContent = item.verification_status || "UNKNOWN";
    heading.append(title, badge);
    const evidence = document.createElement("p");
    evidence.textContent = `${item.address_type.replaceAll("_", " ")} · ${item.address} · source: ${item.source_url} · review: ${item.review_status}`;
    const reason = document.createElement("p");
    reason.className = "wallet-message";
    reason.textContent = `${item.blocked_reason || "Waiting for on-chain inspection and manual source review."} · last checked: ${formatDashboardTime(item.last_checked_at)}`;
    const urls = document.createElement("p");
    urls.className = "watchlist-links";
    for (const [label, url] of [["Website", item.official_website], ["Announcement", item.announcement_url], ["Launchpad", item.launchpad_url]]) {
      const link = createExternalLink(url, label);
      if (link) urls.appendChild(link);
    }
    if (!urls.childElementCount) urls.textContent = "No project links supplied";
    const schedule = document.createElement("p");
    schedule.className = "wallet-message";
    schedule.textContent = `Announcement date claim: ${formatDashboardTime(item.announcement_date)} · mint date claim: ${formatDashboardTime(item.mint_date)} · price claim: ${item.mint_price_claim || "UNKNOWN"} (unverified) · allowlist claim: ${item.allowlist_status || "UNKNOWN"} (unverified)`;
    const controls = document.createElement("div");
    controls.className = "watchlist-controls";
    const verifiedLabel = document.createElement("label");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = Boolean(item.official_source_verified);
    verifiedLabel.append(checkbox, document.createTextNode(" I reviewed the official source"));
    const review = document.createElement("button");
    review.type = "button";
    review.className = "wallet-button";
    review.textContent = "Save source review";
    review.addEventListener("click", async () => {
      try {
        const response = await fetch(`/api/solana/watchlist/${encodeURIComponent(item.address)}/review`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ review_status: checkbox.checked ? "approved" : "pending", official_source_verified: checkbox.checked })
        });
        if (!response.ok) throw new Error("Review update failed");
        $("watchlist-message").textContent = "Source review saved. Eligibility remains wallet-specific.";
        await loadSolanaWatchlist();
      } catch (error) { $("watchlist-message").textContent = error.message; }
    });
    const verify = document.createElement("button");
    verify.type = "button";
    verify.className = "wallet-button";
    verify.textContent = "Check selected Phantom eligibility";
    verify.disabled = item.address_type !== "core_candy_machine" || !wallets.getSession("phantom")?.connected;
    verify.addEventListener("click", async () => {
      const session = wallets.getSession("phantom");
      if (!session?.connected || !session.publicKey || !session.networkVerified) {
        $("watchlist-message").textContent = "Connect Phantom and confirm Solana mainnet-beta first.";
        return;
      }
      try {
        const response = await fetch(`/api/solana/watchlist/${encodeURIComponent(item.address)}/eligibility`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ provider_id: "phantom", wallet_address: session.publicKey, chain_id: "solana-mainnet-beta" })
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || "Eligibility check failed");
        $("watchlist-message").textContent = `${result.status}: ${result.blocked_reason || "checked for the selected Phantom address"}; Telegram: ${result.telegram || "not sent"}.`;
        await loadSolanaWatchlist();
        await Promise.all([loadSummary(), loadDiscoveries()]);
      } catch (error) { $("watchlist-message").textContent = error.message; }
    });
    controls.append(verifiedLabel, review, verify);
    card.append(heading, evidence, reason, urls, schedule, controls);
    container.appendChild(card);
  }
  } catch (error) {
    container.replaceChildren();
    $("watchlist-error-text").textContent = error.message;
    $("watchlist-error").hidden = false;
  } finally {
    $("watchlist-loading").hidden = true;
  }
}

async function loadSourceHealth() {
  const list = $("source-health-list");
  $("source-health-error").hidden = true;
  list.setAttribute("aria-busy", "true");
  try {
    const data = await fetchJson("/api/source-health");
    list.replaceChildren();
    if (!data.items.length) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = "No persisted source-health checks yet.";
      list.appendChild(empty);
    } else {
      for (const item of data.items) {
        const card = document.createElement("article");
        card.className = "source-health-item";
        const name = document.createElement("strong");
      name.textContent = `${item.source_name} · ${item.chain_identifier}`;
        const status = document.createElement("span");
        status.className = `health-status ${item.status === "ok" ? "healthy" : "unhealthy"}`;
        status.textContent = String(item.status || "unknown").replaceAll("_", " ");
        const meta = document.createElement("small");
        meta.textContent = `${item.records} records · ${item.requests} requests · checked ${formatDashboardTime(item.checked_at)}`;
        card.append(name, status, meta);
        list.appendChild(card);
      }
    }
    $("source-health-time").textContent = `Updated ${formatDashboardTime(new Date().toISOString())}`;
  } catch (error) {
    list.replaceChildren();
    $("source-health-error-text").textContent = error.message;
    $("source-health-error").hidden = false;
    $("source-health-time").textContent = "Unavailable";
  } finally { list.setAttribute("aria-busy", "false"); }
}

function detailField(parent, label, value) {
  const box = document.createElement("div");
  box.className = "detail-field";
  const caption = document.createElement("span");
  caption.textContent = label;
  const data = document.createElement("strong");
  data.textContent = value == null || value === "" ? "Unknown" : String(value);
  box.append(caption, data);
  parent.appendChild(box);
}

async function showDetails(id) {
  try {
    const response = await fetch(`/api/discoveries/${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!response.ok) throw new Error("Details unavailable");
    const item = await response.json();
    state.activeDiscovery = item;
    $("details-title").textContent = item.project_name || "Unnamed project";
    const summary = $("detail-summary");
    summary.replaceChildren();
    const details = item.evidence?.details || {};
    for (const [label, value] of [
      ["Network", item.network], ["Contract / mint", item.address],
      ["NFT standard", item.nft_standard], ["Mint date and time", formatDashboardTime(item.mint_date)],
      ["Mint price claim", priceLabel(item)], ["Price verification", item.mint_price_status],
      ["Mint infrastructure", infrastructureLabel(item)],
      ["Official source review", details.official_source_verified === true ? "Verified" : (item.announcement_status || "UNKNOWN")],
      ["Network fee", costLabel(details.network_fee, item.native_gas_token)],
      ["Priority fee", costLabel(details.priority_fee, item.native_gas_token)],
      ["Rent / account cost", costLabel(details.rent_or_account_cost, item.native_gas_token)],
      ["Allowlist", item.allowlist_status],
      ["Eligibility", item.eligibility], ["Verification", item.verification_status],
      ["Risk assessment", item.risk_assessment], ["Discovered", formatDashboardTime(item.discovery_time)],
      ["Last checked", formatDashboardTime(item.source_last_checked_at || item.discovery_time)],
    ]) detailField(summary, label, value);
    const reasons = [];
    if (["UNKNOWN", "PRICE_UNKNOWN"].includes(String(item.mint_price_status || "UNKNOWN").toUpperCase()))
      reasons.push("Mint price is unknown; mint preview is blocked until zero price is verified.");
    if (!item.mint_infrastructure_verified)
      reasons.push(`Mint infrastructure is not verified (${infrastructureLabel(item)}).`);
    if (details.official_source_verified !== true)
      reasons.push("Official project announcement has not been independently verified.");
    if (!item.eligibility || String(item.eligibility).toLowerCase() === "unknown")
      reasons.push("Eligibility has not been verified for a selected wallet address.");
    $("detail-explanation").textContent = reasons.join(" ") || "Available deterministic checks passed; review the evidence and costs before any separate approval flow.";
    const links = $("detail-source-links");
    links.replaceChildren();
    const sources = [
      ["Discovery source", item.source_url], ["Official project source", details.official_source_url],
      ["Announcement", details.announcement_url], ["Official website", details.website || details.official_website],
      ["Launchpad", details.launchpad_url],
    ];
    for (const [label, url] of sources) {
      const link = createExternalLink(url, label);
      if (link) links.appendChild(link);
    }
    if (!links.childElementCount) links.textContent = "No verified source links are available.";
    $("detail-evidence").textContent = JSON.stringify(item.evidence, null, 2);
    renderOpportunityWallet(item);
    populateMintFunctions(item);
    $("mint-preview-result").textContent = "No preview run.";
    $("details-dialog").showModal();
  } catch (_error) {
    $("detail-explanation").textContent = "Details could not be loaded. Refresh the saved observations and try again.";
    if (!$("details-dialog").open) $("details-dialog").showModal();
  }
}

function renderWalletProvider(providerId, stateId, detailsId) {
  const status = $(stateId);
  const details = $(detailsId);
  const session = wallets.getSession(providerId);
  const extraStatus = providerId === "binance_agentic" ? state.binanceStatus : "";
  const connected = Boolean(session?.connected);
  if (!connected && state.walletStatuses[providerId] === "connected") state.walletStatuses[providerId] = "disconnected";
  const walletStatus = providerId === "binance_agentic" ? extraStatus : state.walletStatuses[providerId];
  const displayStatus = connected && walletStatus === "loading" ? "loading"
    : (connected ? "connected" : (walletStatus || "unavailable"));
  status.textContent = displayStatus.replaceAll("_", " ").replace(/^./, (character) => character.toUpperCase());
  status.classList.toggle("connected", connected);
  status.classList.toggle("error", displayStatus === "error" || displayStatus === "unavailable");
  status.classList.toggle("pending", displayStatus === "loading" || displayStatus === "checking");
  if (!connected) {
    const messages = {
      unavailable: providerId === "binance_agentic" ? "The local Binance CLI state could not be read." : "Wallet extension was not detected in this browser.",
      error: state.walletErrors[providerId] || "Wallet data could not be refreshed; check the extension and network, then retry.",
      disconnected: providerId === "binance_agentic" ? "Binance CLI reports no connected wallet session." : "Disconnected from this dashboard session.",
      loading: "Refreshing read-only wallet state…", checking: "Checking the local Binance wallet status…",
    };
    details.textContent = messages[displayStatus] || "Connect to read the public address, supported network, and native balance.";
    return;
  }
  const items = Object.entries(session.addresses).map(([chainId, address]) => {
    const network = SUPPORTED_WALLET_NETWORKS[chainId];
    return `${network.name}: ${shortWalletAddress(address)} · ${session.balances[chainId] ?? "balance unavailable"} ${network.nativeToken}`;
  });
  details.textContent = providerId === "phantom"
    ? `Phantom public key: ${shortWalletAddress(session.publicKey)} · SOL balance: ${session.balances["solana-mainnet-beta"] ?? "unavailable"} · ${session.networkVerified ? "Hunter RPC confirmed mainnet-beta" : "Hunter RPC/network verification unavailable"}; Phantom does not expose its selected cluster.`
    : items.join(" | ") || "Connected, but no supported address is available.";
}

function renderWallets() {
  renderWalletProvider("metamask", "metamask-state", "metamask-details");
  renderWalletProvider("binance_agentic", "binance-state", "binance-details");
  renderWalletProvider("phantom", "phantom-state", "phantom-details");
  const phantom = wallets.getSession("phantom");
  const coreConfirm = $("core-wallet-confirm");
  const coreButton = $("core-asset-preview-button");
  if (coreConfirm && coreButton) {
    const confirmedWallet = coreConfirm.dataset.walletAddress;
    if (confirmedWallet && (!phantom?.connected || !phantom.networkVerified
      || confirmedWallet !== (phantom?.publicKey || ""))) {
      coreConfirm.checked = false;
      delete coreConfirm.dataset.walletAddress;
    }
    $("core-wallet-address").textContent = phantom?.connected && phantom.networkVerified
      ? `Phantom mainnet wallet: ${shortWalletAddress(phantom.publicKey)} · ${phantom.balances?.["solana-mainnet-beta"] ?? "balance unavailable"} SOL`
      : "Phantom mainnet wallet: unavailable — connect Phantom and verify it through Hunter RPC.";
    coreButton.disabled = !(phantom?.connected && phantom.networkVerified && coreConfirm.checked);
  }
  $("connect-metamask").textContent = wallets.getSession("metamask")?.connected
    ? "Disconnect MetaMask from Hunter" : "Connect MetaMask";
  $("connect-phantom").textContent = wallets.getSession("phantom")?.connected
    ? "Disconnect Phantom" : "Connect Phantom";
  $("refresh-metamask").disabled = !wallets.getSession("metamask")?.connected || state.walletStatuses.metamask === "loading";
  $("refresh-phantom").disabled = !wallets.getSession("phantom")?.connected || state.walletStatuses.phantom === "loading";
}

async function refreshInjectedWallet(providerId) {
  const buttonId = providerId === "metamask" ? "refresh-metamask" : "refresh-phantom";
  const button = $(buttonId);
  const session = wallets.getSession(providerId);
  if (!session?.connected) return;
  button.disabled = true;
  state.walletStatuses[providerId] = "loading";
  renderWallets();
  try {
    if (providerId === "metamask") await wallets.refreshMetaMask(wallets.metamaskProvider);
    else await wallets.refreshPhantom(wallets.phantomProvider);
    const refreshed = wallets.getSession(providerId);
    state.walletStatuses[providerId] = refreshed?.connected ? "connected" : (wallets.lastError ? "error" : "disconnected");
    if (wallets.lastError) state.walletErrors[providerId] = wallets.lastError;
    else delete state.walletErrors[providerId];
  } catch (_) {
    state.walletStatuses[providerId] = "error";
    state.walletErrors[providerId] = "Wallet data could not be refreshed; check the extension and retry.";
  } finally {
    renderWallets();
    if (state.activeDiscovery) renderOpportunityWallet(state.activeDiscovery);
  }
}

async function refreshBinanceWallet() {
  const button = $("refresh-binance");
  button.disabled = true;
  state.binanceStatus = "loading";
  renderWallets();
  try {
    const response = await fetch("/api/wallets/binance", { cache: "no-store" });
    if (!response.ok) throw new Error("unavailable");
    const snapshot = await response.json();
    state.binanceStatus = snapshot.status;
    if (snapshot.status === "connected" || snapshot.status === "disconnected") wallets.setBinanceSnapshot(snapshot);
    else wallets.disconnect("binance_agentic");
  } catch (_) {
    state.binanceStatus = "unavailable";
    wallets.disconnect("binance_agentic");
  } finally {
    button.disabled = false;
    renderWallets();
    if (state.activeDiscovery) renderOpportunityWallet(state.activeDiscovery);
  }
}

function renderOpportunityWallet(item) {
  const select = $("opportunity-wallet-select");
  const status = $("opportunity-wallet-status");
  select.replaceChildren();
  const chainId = String(item.chain_id ?? "");
  const network = SUPPORTED_WALLET_NETWORKS[chainId];
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = network ? "Choose a connected wallet" : "Wallet selection is unavailable for this network";
  select.appendChild(placeholder);
  const options = network ? wallets.providersForChain(chainId) : [];
  const selection = wallets.getSelection(String(item.id));
  for (const candidate of options) {
    const option = document.createElement("option");
    option.value = candidate.providerId;
    option.textContent = `${candidate.name} · ${shortWalletAddress(candidate.address)}${candidate.balance == null ? "" : ` · ${candidate.balance} ${network.nativeToken}`}`;
    option.selected = selection?.providerId === candidate.providerId
      && selection.address.toLowerCase() === candidate.address.toLowerCase();
    select.appendChild(option);
  }
  select.disabled = options.length === 0;
  const detail = item.evidence?.details || {};
  const isMeowCandidate = String(item.id) === "27"
    && String(item.address || "").toLowerCase() === "0xb8164afefc53910d7a74cece15a60a9e0c86089e";
  const campaignButton = $("meow-campaign-check-button");
  campaignButton.hidden = !isMeowCandidate;
  campaignButton.disabled = !isMeowCandidate || !selection
    || selection.providerId !== "binance_agentic" || selection.chainId !== "56";
  const priceVerifiedZero = (detail.mint_price_verified === true && String(item.mint_price ?? "") === "0")
    || (item.mint_price_status === "PRICE_VERIFIED_FREE" && detail.price_evidence?.verified === true
      && String(detail.price_evidence.amount_lamports) === "0" && !detail.price_evidence.spl_payment_mint);
  $("mint-preview-button").disabled = options.length === 0 || !selection || !priceVerifiedZero;
  if (!priceVerifiedZero) {
    $("mint-preview-result").textContent = "Blocked: mint price is not verified as zero. No preview transaction was prepared.";
  }
  const isSolana = chainId === "solana-mainnet-beta";
  $("evm-preview-controls").hidden = isSolana;
  $("solana-preview-controls").hidden = !isSolana;
  $("solana-preview-button").disabled = !selection || selection.providerId !== "phantom";
  status.textContent = selection
    ? `Selected ${selection.providerId === "metamask" ? "MetaMask" : selection.providerId === "phantom" ? "Phantom" : "Binance Agentic Wallet"} (${shortWalletAddress(selection.address)}) on ${network?.name || chainId}. Eligibility is checked only for this exact address.`
    : "No wallet selected. Eligibility is not checked here.";
}

function populateMintFunctions(item) {
  const select = $("mint-function-select");
  select.replaceChildren();
  const functions = item.evidence?.details?.mint_functions;
  const candidates = Array.isArray(functions) ? functions.filter((fn) => fn && typeof fn.name === "string") : [];
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = candidates.length ? "Choose inspected mint function" : "No inspected function available";
  select.appendChild(placeholder);
  for (const fn of candidates) {
    const option = document.createElement("option");
    option.value = fn.name;
    const types = Array.isArray(fn.inputs) ? fn.inputs.map((input) => input.type).join(",") : "";
    option.textContent = `${fn.name}(${types})`;
    select.appendChild(option);
  }
}

function displayMintPreview(result) {
  const status = result.status || "UNKNOWN";
  const price = result.mint_price_status === "VERIFIED_ZERO"
    ? `Verified mint price: 0 ${result.native_gas_token || "native token"}`
    : `Mint price: ${result.mint_price_status || "UNKNOWN"}`;
  const fee = result.network_fee_native != null
    ? `Estimated gas: ${result.estimated_gas_limit} · ${result.network_fee_native} ${result.native_gas_token}`
    : "Estimated gas: unavailable";
  const digest = result.transaction_digest ? `Transaction digest: ${result.transaction_digest}` : "No transaction prepared";
  $("mint-preview-result").textContent = [
    `${status} · eligibility ${result.eligibility_status || "unknown"}`,
    price, fee, digest, result.detail || result.error || "No transaction was signed or sent.",
  ].join("\n");
}

wallets.subscribe(() => {
  renderWallets();
  if (state.activeDiscovery) renderOpportunityWallet(state.activeDiscovery);
  if (currentCorePreview) {
    const session = wallets.getSession("phantom");
    if (!session?.connected || !session.networkVerified || session.publicKey !== currentCorePreview.wallet_address) {
      $("core-transaction-confirm").checked = false;
      $("core-asset-execute-button").disabled = true;
      $("core-asset-execution-status").textContent = "BLOCKED — Phantom account or mainnet state changed. Build a fresh preview for the selected address.";
    }
  }
  void loadSolanaWatchlist().catch(() => {});
});

$("core-wallet-confirm").addEventListener("change", (event) => {
  const session = wallets.getSession("phantom");
  if (!event.target.checked || !session?.connected || !session.networkVerified) {
    event.target.checked = false;
    delete event.target.dataset.walletAddress;
  } else {
    event.target.dataset.walletAddress = session.publicKey;
  }
  renderWallets();
});

$("core-asset-preview-button").addEventListener("click", async () => {
  const session = wallets.getSession("phantom");
  const confirmation = $("core-wallet-confirm");
  const output = $("core-asset-preview-status");
  const details = $("core-asset-preview-details");
  const button = $("core-asset-preview-button");
  const metadataUri = $("core-metadata-uri").value.trim();
  currentCorePreview = null;
  details.hidden = true;
  details.replaceChildren();
  $("core-asset-execution").hidden = true;
  $("core-transaction-confirm").checked = false;
  $("core-asset-execute-button").disabled = true;
  if (!session?.connected || !session.networkVerified || !confirmation.checked
    || confirmation.dataset.walletAddress !== session.publicKey) {
    output.textContent = "BLOCKED_WALLET_NOT_CONFIRMED — Connect Phantom on mainnet and confirm the exact displayed address.";
    return;
  }
  if (!metadataUri) {
    output.textContent = "BLOCKED_METADATA_URI — Host the supplied JSON and SVG at public HTTPS URLs first.";
    return;
  }
  button.disabled = true;
  output.textContent = "Validating hosted metadata, constructing the Core transaction, and simulating read-only…";
  try {
    const response = await fetch("/api/solana/self-created-core-asset-preview", {
      method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ provider_id: "phantom", chain_id: "solana-mainnet-beta",
        wallet_address: session.publicKey, wallet_confirmed: true, metadata_uri: metadataUri }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Core asset preview failed");
    const current = wallets.getSession("phantom");
    if (!current?.connected || current.publicKey !== session.publicKey
      || result.wallet_address && result.wallet_address !== session.publicKey) {
      throw new Error("Phantom account changed during preview; discard this result and retry.");
    }
    output.textContent = `${result.status}${result.reason ? ` — ${result.reason}` : ""}`;
    if (result.status === "PREVIEW_READY") {
      currentCorePreview = result;
      $("core-asset-execution").hidden = false;
      $("core-transaction-confirm").checked = false;
      $("core-asset-execute-button").disabled = true;
      $("core-asset-execution-status").textContent = "Review the exact transaction above, check approval, then continue to Phantom.";
      const cost = result.costs;
      const toSol = (value) => `${(Number(value) / 1e9).toFixed(9)} SOL`;
      details.hidden = false;
      details.textContent = [
        `Asset address: ${result.asset_address}`,
        `Owner / payer: ${result.wallet_address}`,
        `Network: ${result.network}`,
        `Recent blockhash: ${result.transaction.recent_blockhash}`,
        `Last valid block height: ${result.transaction.last_valid_block_height}`,
        `Approved message SHA-256: ${result.transaction.message_sha256}`,
        `Metadata URI: ${result.metadata_uri}`,
        `Core program: ${result.transaction.instruction.programId}`,
        `Instructions: ${result.transaction.instruction.instructionCount}`,
        `Required signers: ${result.transaction.instruction.requiredSigners.join(", ")}`,
        `Campaign mint price: not applicable (self-created asset)`,
        `Instruction accounts: ${result.transaction.instruction.accountMetas.map((meta) => `${meta.address} [${meta.isSigner ? "signer" : "account"}, ${meta.isWritable ? "writable" : "read-only"}]`).join("; ")}`,
        `Network fee: ${toSol(cost.fee_lamports)}`,
        `Rent: ${toSol(cost.rent_lamports)}`,
        `Core protocol fee: ${toSol(cost.protocol_fee_lamports)}`,
        `Priority fee: ${toSol(cost.priority_fee_lamports)}`,
        `Estimated total: ${toSol(cost.total_lamports)} · ${cost.total_lamports} lamports`,
        `Preview SOL cap: ${toSol(cost.maximum_lamports)} · available balance: ${toSol(cost.available_lamports)}`,
        `Simulation: ${result.simulation.status} · ${result.simulation.units_consumed ?? "units unavailable"} compute units`,
        `Safety: Phantom signature required later; no wallet signature requested; no broadcast attempted.`,
        `Volatile signer: ${result.asset_signer_retention}`,
        `Unsigned transaction (base64): ${result.transaction.unsigned_transaction_base64}`,
      ].join("\n");
    }
  } catch (error) {
    output.textContent = `BLOCKED_SIMULATION — ${error.message || "Core asset preview failed; no transaction was signed or sent."}`;
  } finally {
    const current = wallets.getSession("phantom");
    button.disabled = !(current?.connected && current.networkVerified && confirmation.checked
      && confirmation.dataset.walletAddress === current.publicKey);
  }
});

$("core-transaction-confirm").addEventListener("change", (event) => {
  const session = wallets.getSession("phantom");
  const preview = currentCorePreview;
  $("core-asset-execute-button").disabled = !(event.target.checked && preview?.status === "PREVIEW_READY"
    && session?.connected && session.networkVerified && session.publicKey === preview.wallet_address
    && preview.network === "solana-mainnet-beta");
});

$("core-asset-execute-button").addEventListener("click", async () => {
  const preview = currentCorePreview;
  const session = wallets.getSession("phantom");
  const output = $("core-asset-execution-status");
  const button = $("core-asset-execute-button");
  const provider = globalThis.phantom?.solana;
  if (!preview || !$("core-transaction-confirm").checked || !session?.connected || !session.networkVerified
    || session.publicKey !== preview.wallet_address || preview.network !== "solana-mainnet-beta"
    || provider?.isPhantom !== true || !provider.signTransaction || !globalThis.solanaWeb3?.Transaction) {
    output.textContent = "BLOCKED — Preview, Phantom session, or supported signTransaction provider is unavailable. Build a fresh preview if it expired.";
    return;
  }
  button.disabled = true;
  output.textContent = "Revalidating the exact transaction and cost cap, then requesting Phantom's signature…";
  try {
    const approved = await fetch("/api/solana/self-created-core-asset-approve", {
      method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ preview_id: preview.preview_id, provider_id: "phantom",
        chain_id: "solana-mainnet-beta", wallet_address: preview.wallet_address,
        asset_address: preview.asset_address, approved: true }),
    });
    const signing = await approved.json();
    if (!approved.ok || signing.status !== "PHANTOM_SIGNATURE_REQUIRED") throw new Error(signing.detail || "Transaction preflight failed");
    const transaction = globalThis.solanaWeb3.VersionedTransaction.deserialize(
      Uint8Array.from(atob(signing.transaction_base64), (c) => c.charCodeAt(0)));
    const approvedDigest = await crypto.subtle.digest("SHA-256", transaction.message.serialize());
    const digestHex = [...new Uint8Array(approvedDigest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
    if (digestHex !== preview.transaction.message_sha256) throw new Error("Prepared transaction does not match the reviewed preview");
    const beforePrompt = wallets.getSession("phantom");
    if (!beforePrompt?.connected || beforePrompt.publicKey !== preview.wallet_address
      || provider.publicKey?.toBase58?.() !== preview.wallet_address) throw new Error("Phantom account changed before signing");
    const signed = await provider.signTransaction(transaction);
    const originalMessage = transaction.message.serialize();
    const signedMessage = signed.message.serialize();
    const hasBothSignatures = signed.signatures.length === 2
      && signed.signatures.every((signature) => signature.some((byte) => byte !== 0));
    if (originalMessage.length !== signedMessage.length
      || originalMessage.some((byte, index) => byte !== signedMessage[index]) || !hasBothSignatures) {
      throw new Error("Phantom returned missing or changed transaction signatures");
    }
    const afterPrompt = wallets.getSession("phantom");
    if (!afterPrompt?.connected || afterPrompt.publicKey !== preview.wallet_address
      || provider.publicKey?.toBase58?.() !== preview.wallet_address) throw new Error("Phantom account changed during signing; no submission made");
    const signedBase64 = btoa(String.fromCharCode(...signed.serialize()));
    output.textContent = "Phantom signed the approved message. Hunter is validating signatures and current mainnet costs before one submission…";
    const submitted = await fetch("/api/solana/self-created-core-asset-submit", {
      method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ preview_id: preview.preview_id, wallet_address: preview.wallet_address,
        signed_transaction_base64: signedBase64 }),
    });
    const result = await submitted.json();
    if (!submitted.ok) throw new Error(result.detail || "Submission failed; check status before any further action");
    output.textContent = `Submitted once · signature ${result.signature} · asset ${result.asset_address}. Checking mainnet confirmation…`;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      await new Promise((resolve) => setTimeout(resolve, 3000));
      const statusResponse = await fetch(`/api/solana/self-created-core-asset-status/${encodeURIComponent(preview.preview_id)}`, { cache: "no-store" });
      const status = await statusResponse.json();
      if (!statusResponse.ok) throw new Error(status.detail || "Could not read transaction status");
      output.textContent = `${status.status} · ${status.confirmation_status || "checking"} · signature ${status.signature} · asset ${status.asset_address}`;
      if (["CONFIRMED", "FAILED", "OWNERSHIP_UNVERIFIED"].includes(status.status)) break;
    }
    currentCorePreview = null;
    $("core-transaction-confirm").checked = false;
  } catch (error) {
    output.textContent = `BLOCKED / CHECK STATUS — ${error.message || "The workflow stopped. Do not retry automatically; refresh transaction status first."}`;
  } finally {
    button.disabled = true;
  }
});

async function loadLatestCoreTransaction() {
  const data = await fetchJson("/api/solana/self-created-core-asset-latest");
  if (!data.signature) return;
  latestCoreTransaction = data;
  $("core-last-transaction").hidden = false;
  $("core-last-transaction-info").textContent = [
    `${data.status} · asset ${data.asset_address} · wallet ${shortWalletAddress(data.wallet_address)}`,
    `Signature: ${data.signature}`,
    `Recorded cost: ${(Number(data.estimated_total_lamports) / 1e9).toFixed(9)} SOL`,
  ].join("\n");
  const link = $("core-last-transaction-link");
  link.href = `https://explorer.solana.com/tx/${encodeURIComponent(data.signature)}?cluster=mainnet-beta`;
  link.hidden = false;
}

$("core-last-transaction-check").addEventListener("click", async () => {
  const output = $("core-last-transaction-status");
  const button = $("core-last-transaction-check");
  if (!latestCoreTransaction?.preview_id) {
    output.textContent = "No saved transaction signature is available.";
    return;
  }
  button.disabled = true;
  output.textContent = "Checking the saved signature using read-only mainnet status calls… No transaction will be resent.";
  try {
    const data = await fetchJson(`/api/solana/self-created-core-asset-status/${encodeURIComponent(latestCoreTransaction.preview_id)}`);
    output.textContent = `${data.status} · ${data.confirmation_status || "status unavailable"} · signature ${data.signature || latestCoreTransaction.signature}`;
    latestCoreTransaction = { ...latestCoreTransaction, ...data };
    $("core-last-transaction-info").textContent = [
      `${data.status} · asset ${data.asset_address} · wallet ${shortWalletAddress(data.wallet_address)}`,
      `Signature: ${data.signature || latestCoreTransaction.signature}`,
      `Recorded cost: ${(Number(data.estimated_total_lamports) / 1e9).toFixed(9)} SOL`,
    ].join("\n");
  } catch (error) {
    output.textContent = `Status check unavailable: ${error.message}. The saved transaction was not resubmitted.`;
  } finally {
    button.disabled = false;
  }
});

$("connect-metamask").addEventListener("click", async () => {
  const button = $("connect-metamask");
  if (wallets.getSession("metamask")?.connected) {
    wallets.disconnect("metamask");
    state.walletStatuses.metamask = "disconnected";
    delete state.walletErrors.metamask;
    $("wallet-message").textContent = "MetaMask was disconnected from this dashboard session; Binance remains unchanged. Extension permission was not revoked.";
    return;
  }
  button.disabled = true;
  state.walletStatuses.metamask = "loading";
  renderWallets();
  $("wallet-message").textContent = "Waiting for MetaMask account access…";
  try {
    await wallets.connectMetaMask();
    state.walletStatuses.metamask = "connected";
    delete state.walletErrors.metamask;
    $("wallet-message").textContent = "MetaMask connected read-only. No signing or transactions are available.";
  } catch (error) {
    state.walletStatuses.metamask = /not detected|not found/i.test(error.message) ? "unavailable" : "error";
    state.walletErrors.metamask = error.message;
    $("wallet-message").textContent = error.message;
  } finally {
    button.disabled = false;
    renderWallets();
  }
});

$("refresh-metamask").addEventListener("click", () => refreshInjectedWallet("metamask"));
$("refresh-binance").addEventListener("click", refreshBinanceWallet);

$("connect-phantom").addEventListener("click", async () => {
  const button = $("connect-phantom");
  if (wallets.getSession("phantom")?.connected) {
    await wallets.disconnectPhantom();
    state.walletStatuses.phantom = "disconnected";
    delete state.walletErrors.phantom;
    $("wallet-message").textContent = "Phantom was disconnected. MetaMask and Binance sessions remain unchanged.";
    return;
  }
  button.disabled = true;
  state.walletStatuses.phantom = "loading";
  renderWallets();
  $("wallet-message").textContent = "Waiting for Phantom public-key access…";
  try {
    await wallets.connectPhantom(globalThis.phantom?.solana);
    state.walletStatuses.phantom = "connected";
    delete state.walletErrors.phantom;
    $("wallet-message").textContent = "Phantom connected read-only. The displayed network is verified through Hunter's configured Solana mainnet RPC. No signing or transactions are available.";
  } catch (error) {
    state.walletStatuses.phantom = /not found/i.test(error.message) ? "unavailable" : "error";
    state.walletErrors.phantom = error.message;
    $("wallet-message").textContent = error.message;
  } finally {
    button.disabled = false;
    renderWallets();
  }
});

$("refresh-phantom").addEventListener("click", () => refreshInjectedWallet("phantom"));

$("opportunity-wallet-select").addEventListener("change", (event) => {
  const item = state.activeDiscovery;
  if (!item) return;
  const opportunityId = String(item.id);
  if (!event.target.value) wallets.clearSelection(opportunityId);
  else {
    try {
      wallets.selectOpportunity(opportunityId, event.target.value, String(item.chain_id));
    } catch (_) {
      wallets.clearSelection(opportunityId);
      $("opportunity-wallet-status").textContent = "That wallet is not connected on the opportunity's network.";
    }
  }
  $("meow-campaign-check-result").hidden = true;
  $("meow-campaign-check-result").textContent = "No campaign check run.";
  renderOpportunityWallet(item);
});

$("meow-campaign-check-button").addEventListener("click", async () => {
  const item = state.activeDiscovery;
  if (!item) return;
  const selection = wallets.getSelection(String(item.id));
  const output = $("meow-campaign-check-result");
  const button = $("meow-campaign-check-button");
  if (!selection || selection.providerId !== "binance_agentic" || selection.chainId !== "56") {
    output.hidden = false;
    output.textContent = "Select the connected Binance Agentic Wallet on BNB Chain for this opportunity.";
    return;
  }
  button.disabled = true;
  output.hidden = false;
  output.textContent = "Checking verified campaign state for the selected wallet. Read-only RPC calls only…";
  try {
    const response = await fetch(`/api/discoveries/${encodeURIComponent(item.id)}/meow-campaign-check`, {
      method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ provider_id: selection.providerId,
        wallet_address: selection.address, chain_id: selection.chainId }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Campaign verification failed");
    if (result.selected_wallet?.toLowerCase() !== selection.address.toLowerCase()
      || String(result.chain_id) !== selection.chainId) {
      throw new Error("Campaign check result does not match the selected wallet and chain");
    }
    output.textContent = [
      `Source/ABI: ${result.contract_source_verified ? "verified" : "UNKNOWN"}`,
      `NFT standard: ${result.nft_standard}`,
      `Agent ID eligibility for ${shortWalletAddress(selection.address)}: ${result.agent_eligible ? "eligible" : "ineligible"}`,
      `Price (quantity ${result.quantity}): ${result.mint_price_bnb} BNB`,
      `Mint status: ${result.availability} · ${result.total_minted}/${result.max_supply} minted · ${result.wallet_minted}/${result.max_per_wallet} for wallet`,
      `eth_call simulation: ${result.simulation}`,
      `Estimated gas: ${result.gas_limit ?? "UNKNOWN"} · fee: ${result.gas_cost_bnb ?? "UNKNOWN"} BNB · total: ${result.total_cost_bnb ?? "UNKNOWN"} BNB`,
      result.unsigned_transaction ? `Unsigned transaction summary:\n${JSON.stringify(result.unsigned_transaction, null, 2)}` : "Unsigned transaction summary: BLOCKED",
      "Read-only check only. The standard/risk gates for the generic wallet preview remain in force.",
    ].join("\n");
  } catch (error) {
    output.textContent = error.message || "Read-only campaign check failed; no transaction was signed or sent.";
  } finally {
    button.disabled = false;
  }
});

$("mint-preview-button").addEventListener("click", async () => {
  const item = state.activeDiscovery;
  if (!item) return;
  const selection = wallets.getSelection(String(item.id));
  if (!selection) {
    $("mint-preview-result").textContent = "Select a wallet for this opportunity first.";
    return;
  }
  let argumentsValue;
  try {
    argumentsValue = JSON.parse($("mint-arguments").value);
  } catch (_) {
    $("mint-preview-result").textContent = "Arguments must be valid JSON.";
    return;
  }
  const functionName = $("mint-function-select").value;
  if (!functionName) {
    $("mint-preview-result").textContent = "Choose an inspected mint function first.";
    return;
  }
  const button = $("mint-preview-button");
  button.disabled = true;
  $("mint-preview-result").textContent = "Preparing read-only preview for the selected wallet…";
  try {
    const response = await fetch(`/api/discoveries/${encodeURIComponent(item.id)}/mint-preview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      cache: "no-store",
      body: JSON.stringify({
        provider_id: selection.providerId,
        wallet_address: selection.address,
        chain_id: selection.chainId,
        function_name: functionName,
        arguments: argumentsValue,
      }),
    });
    const prepared = await response.json();
    if (!response.ok) throw new Error(prepared.detail || "Preview preparation failed");
    if (prepared.provider_id !== selection.providerId
      || prepared.wallet_address?.toLowerCase() !== selection.address.toLowerCase()
      || String(prepared.chain_id) !== selection.chainId) {
      throw new Error("Preview response does not match the selected wallet and network");
    }
    const result = prepared.status === "AWAITING_METAMASK_READ_ONLY_SIMULATION"
      ? await wallets.simulateMetaMaskPreview(String(item.id), prepared) : prepared;
    displayMintPreview(result);
  } catch (error) {
    $("mint-preview-result").textContent = error.message || "Read-only preview failed; no transaction was signed or sent.";
  } finally {
    renderOpportunityWallet(item);
  }
});

$("solana-preview-button").addEventListener("click", async () => {
  const item = state.activeDiscovery;
  if (!item) return;
  const selection = wallets.getSelection(String(item.id));
  if (!selection || selection.providerId !== "phantom" || selection.chainId !== "solana-mainnet-beta") {
    $("solana-preview-result").textContent = "Select the connected Phantom wallet for this Solana opportunity first.";
    return;
  }
  const button = $("solana-preview-button");
  button.disabled = true;
  $("solana-preview-result").textContent = "Reading stored Solana mint evidence…";
  try {
    const response = await fetch(`/api/discoveries/${encodeURIComponent(item.id)}/solana-preview`, {
      method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
      body: JSON.stringify({ provider_id: selection.providerId, wallet_address: selection.address, chain_id: selection.chainId }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Solana preview failed");
    if (result.provider_id !== selection.providerId || result.wallet_address !== selection.address
      || result.chain_id !== selection.chainId) throw new Error("Solana preview does not match selected wallet and network");
    $("solana-preview-result").textContent = [
      `${result.status} · eligibility ${result.eligibility_status}`,
      `Mint price: ${result.mint_price.status}${result.mint_price.amount == null ? "" : ` · ${result.mint_price.amount} SOL`}`,
      `Network fee: ${result.network_fee.status}`,
      `Rent/account cost: ${result.rent_or_account_cost.status}`,
      `Total estimated cost: ${result.total_estimated_cost.status}`,
      result.detail,
    ].join("\n");
  } catch (error) {
    $("solana-preview-result").textContent = error.message || "Solana evidence preview failed.";
  } finally {
    renderOpportunityWallet(item);
  }
});

$("chain-filter").addEventListener("change", (event) => {
  state.chain = event.target.value;
  discoveryVisibleCount = RESULT_BATCH_SIZE;
  void loadDiscoveries();
});
$("status-filter").addEventListener("change", (event) => {
  state.status = event.target.value;
  discoveryVisibleCount = RESULT_BATCH_SIZE;
  void loadDiscoveries();
});
$("discovery-search").addEventListener("input", (event) => {
  state.search = event.target.value.trim().toLowerCase();
  discoveryVisibleCount = RESULT_BATCH_SIZE;
  renderDiscoveries(state.discoveries, state.lastDiscoveryTotal ?? state.discoveries.length);
});

$("price-filter").addEventListener("change", (event) => {
  state.priceStatus = event.target.value;
  discoveryVisibleCount = RESULT_BATCH_SIZE;
  void loadDiscoveries();
});
$("campaign-chain-filter").addEventListener("change", (event) => {
  state.campaignChain = event.target.value;
  campaignVisibleCount = RESULT_BATCH_SIZE;
  void loadCampaignResults();
});
$("campaign-status-filter").addEventListener("change", (event) => {
  state.campaignStatus = event.target.value;
  campaignVisibleCount = RESULT_BATCH_SIZE;
  void loadCampaignResults();
});
$("campaign-results-more").addEventListener("click", () => {
  campaignVisibleCount = campaignVisibleCount >= campaignResultTotal
    ? RESULT_BATCH_SIZE : campaignVisibleCount + RESULT_BATCH_SIZE;
  updateCampaignResultWindow();
});
$("discoveries-more").addEventListener("click", () => {
  discoveryVisibleCount = discoveryVisibleCount >= [...$("results-body").children].length
    ? RESULT_BATCH_SIZE : discoveryVisibleCount + RESULT_BATCH_SIZE;
  updateDiscoveryResultWindow(
    [...$("results-body").children].length,
    state.lastDiscoveryTotal ?? state.discoveries.length,
  );
});
$("campaign-results-refresh").addEventListener("click", loadCampaignResults);
$("campaign-results-retry").addEventListener("click", loadCampaignResults);
$("run-campaign-scan").addEventListener("click", startCampaignCycle);
$("upcoming-filter").addEventListener("change", (event) => {
  state.upcomingOnly = event.target.checked;
  discoveryVisibleCount = RESULT_BATCH_SIZE;
  void loadDiscoveries();
});

const pageCopy = {
  overview: { title: "Overview", eyebrow: "YOUR NFT SCOUT", description: "A calmer little home for launches, evidence and wallet readiness." },
  campaigns: { title: "Campaigns", eyebrow: "FRESH FINDS, CAREFUL CHECKS", description: "Scan public feeds, then review each lead at your own pace." },
  opportunities: { title: "Discoveries", eyebrow: "WHAT WE’VE SPOTTED", description: "Browse saved observations and see what’s known so far." },
  watchlist: { title: "Watchlist", eyebrow: "KEEP AN EYE ON IT", description: "Save projects you want to come back to." },
  wallets: { title: "Wallets", eyebrow: "YOUR CONNECTIONS", description: "Connect only when you need a public address or balance." },
  activity: { title: "Activity", eyebrow: "HOW THINGS ARE GOING", description: "A quiet place to check scans, sources and network health." },
};

function pageFromHash() {
  const hash = decodeURIComponent(window.location.hash.slice(1));
  if (hash === "campaign-scan") return "campaigns";
  const section = document.getElementById(hash);
  return section?.dataset.page || (pageCopy[hash] ? hash : "overview");
}

function activatePage(page, updateHistory = false) {
  const activePage = pageCopy[page] ? page : "overview";
  const copy = pageCopy[activePage];
  document.querySelectorAll(".page-section[data-page]").forEach((section) => {
    section.hidden = section.dataset.page !== activePage;
  });
  document.querySelectorAll(".nav-link[data-page]").forEach((link) => {
    const active = link.dataset.page === activePage;
    link.classList.toggle("active", active);
    link.setAttribute("aria-current", active ? "page" : "false");
  });
  document.querySelector(".page-heading h1").textContent = copy.title;
  document.querySelector(".page-heading .eyebrow").textContent = copy.eyebrow;
  document.querySelector(".page-heading .page-description").textContent = copy.description;
  if (updateHistory && window.location.hash !== `#${activePage}`) {
    window.history.pushState(null, "", `#${activePage}`);
  }
}

document.querySelectorAll(".nav-link[data-page]").forEach((link) => link.addEventListener("click", (event) => {
  event.preventDefault();
  activatePage(link.dataset.page, true);
  window.scrollTo({ top: 0, behavior: "smooth" });
}));
document.querySelector(".brand").addEventListener("click", (event) => {
  event.preventDefault();
  activatePage("overview", true);
  window.scrollTo({ top: 0, behavior: "smooth" });
});
window.addEventListener("popstate", () => activatePage(pageFromHash()));
window.addEventListener("hashchange", () => activatePage(pageFromHash()));
activatePage(pageFromHash());

$("refresh-button").addEventListener("click", refresh);
$("rpc-test-button").addEventListener("click", testBnbRpc);
$("watchlist-refresh").addEventListener("click", loadSolanaWatchlist);
$("watchlist-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const data = Object.fromEntries(new FormData(form).entries());
  try {
    const response = await fetch("/api/solana/watchlist", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Could not add candidate");
    $("watchlist-message").textContent = "Candidate saved with source provenance. Price, schedule and eligibility remain unverified.";
    form.reset();
        await loadSolanaWatchlist();
  } catch (error) { $("watchlist-message").textContent = error.message; }
});
$("close-dialog").addEventListener("click", () => $("details-dialog").close());
$("details-dialog").addEventListener("click", (event) => {
  if (event.target === $("details-dialog")) $("details-dialog").close();
});
refresh();
void loadLatestCoreTransaction().catch(() => {});
void loadRpcDiagnostics();
renderWallets();
void refreshBinanceWallet();
window.setInterval(refresh, 30000);

