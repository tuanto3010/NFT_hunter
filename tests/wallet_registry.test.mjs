import test from "node:test";
import assert from "node:assert/strict";
import { evmTransactionDigest, WalletRegistry } from "../app/dashboard/static/wallet_registry.js";

const metaAddress = `0x${"1".repeat(40)}`;
const binanceAddress = `0x${"2".repeat(40)}`;
const otherAddress = `0x${"3".repeat(40)}`;
const solanaAddress = "1".repeat(32);
const otherSolanaAddress = `${"1".repeat(31)}2`;

function fakeMetaMask({ chainId = "0x38", address = metaAddress, balance = "0x16345785d8a0000", failCall = false } = {}) {
  return {
    isMetaMask: true,
    handlers: new Map(),
    calls: [],
    async request({ method }) {
      this.calls.push(method);
      if (method === "eth_requestAccounts" || method === "eth_accounts") return [address];
      if (method === "eth_chainId") return chainId;
      if (method === "eth_getBalance") return balance;
      if (method === "eth_call") {
        if (failCall) throw new Error("execution reverted");
        return "0x";
      }
      if (method === "eth_estimateGas") return "0x5208";
      if (method === "eth_gasPrice") return "0x3b9aca00";
      throw new Error(`unexpected method ${method}`);
    },
    on(event, callback) { this.handlers.set(event, callback); },
    removeListener(event) { this.handlers.delete(event); },
  };
}

async function preparedMetaMaskPreview(address = metaAddress) {
  const transaction = {
    from_address: address,
    contract_address: `0x${"b".repeat(40)}`,
    calldata: `0x${"a0712d68"}${"0".repeat(64)}`,
    value_wei: "0",
  };
  return {
    provider_id: "metamask", wallet_address: address, chain_id: "56",
    transaction_digest: await evmTransactionDigest("56", address, transaction.contract_address,
      transaction.value_wei, transaction.calldata),
    mint_price_status: "VERIFIED_ZERO", mint_price_wei: "0", transaction,
  };
}

function binanceSnapshot() {
  return {
    provider_id: "binance_agentic", status: "connected",
    networks: [{ chain_id: "56", family: "evm" }, { chain_id: "CT_501", family: "solana" }],
    addresses: [{ chain_id: "56", address: binanceAddress }, { chain_id: "CT_501", address: solanaAddress }],
    balances: [{ chain_id: "56", symbol: "BNB", amount: "0.5" }, { chain_id: "CT_501", symbol: "SOL", amount: "7" }],
  };
}

function fakePhantom({ address = solanaAddress } = {}) {
  return {
    isPhantom: true,
    handlers: new Map(),
    disconnectCalls: 0,
    async connect() { return { publicKey: { toBase58: () => address } }; },
    async disconnect() { this.disconnectCalls += 1; },
    on(event, callback) { this.handlers.set(event, callback); },
    removeListener(event) { this.handlers.delete(event); },
  };
}

function registryWithSolanaReader() {
  return new WalletRegistry({ solanaBalanceReader: async (address) => ({
    provider_id: "phantom", public_key: address, chain_id: "solana-mainnet-beta",
    balance_sol: address === otherSolanaAddress ? "3.5" : "2.25",
  }) });
}

test("MetaMask and Binance connect independently and coexist", async () => {
  const registry = new WalletRegistry();
  registry.setBinanceSnapshot(binanceSnapshot());
  await registry.connectMetaMask(fakeMetaMask());
  assert.equal(registry.getSession("metamask").connected, true);
  assert.equal(registry.getSession("binance_agentic").connected, true);
  assert.equal(registry.providersForChain("56").length, 2);
  assert.equal(registry.providersForChain("solana-mainnet-beta")[0].providerId, "binance_agentic");
  assert.equal(registry.providersForChain("solana-mainnet-beta")[0].balance, "7");
  assert.equal(typeof registry.signTransaction, "undefined");
  assert.equal(typeof registry.broadcastTransaction, "undefined");
});

test("MetaMask provider resolution prefers its EIP-6963 announcement over another window.ethereum wallet", async () => {
  const registry = new WalletRegistry({ solanaBalanceReader: async () => ({}) });
  const metamask = fakeMetaMask();
  registry.registerEip6963Provider({
    info: { uuid: "metamask-id", name: "MetaMask", rdns: "io.metamask" }, provider: metamask,
  });
  const previousEthereum = globalThis.ethereum;
  globalThis.ethereum = { isMetaMask: false, request: async () => { throw new Error("wrong provider"); } };
  try {
    const selected = await registry.resolveMetaMaskProvider();
    assert.equal(selected, metamask);
  } finally {
    if (previousEthereum === undefined) delete globalThis.ethereum;
    else globalThis.ethereum = previousEthereum;
  }
});

test("disconnecting one provider preserves the other provider session", async () => {
  const registry = new WalletRegistry();
  registry.setBinanceSnapshot(binanceSnapshot());
  await registry.connectMetaMask(fakeMetaMask());
  registry.disconnect("metamask");
  assert.equal(registry.getSession("metamask"), null);
  assert.equal(registry.getSession("binance_agentic").connected, true);
  assert.equal(registry.providersForChain("56").length, 1);
  registry.disconnect("binance_agentic");
  assert.equal(registry.getSession("binance_agentic"), null);
});

test("Phantom connects read-only, refreshes balance on account changes and disconnects independently", async () => {
  const registry = registryWithSolanaReader();
  const phantom = fakePhantom();
  await assert.rejects(registry.connectPhantom({}), /provider was not found/);
  registry.setBinanceSnapshot(binanceSnapshot());
  await registry.connectMetaMask(fakeMetaMask());
  await registry.connectPhantom(phantom);
  assert.equal(registry.getSession("phantom").addresses["solana-mainnet-beta"], solanaAddress);
  assert.equal(registry.getSession("phantom").balances["solana-mainnet-beta"], "2.25");
  assert.deepEqual(registry.providersForChain("solana-mainnet-beta").map((item) => item.providerId).sort(), ["binance_agentic", "phantom"]);
  phantom.handlers.get("accountChanged")({ toBase58: () => otherSolanaAddress });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(registry.getSession("phantom").addresses["solana-mainnet-beta"], otherSolanaAddress);
  assert.equal(registry.getSession("phantom").balances["solana-mainnet-beta"], "3.5");
  registry.selectOpportunity("sol-1", "phantom", "solana-mainnet-beta");
  await registry.disconnectPhantom();
  assert.equal(phantom.disconnectCalls, 1);
  assert.equal(registry.getSession("phantom"), null);
  assert.equal(registry.getSession("metamask").connected, true);
  assert.equal(registry.getSession("binance_agentic").connected, true);
  assert.equal(registry.getSelection("sol-1"), null);
});

test("Phantom cannot be selected when network or balance response is unverified", async () => {
  const registry = new WalletRegistry({ solanaBalanceReader: async (address) => ({
    public_key: address, chain_id: "1", balance_sol: "1",
  }) });
  await registry.connectPhantom(fakePhantom());
  assert.equal(registry.getSession("phantom").connected, true);
  assert.equal(registry.getSession("phantom").networkVerified, false);
  assert.equal(registry.providersForChain("solana-mainnet-beta").length, 0);
  assert.deepEqual(registry.providersForChain("4663"), []);
});

test("Phantom remains connected when balance RPC is offline, but is not selectable for mainnet", async () => {
  const registry = new WalletRegistry({ solanaBalanceReader: async () => { throw new Error("RPC offline"); } });
  await registry.connectPhantom(fakePhantom());
  const session = registry.getSession("phantom");
  assert.equal(session.connected, true);
  assert.equal(session.publicKey, solanaAddress);
  assert.equal(session.networkVerified, false);
  assert.equal(session.balances["solana-mainnet-beta"], undefined);
  assert.deepEqual(registry.providersForChain("solana-mainnet-beta"), []);
});

test("Solana opportunity can select Phantom only with its independent Solana address", async () => {
  const registry = registryWithSolanaReader();
  registry.setBinanceSnapshot(binanceSnapshot());
  await registry.connectMetaMask(fakeMetaMask());
  await registry.connectPhantom(fakePhantom());
  const selected = registry.selectOpportunity("sol-2", "phantom", "solana-mainnet-beta");
  assert.equal(selected.address, solanaAddress);
  assert.equal(selected.chainId, "solana-mainnet-beta");
  assert.throws(() => registry.selectOpportunity("sol-3", "metamask", "solana-mainnet-beta"), /not connected/);
  assert.throws(() => registry.selectOpportunity("evm-1", "phantom", "56"), /not connected/);
});

test("opportunity selection binds provider, address and chain", async () => {
  const registry = new WalletRegistry();
  registry.setBinanceSnapshot(binanceSnapshot());
  await registry.connectMetaMask(fakeMetaMask());
  const selected = registry.selectOpportunity("42", "metamask", "56");
  assert.deepEqual(selected, {
    opportunityId: "42", providerId: "metamask", chainId: "56", address: metaAddress,
  });
  assert.equal(registry.getSelection("42").address, metaAddress);
  assert.equal(registry.selectOpportunity("43", "binance_agentic", 56).address, binanceAddress);
  assert.equal(registry.selectOpportunity("sol", "binance_agentic", "solana-mainnet-beta").address, solanaAddress);
  assert.throws(() => registry.selectOpportunity("44", "metamask", "1"), /not connected/);
  registry.disconnect("metamask");
  assert.equal(registry.getSelection("42"), null);
  assert.equal(registry.getSelection("43").providerId, "binance_agentic");
});

test("invalid addresses and unsupported EVM chains are rejected", async () => {
  const registry = new WalletRegistry();
  await assert.rejects(registry.connectMetaMask(fakeMetaMask({ chainId: "0x9999" })), /could not be validated/);
  assert.equal(registry.getSession("metamask"), null);
  registry.setBinanceSnapshot({
    provider_id: "binance_agentic", status: "connected",
    networks: [{ chain_id: "56", family: "evm" }],
    addresses: [{ chain_id: "56", address: "not-an-address" }], balances: [],
  });
  assert.deepEqual(registry.providersForChain("56"), []);
  assert.deepEqual(registry.providersForChain("4663"), []);
  assert.throws(() => registry.providersForChain("9999"), /unsupported/i);
});

test("MetaMask read-only simulation uses the selected address and binds gas result", async () => {
  const registry = new WalletRegistry();
  const provider = fakeMetaMask();
  await registry.connectMetaMask(provider);
  registry.selectOpportunity("preview-1", "metamask", "56");
  const prepared = await preparedMetaMaskPreview();
  const result = await registry.simulateMetaMaskPreview("preview-1", prepared);
  assert.equal(result.status, "SIMULATION_PASSED");
  assert.equal(result.eligibility_status, "eligible");
  assert.equal(result.wallet_address, metaAddress);
  assert.equal(result.transaction_digest, prepared.transaction_digest);
  assert.equal(result.network_fee_wei, String(21000n * 1_000_000_000n));
  assert.ok(provider.calls.includes("eth_call"));
  assert.ok(provider.calls.includes("eth_estimateGas"));
  assert.ok(provider.calls.includes("eth_gasPrice"));
  assert.ok(!provider.calls.some((method) => /sign|sendTransaction|broadcast/i.test(method)));
});

test("MetaMask simulation failure leaves selected-wallet eligibility unknown", async () => {
  const registry = new WalletRegistry();
  await registry.connectMetaMask(fakeMetaMask({ failCall: true }));
  registry.selectOpportunity("preview-2", "metamask", "56");
  const result = await registry.simulateMetaMaskPreview("preview-2", await preparedMetaMaskPreview());
  assert.equal(result.status, "SIMULATION_FAILED");
  assert.equal(result.eligibility_status, "unknown");
});

test("MetaMask preview rejects a selected-provider or address mismatch without fallback", async () => {
  const registry = new WalletRegistry();
  registry.setBinanceSnapshot(binanceSnapshot());
  await registry.connectMetaMask(fakeMetaMask());
  registry.selectOpportunity("preview-3", "binance_agentic", "56");
  await assert.rejects(registry.simulateMetaMaskPreview("preview-3", await preparedMetaMaskPreview()), /not explicitly selected/);
  registry.selectOpportunity("preview-3", "metamask", "56");
  await assert.rejects(registry.simulateMetaMaskPreview("preview-3", await preparedMetaMaskPreview(otherAddress)), /does not match/);
});

test("MetaMask preview rejects chain changes after selection", async () => {
  const registry = new WalletRegistry();
  const provider = fakeMetaMask();
  await registry.connectMetaMask(provider);
  registry.selectOpportunity("preview-4", "metamask", "56");
  provider.request = async ({ method }) => {
    if (method === "eth_accounts") return [metaAddress];
    if (method === "eth_chainId") return "0x1";
    throw new Error("should not simulate on changed chain");
  };
  await assert.rejects(registry.simulateMetaMaskPreview("preview-4", await preparedMetaMaskPreview()), /changed/);
});
