"use strict";

export const SUPPORTED_EVM_CHAINS = Object.freeze({
  "1": { name: "Ethereum", nativeToken: "ETH" },
  "56": { name: "BNB Smart Chain", nativeToken: "BNB" },
  "8453": { name: "Base", nativeToken: "ETH" },
  "4663": { name: "Robinhood Chain", nativeToken: "ETH" },
});
export const SUPPORTED_WALLET_NETWORKS = Object.freeze({
  ...SUPPORTED_EVM_CHAINS,
  "solana-mainnet-beta": { name: "Solana mainnet-beta", nativeToken: "SOL", family: "solana" },
});

const ADDRESS = /^0x[0-9a-fA-F]{40}$/;
const PROVIDER_REQUEST_TIMEOUT_MS = 30000;
const SOLANA_READ_TIMEOUT_MS = 10000;

export function normalizeChainId(value) {
  const raw = String(value ?? "");
  if (!/^(?:0x[0-9a-fA-F]+|[0-9]+)$/.test(raw)) throw new Error("Invalid EVM chain ID");
  const parsed = raw.startsWith("0x") ? BigInt(raw) : BigInt(raw);
  const chainId = parsed.toString(10);
  if (!Object.hasOwn(SUPPORTED_EVM_CHAINS, chainId)) throw new Error("Unsupported EVM network");
  return chainId;
}

function normalizeWalletNetworkId(value) {
  const raw = String(value ?? "");
  if (raw === "CT_501") return "solana-mainnet-beta";
  if (raw === "solana-mainnet-beta") return raw;
  return normalizeChainId(raw);
}

function validBalance(value) {
  return typeof value === "string" && /^(?:0|[0-9]+)(?:\.[0-9]+)?$/.test(value);
}

function maskAddress(address) {
  return `${address.slice(0, 6)}…${address.slice(-4)}`;
}

/** Browser-side read-only provider registry. It has no signing or transaction methods. */
export class WalletRegistry {
  constructor({ solanaBalanceReader = defaultSolanaBalanceReader } = {}) {
    this.sessions = new Map();
    this.selections = new Map();
    this.listeners = new Set();
    this.lastError = "";
    this.metamaskProvider = null;
    this.metamaskHandlers = null;
    this.eip6963Providers = new Map();
    this.eip6963Handler = (event) => this.registerEip6963Provider(event?.detail);
    if (typeof globalThis.addEventListener === "function" && typeof globalThis.dispatchEvent === "function") {
      globalThis.addEventListener("eip6963:announceProvider", this.eip6963Handler);
      globalThis.dispatchEvent(new Event("eip6963:requestProvider"));
    }
    this.solanaBalanceReader = solanaBalanceReader;
    this.phantomProvider = null;
    this.phantomHandlers = null;
  }

  subscribe(callback) {
    this.listeners.add(callback);
    return () => this.listeners.delete(callback);
  }

  notify() {
    for (const callback of this.listeners) callback();
  }

  getSession(providerId) {
    const session = this.sessions.get(providerId);
    return session ? structuredClone(session) : null;
  }

  getSessions() {
    return [...this.sessions.values()].map((session) => structuredClone(session));
  }

  setBinanceSnapshot(snapshot) {
    if (!snapshot || snapshot.provider_id !== "binance_agentic") throw new Error("Invalid Binance wallet snapshot");
    if (snapshot.status !== "connected") {
      this.sessions.delete("binance_agentic");
      this.clearSelections("binance_agentic");
      this.notify();
      return;
    }
    const networks = new Map((snapshot.networks || []).map((item) => [String(item.chain_id), item]));
    const addresses = new Map();
    for (const item of snapshot.addresses || []) {
      let chainId;
      try { chainId = normalizeWalletNetworkId(item.chain_id); } catch (_) { continue; }
      const family = networks.get(String(item.chain_id))?.family ?? networks.get(chainId)?.family;
      const validAddress = family === "evm" ? ADDRESS.test(String(item.address))
        : family === "solana" ? /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(String(item.address)) : false;
      if (!validAddress) continue;
      addresses.set(chainId, item.address);
    }
    const balances = new Map();
    for (const item of snapshot.balances || []) {
      let chainId;
      try { chainId = normalizeWalletNetworkId(item.chain_id); } catch (_) { continue; }
      if (item.symbol === SUPPORTED_WALLET_NETWORKS[chainId].nativeToken && validBalance(String(item.amount))) {
        balances.set(chainId, String(item.amount));
      }
    }
    this.sessions.set("binance_agentic", {
      providerId: "binance_agentic", name: "Binance Agentic Wallet", connected: true,
      addresses: Object.fromEntries(addresses), balances: Object.fromEntries(balances),
    });
    this.pruneSelections();
    this.notify();
  }

  registerEip6963Provider(detail) {
    const info = detail?.info;
    const provider = detail?.provider;
    if (!info || !provider || typeof provider.request !== "function") return;
    const uuid = typeof info.uuid === "string" ? info.uuid : "";
    if (uuid) this.eip6963Providers.set(uuid, { info, provider });
  }

  async resolveMetaMaskProvider() {
    if (typeof globalThis.dispatchEvent === "function") {
      try { globalThis.dispatchEvent(new Event("eip6963:requestProvider")); } catch (_) {}
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    const announced = [...this.eip6963Providers.values()]
      .filter(({ info, provider }) => info.rdns?.toLowerCase() === "io.metamask"
        && provider.isMetaMask === true && typeof provider.request === "function");
    const legacy = globalThis.ethereum?.providers;
    const candidates = [
      ...announced.map(({ provider }) => provider),
      ...(Array.isArray(legacy) ? legacy : []),
      globalThis.ethereum,
    ];
    const provider = candidates.find((item) => item?.isMetaMask === true && typeof item.request === "function");
    if (!provider) throw new Error("MetaMask provider was not detected. Unlock the extension and reload this local dashboard.");
    return provider;
  }

  async connectMetaMask(provider = null) {
    provider = provider || await this.resolveMetaMaskProvider();
    if (!provider || provider.isMetaMask !== true || typeof provider.request !== "function") {
      throw new Error("MetaMask EVM provider was not found in this browser");
    }
    try {
      const accounts = await withTimeout(provider.request({ method: "eth_requestAccounts" }), PROVIDER_REQUEST_TIMEOUT_MS);
      const chainId = normalizeChainId(await withTimeout(provider.request({ method: "eth_chainId" }), PROVIDER_REQUEST_TIMEOUT_MS));
      const address = Array.isArray(accounts) ? accounts[0] : null;
      if (typeof address !== "string" || !ADDRESS.test(address)) throw new Error("invalid account");
      const balanceWei = await withTimeout(provider.request({ method: "eth_getBalance", params: [address, "latest"] }), PROVIDER_REQUEST_TIMEOUT_MS);
      if (typeof balanceWei !== "string" || !/^0x[0-9a-fA-F]+$/.test(balanceWei)) throw new Error("invalid balance");
      this.metamaskProvider = provider;
      this.sessions.set("metamask", {
        providerId: "metamask", name: "MetaMask", connected: true,
        addresses: { [chainId]: address }, balances: { [chainId]: weiToNative(balanceWei) },
      });
      this.installMetaMaskListeners(provider);
      this.lastError = "";
      this.pruneSelections();
      this.notify();
      return this.getSession("metamask");
    } catch (error) {
      this.disconnect("metamask");
      const timedOut = error?.message === "Wallet request timed out";
      throw new Error(timedOut ? "MetaMask did not respond in time. Check its pending account-access prompt, then retry." : "MetaMask account, network, or balance could not be validated");
    }
  }

  async connectPhantom(provider = globalThis.phantom?.solana) {
    if (!provider || provider.isPhantom !== true || typeof provider.connect !== "function") {
      throw new Error("Phantom Solana provider was not found in this browser");
    }
    try {
      const response = await withTimeout(provider.connect(), PROVIDER_REQUEST_TIMEOUT_MS);
      const address = publicKeyString(response?.publicKey ?? provider.publicKey);
      let balance = null;
      try {
        balance = await withTimeout(this.solanaBalanceReader(address), SOLANA_READ_TIMEOUT_MS);
      } catch (_) { /* Wallet connection is independent of an unavailable read-only RPC. */ }
      const networkVerified = balance?.chain_id === "solana-mainnet-beta" && balance?.public_key === address
        && validBalance(String(balance.balance_sol));
      this.phantomProvider = provider;
      this.sessions.set("phantom", {
        providerId: "phantom", name: "Phantom", connected: true,
        publicKey: address,
        addresses: networkVerified ? { "solana-mainnet-beta": address } : {},
        balances: networkVerified ? { "solana-mainnet-beta": String(balance.balance_sol) } : {},
        networkVerified,
      });
      this.installPhantomListeners(provider);
      this.lastError = "";
      this.pruneSelections();
      this.notify();
      return this.getSession("phantom");
    } catch (_) {
      this.disconnect("phantom");
      throw new Error("Phantom account or Solana mainnet balance could not be validated");
    }
  }

  installPhantomListeners(provider) {
    if (this.phantomProvider === provider && this.phantomHandlers) return;
    if (this.phantomProvider && this.phantomHandlers && typeof this.phantomProvider.removeListener === "function") {
      for (const [event, handler] of Object.entries(this.phantomHandlers)) this.phantomProvider.removeListener(event, handler);
    }
    const accountChanged = (key) => {
      if (key == null) return this.disconnect("phantom");
      try {
        const address = publicKeyString(key);
        void this.refreshPhantom(provider, address);
      } catch (_) { this.disconnect("phantom"); }
    };
    const disconnect = () => this.disconnect("phantom");
    this.phantomHandlers = { accountChanged, disconnect };
    if (typeof provider.on === "function") {
      provider.on("accountChanged", accountChanged);
      provider.on("disconnect", disconnect);
    }
  }

  async disconnectPhantom() {
    const provider = this.phantomProvider;
    this.disconnect("phantom");
    if (provider && typeof provider.disconnect === "function") {
      try { await provider.disconnect(); } catch (_) { /* local session is already cleared */ }
    }
  }

  async refreshPhantom(provider, address = null) {
    let selectedAddress;
    try {
      selectedAddress = address ?? publicKeyString(provider.publicKey);
    } catch (_) {
      this.disconnect("phantom");
      this.lastError = "Phantom public key could not be validated";
      return;
    }
    let balance = null;
    try { balance = await withTimeout(this.solanaBalanceReader(selectedAddress), SOLANA_READ_TIMEOUT_MS); } catch (_) {}
    const networkVerified = balance?.chain_id === "solana-mainnet-beta" && balance?.public_key === selectedAddress
      && validBalance(String(balance.balance_sol));
    this.sessions.set("phantom", {
      providerId: "phantom", name: "Phantom", connected: true,
      publicKey: selectedAddress,
      addresses: networkVerified ? { "solana-mainnet-beta": selectedAddress } : {},
      balances: networkVerified ? { "solana-mainnet-beta": String(balance.balance_sol) } : {},
      networkVerified,
    });
    this.lastError = "";
    this.pruneSelections();
    this.notify();
  }

  installMetaMaskListeners(provider) {
    if (this.metamaskProvider === provider && this.metamaskHandlers) return;
    if (this.metamaskProvider && this.metamaskHandlers && typeof this.metamaskProvider.removeListener === "function") {
      for (const [event, handler] of Object.entries(this.metamaskHandlers)) this.metamaskProvider.removeListener(event, handler);
    }
    const accountsChanged = (accounts) => {
      if (!Array.isArray(accounts) || !accounts.length || !ADDRESS.test(String(accounts[0]))) {
        this.disconnect("metamask");
      } else {
        void this.refreshMetaMask(provider, String(accounts[0]));
      }
    };
    const chainChanged = () => { void this.refreshMetaMask(provider); };
    const disconnect = () => this.disconnect("metamask");
    this.metamaskHandlers = { accountsChanged, chainChanged, disconnect };
    if (typeof provider.on === "function") {
      for (const [event, handler] of Object.entries(this.metamaskHandlers)) provider.on(event, handler);
    }
  }

  async refreshMetaMask(provider, accountOverride = null) {
    try {
      const accounts = accountOverride ? [accountOverride] : await provider.request({ method: "eth_accounts" });
      if (!Array.isArray(accounts) || !accounts.length || !ADDRESS.test(String(accounts[0]))) return this.disconnect("metamask");
      const chainId = normalizeChainId(await provider.request({ method: "eth_chainId" }));
      const address = String(accounts[0]);
      const balanceWei = await provider.request({ method: "eth_getBalance", params: [address, "latest"] });
      if (typeof balanceWei !== "string" || !/^0x[0-9a-fA-F]+$/.test(balanceWei)) throw new Error("Invalid MetaMask native balance");
      this.sessions.set("metamask", {
        providerId: "metamask", name: "MetaMask", connected: true,
        addresses: { [chainId]: address }, balances: { [chainId]: weiToNative(balanceWei) },
      });
      this.lastError = "";
    } catch (error) {
      this.disconnect("metamask");
      this.lastError = "MetaMask network or account could not be validated";
    }
    this.pruneSelections();
    this.notify();
  }

  disconnect(providerId) {
    if (providerId !== "metamask" && providerId !== "binance_agentic" && providerId !== "phantom") throw new Error("Unknown wallet provider");
    this.sessions.delete(providerId);
    this.clearSelections(providerId);
    if (providerId === "metamask" && this.metamaskProvider && this.metamaskHandlers
      && typeof this.metamaskProvider.removeListener === "function") {
      for (const [event, handler] of Object.entries(this.metamaskHandlers)) this.metamaskProvider.removeListener(event, handler);
      this.metamaskProvider = null;
      this.metamaskHandlers = null;
    }
    if (providerId === "phantom" && this.phantomProvider && this.phantomHandlers
      && typeof this.phantomProvider.removeListener === "function") {
      for (const [event, handler] of Object.entries(this.phantomHandlers)) this.phantomProvider.removeListener(event, handler);
      this.phantomProvider = null;
      this.phantomHandlers = null;
    }
    this.notify();
  }

  providersForChain(chainValue) {
    const chainId = normalizeWalletNetworkId(chainValue);
    const options = [];
    for (const session of this.sessions.values()) {
      const address = session.addresses[chainId];
      if (session.connected && address) options.push({ providerId: session.providerId, name: session.name, address, balance: session.balances[chainId] ?? null });
    }
    return options;
  }

  selectOpportunity(opportunityId, providerId, chainValue) {
    if (typeof opportunityId !== "string" || !opportunityId.trim()) throw new Error("Invalid opportunity ID");
    const chainId = normalizeWalletNetworkId(chainValue);
    const session = this.sessions.get(providerId);
    const address = session?.connected ? session.addresses[chainId] : null;
    if (!address) throw new Error("Selected wallet is not connected on this opportunity's network");
    const selection = { opportunityId, providerId, chainId, address };
    this.selections.set(opportunityId, selection);
    this.notify();
    return { ...selection };
  }

  async simulateMetaMaskPreview(opportunityId, prepared) {
    const selection = this.getSelection(opportunityId);
    const transaction = prepared?.transaction;
    if (!selection || selection.providerId !== "metamask" || prepared?.provider_id !== "metamask") {
      throw new Error("This opportunity is not explicitly selected for MetaMask");
    }
    if (!transaction || prepared.chain_id !== selection.chainId
      || transaction.from_address?.toLowerCase() !== selection.address.toLowerCase()
      || prepared.wallet_address?.toLowerCase() !== selection.address.toLowerCase()) {
      throw new Error("Prepared transaction does not match the selected wallet and network");
    }
    const chainId = normalizeChainId(prepared.chain_id);
    if (transaction.value_wei !== "0" || !ADDRESS.test(transaction.from_address)
      || !ADDRESS.test(transaction.contract_address)
      || typeof transaction.calldata !== "string" || !/^0x[0-9a-fA-F]+$/.test(transaction.calldata)) {
      throw new Error("Prepared transaction is invalid or not zero value");
    }
    const digest = await evmTransactionDigest(
      chainId, transaction.from_address, transaction.contract_address,
      transaction.value_wei, transaction.calldata,
    );
    if (digest !== prepared.transaction_digest) throw new Error("Prepared transaction digest does not match its parameters");
    const provider = this.metamaskProvider;
    if (!provider) throw new Error("Selected MetaMask provider is disconnected");

    const assertCurrentSelection = async () => {
      const [accounts, activeChain] = await Promise.all([
        provider.request({ method: "eth_accounts" }), provider.request({ method: "eth_chainId" }),
      ]);
      if (normalizeChainId(activeChain) !== chainId || !Array.isArray(accounts)
        || !accounts.some((account) => String(account).toLowerCase() === selection.address.toLowerCase())) {
        throw new Error("MetaMask account or network changed; select the wallet again");
      }
    };
    await assertCurrentSelection();
    const call = {
      from: transaction.from_address,
      to: transaction.contract_address,
      value: "0x0",
      data: transaction.calldata,
    };
    try {
      await provider.request({ method: "eth_call", params: [call, "latest"] });
    } catch (_) {
      return {
        provider_id: "metamask", wallet_address: selection.address, chain_id: chainId,
        transaction_digest: digest, status: "SIMULATION_FAILED", eligibility_status: "unknown",
        mint_price_status: prepared.mint_price_status, mint_price_wei: prepared.mint_price_wei,
        native_gas_token: SUPPORTED_EVM_CHAINS[chainId].nativeToken,
        error: "Read-only contract call failed for the selected wallet; eligibility remains unknown.",
      };
    }
    await assertCurrentSelection();
    let estimatedGas;
    let gasPrice;
    try {
      [estimatedGas, gasPrice] = await Promise.all([
        provider.request({ method: "eth_estimateGas", params: [call] }),
        provider.request({ method: "eth_gasPrice" }),
      ]);
      if (typeof estimatedGas !== "string" || !/^0x[0-9a-fA-F]+$/.test(estimatedGas)
        || typeof gasPrice !== "string" || !/^0x[0-9a-fA-F]+$/.test(gasPrice)) throw new Error("invalid estimate");
    } catch (_) {
      return {
        provider_id: "metamask", wallet_address: selection.address, chain_id: chainId,
        transaction_digest: digest, status: "SIMULATION_PASSED_GAS_UNAVAILABLE", eligibility_status: "eligible",
        mint_price_status: prepared.mint_price_status, mint_price_wei: prepared.mint_price_wei,
        native_gas_token: SUPPORTED_EVM_CHAINS[chainId].nativeToken,
        error: "The read-only contract call passed, but this provider could not estimate gas.",
      };
    }
    await assertCurrentSelection();
    const gasLimitBig = BigInt(estimatedGas);
    const gasPriceBig = BigInt(gasPrice);
    const fee = gasLimitBig * gasPriceBig;
    return {
      provider_id: "metamask", wallet_address: selection.address, chain_id: chainId,
      transaction_digest: digest, status: "SIMULATION_PASSED", eligibility_status: "eligible",
      mint_price_status: prepared.mint_price_status, mint_price_wei: prepared.mint_price_wei,
      native_gas_token: SUPPORTED_EVM_CHAINS[chainId].nativeToken,
      estimated_gas_limit: gasLimitBig.toString(), gas_price_wei: gasPriceBig.toString(),
      network_fee_wei: fee.toString(), network_fee_native: weiToNative(`0x${fee.toString(16)}`),
    };
  }

  getSelection(opportunityId) {
    const selection = this.selections.get(opportunityId);
    return selection ? { ...selection } : null;
  }

  clearSelection(opportunityId) {
    this.selections.delete(opportunityId);
    this.notify();
  }

  clearSelections(providerId) {
    for (const [key, selection] of this.selections) if (selection.providerId === providerId) this.selections.delete(key);
  }

  pruneSelections() {
    for (const [key, selection] of this.selections) {
      const address = this.sessions.get(selection.providerId)?.addresses[selection.chainId];
      if (!address || address.toLowerCase() !== selection.address.toLowerCase()) this.selections.delete(key);
    }
  }
}

export function weiToNative(value) {
  const wei = BigInt(value);
  const whole = wei / 10n ** 18n;
  const fraction = (wei % 10n ** 18n).toString().padStart(18, "0").replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole.toString();
}

function withTimeout(promise, timeoutMs) {
  let timer;
  return Promise.race([
    Promise.resolve(promise),
    new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("Wallet request timed out")), timeoutMs); }),
  ]).finally(() => clearTimeout(timer));
}

function publicKeyString(value) {
  const address = typeof value === "string" ? value : value?.toBase58?.();
  if (typeof address !== "string" || !/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(address)) throw new Error("invalid public key");
  const alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  let number = 0n;
  for (const character of address) {
    const digit = alphabet.indexOf(character);
    if (digit < 0) throw new Error("invalid public key");
    number = number * 58n + BigInt(digit);
  }
  let bytes = 0;
  while (number > 0n) { bytes += 1; number >>= 8n; }
  bytes += [...address].findIndex((character) => character !== "1") < 0 ? address.length
    : [...address].findIndex((character) => character !== "1");
  if (bytes !== 32) throw new Error("invalid public key");
  return address;
}

async function defaultSolanaBalanceReader(publicKey) {
  const response = await fetch(`/api/wallets/solana/balance?public_key=${encodeURIComponent(publicKey)}`, { cache: "no-store" });
  if (!response.ok) throw new Error("Solana balance unavailable");
  return response.json();
}

export async function evmTransactionDigest(chainId, fromAddress, contractAddress, valueWei, calldata) {
  const canonical = JSON.stringify({
    calldata: String(calldata).toLowerCase(),
    chain_id: String(chainId),
    from: String(fromAddress).toLowerCase(),
    to: String(contractAddress).toLowerCase(),
    value_wei: String(valueWei),
  });
  const digest = await globalThis.crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function shortAddress(address) {
  return ADDRESS.test(address) ? maskAddress(address) : "Invalid address";
}

export function shortWalletAddress(address) {
  if (ADDRESS.test(String(address))) return maskAddress(String(address));
  return /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(String(address))
    ? `${address.slice(0, 6)}…${address.slice(-4)}` : "Invalid address";
}
