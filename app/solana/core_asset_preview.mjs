import { create, mplCore, MPL_CORE_PROGRAM_ID } from '@metaplex-foundation/mpl-core';
import {
  createNoopSigner, generateSigner, publicKey, signerIdentity,
} from '@metaplex-foundation/umi';
import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';
import { toWeb3JsTransaction } from '@metaplex-foundation/umi-web3js-adapters';
import { Connection, PublicKey as Web3PublicKey } from '@solana/web3.js';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

export const MAINNET_GENESIS = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d';
export const DEFAULT_MAX_TOTAL_LAMPORTS = 5_000_000;
export const SYSTEM_PROGRAM_ID = '11111111111111111111111111111111';

export function validatePreviewInput(input) {
  if (!input || typeof input !== 'object') throw new Error('Preview input must be an object');
  if (!input.metadata || input.metadata.name !== 'NFT Hunter Test #001'
    || input.metadata.symbol !== 'HUNTER'
    || input.metadata.description !== 'First Solana NFT created by NFT Hunter'
    || typeof input.metadata.image !== 'string') {
    throw new Error('Metadata JSON does not match the expected NFT Hunter test asset');
  }
  const wallet = new Web3PublicKey(input.walletAddress);
  if (wallet.toBase58() !== input.walletAddress) throw new Error('Wallet address is not canonical base58');
  const uri = new URL(input.metadataUri);
  if (uri.protocol !== 'https:' || uri.username || uri.password || uri.search || uri.hash) {
    throw new Error('Metadata URI must be a public HTTPS URL without credentials, query, or fragment');
  }
  if (!input.rpcUrl || new URL(input.rpcUrl).protocol !== 'https:') {
    throw new Error('A configured HTTPS Solana RPC endpoint is required');
  }
  const maxTotalLamports = Number(input.maxTotalLamports ?? DEFAULT_MAX_TOTAL_LAMPORTS);
  if (!Number.isSafeInteger(maxTotalLamports) || maxTotalLamports <= 0) {
    throw new Error('Spending limit must be a positive whole number of lamports');
  }
  return { walletAddress: wallet.toBase58(), metadataUri: uri.toString(), maxTotalLamports };
}

export function buildCoreCreateBuilder(umi, walletAddress, metadataUri, assetSigner = generateSigner(umi)) {
  const payer = createNoopSigner(publicKey(walletAddress));
  const owner = publicKey(walletAddress);
  umi.use(signerIdentity(payer));
  const builder = create(umi, { asset: assetSigner, name: 'NFT Hunter Test #001', uri: metadataUri, owner });
  return { builder, assetSigner, payer, owner };
}

export function inspectCoreCreateBuilder(builder, walletAddress, assetAddress) {
  const instructions = builder.getInstructions();
  if (instructions.length !== 1) throw new Error('Unexpected instruction count for Core asset creation');
  const instruction = instructions[0];
  if (instruction.programId !== MPL_CORE_PROGRAM_ID) throw new Error('Unexpected program ID in Core asset creation');
  const metas = instruction.keys.map((meta) => ({
    address: meta.pubkey,
    isSigner: Boolean(meta.isSigner),
    isWritable: Boolean(meta.isWritable),
  }));
  const expected = [
    { address: assetAddress, isSigner: true, isWritable: true },
    { address: MPL_CORE_PROGRAM_ID, isSigner: false, isWritable: false },
    { address: MPL_CORE_PROGRAM_ID, isSigner: false, isWritable: false },
    { address: walletAddress, isSigner: true, isWritable: true },
    { address: walletAddress, isSigner: false, isWritable: false },
    { address: MPL_CORE_PROGRAM_ID, isSigner: false, isWritable: false },
    { address: SYSTEM_PROGRAM_ID, isSigner: false, isWritable: false },
    { address: MPL_CORE_PROGRAM_ID, isSigner: false, isWritable: false },
  ];
  if (JSON.stringify(metas) !== JSON.stringify(expected)) {
    throw new Error('Core instruction account metas differ from the pinned SDK layout');
  }
  const signerAddresses = [...new Set(builder.getSigners({ payer: createNoopSigner(publicKey(walletAddress)) })
    .map((signer) => signer.publicKey))].sort();
  if (signerAddresses.length !== 2 || signerAddresses[0] !== [walletAddress, assetAddress].sort()[0]
    || signerAddresses[1] !== [walletAddress, assetAddress].sort()[1]) {
    throw new Error('The Core instruction has unexpected additional signers');
  }
  return {
    programId: instruction.programId,
    instructionCount: instructions.length,
    accountMetas: metas,
    requiredSigners: [walletAddress, assetAddress],
    assetSignerAddress: assetAddress,
    payerAddress: walletAddress,
  };
}

export function calculateCostBreakdown(feeLamports, rentExemptLamports, assetAccountLamports, maxTotalLamports) {
  const fee = BigInt(feeLamports);
  const rent = BigInt(rentExemptLamports);
  const assetBalance = BigInt(assetAccountLamports);
  const max = BigInt(maxTotalLamports);
  if (assetBalance < rent) throw new Error('Asset account is below rent-exempt minimum');
  const protocolFee = assetBalance - rent;
  const total = fee + rent + protocolFee;
  return { fee, rent, protocolFee, total, max, withinLimit: total <= max };
}

export function simulationFailure(simulation) {
  return simulation?.err == null ? null : {
    error: simulation.err,
    logs: (simulation.logs ?? []).slice(-12),
  };
}

export async function createCoreAssetPreview(input, dependencies = {}) {
  const valid = validatePreviewInput(input);
  const UmiFactory = dependencies.createUmi ?? createUmi;
  const ConnectionFactory = dependencies.Connection ?? Connection;
  const umi = UmiFactory(input.rpcUrl).use(mplCore());
  const connection = new ConnectionFactory(input.rpcUrl, 'confirmed');
  const genesis = await connection.getGenesisHash();
  if (genesis !== MAINNET_GENESIS) throw new Error('RPC endpoint is not Solana mainnet-beta');

  const { builder, assetSigner } = buildCoreCreateBuilder(umi, valid.walletAddress, valid.metadataUri);
  const instructionSummary = inspectCoreCreateBuilder(builder, valid.walletAddress, assetSigner.publicKey);
  const blockhash = await umi.rpc.getLatestBlockhash({ commitment: 'confirmed' });
  const transaction = builder.setBlockhash(blockhash).build(umi);
  const unsignedTransaction = transaction;
  if (unsignedTransaction.message.header.numRequiredSignatures !== 2
    || unsignedTransaction.signatures.length !== 2
    || unsignedTransaction.signatures.some((signature) => signature.some((byte) => byte !== 0))) {
    throw new Error('Unsigned Core transaction must require exactly Phantom payer and asset signatures');
  }
  const web3Transaction = toWeb3JsTransaction(unsignedTransaction);

  const feeResult = await connection.getFeeForMessage(web3Transaction.message, 'confirmed');
  if (feeResult.value == null) throw new Error('RPC could not estimate the transaction fee');
  const feeLamports = BigInt(feeResult.value);
  const payerBalanceLamports = BigInt(await connection.getBalance(
    new Web3PublicKey(valid.walletAddress), 'confirmed',
  ));
  const simulation = await umi.rpc.simulateTransaction(unsignedTransaction, {
    commitment: 'confirmed', verifySignatures: false, replaceRecentBlockhash: false,
    accounts: [publicKey(valid.walletAddress), publicKey(assetSigner.publicKey)],
  });
  const simulationError = simulationFailure(simulation);
  if (simulationError) {
    return {
      status: 'BLOCKED_SIMULATION', reason: 'Mainnet simulation returned an error',
      simulation: simulationError,
    };
  }
  const accountInfos = simulation.accounts ?? [];
  const payerAccount = accountInfos[0];
  const assetAccount = accountInfos[1];
  if (!payerAccount || !assetAccount) throw new Error('RPC did not return simulated payer and asset account states');
  const assetLamports = BigInt(assetAccount.lamports);
  const rentExemptLamports = BigInt(await connection.getMinimumBalanceForRentExemption(
    Buffer.from(assetAccount.data[0], assetAccount.data[1] || 'base64').length,
    'confirmed',
  ));
  const costs = calculateCostBreakdown(feeLamports, rentExemptLamports, assetLamports, valid.maxTotalLamports);
  const protocolFeeLamports = costs.protocolFee;
  const totalLamports = costs.total;
  if (payerBalanceLamports < totalLamports) {
    return { status: 'BLOCKED_SIMULATION', reason: 'Connected Phantom balance is below the estimated preview cost.',
      costs: { total_lamports: totalLamports.toString(), available_lamports: payerBalanceLamports.toString() } };
  }
  if (!costs.withinLimit) {
    return {
      status: 'BLOCKED_SIMULATION', reason: 'Estimated total exceeds the configured SOL preview limit',
      costs: { fee_lamports: feeLamports.toString(), rent_lamports: rentExemptLamports.toString(),
        protocol_fee_lamports: protocolFeeLamports.toString(), total_lamports: totalLamports.toString(),
        max_total_lamports: String(valid.maxTotalLamports) },
    };
  }
  const serialized = umi.transactions.serialize(unsignedTransaction);
  return {
    status: 'PREVIEW_READY',
    network: 'solana-mainnet-beta',
    wallet_address: valid.walletAddress,
    asset_address: assetSigner.publicKey,
    metadata_uri: valid.metadataUri,
    transaction: {
      version: web3Transaction.version === 'legacy' ? 'legacy' : `v${web3Transaction.version}`,
      recent_blockhash: unsignedTransaction.message.blockhash,
      last_valid_block_height: blockhash.lastValidBlockHeight,
      message_sha256: createHash('sha256').update(web3Transaction.message.serialize()).digest('hex'),
      fee_payer: valid.walletAddress,
      unsigned_transaction_base64: Buffer.from(serialized).toString('base64'),
      phantom_signature_required: true,
      asset_signer_signature_required: true,
      instruction: instructionSummary,
    },
    costs: {
      mint_price_lamports: '0', fee_lamports: feeLamports.toString(),
      rent_lamports: rentExemptLamports.toString(), protocol_fee_lamports: protocolFeeLamports.toString(),
      priority_fee_lamports: '0', total_lamports: totalLamports.toString(),
      maximum_lamports: String(valid.maxTotalLamports),
      available_lamports: payerBalanceLamports.toString(),
    },
    simulation: { status: 'PASS', units_consumed: simulation.unitsConsumed ?? null, error: null },
    safety: { broadcast_attempted: false, wallet_signature_requested: false,
      phantom_private_key_requested: false, asset_signer_secret_written_to_disk: false },
    ...(dependencies.includeInternalSigner === true
      ? { internal_asset_signer_secret_key: Buffer.from(assetSigner.secretKey).toString('base64') } : {}),
  };
}

async function main() {
  if (process.env.NFT_HUNTER_CORE_PREVIEW_BACKEND_CHILD !== '1') {
    process.stdout.write(JSON.stringify({ status: 'BLOCKED_WALLET_NOT_CONFIRMED',
      reason: 'Start the local dashboard and confirm the connected Phantom wallet there.' }));
    return;
  }
  let input;
  try { input = JSON.parse(await new Promise((resolve, reject) => {
    let body = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { body += chunk; if (body.length > 128 * 1024) reject(new Error('Input too large')); });
    process.stdin.on('end', () => resolve(body));
    process.stdin.on('error', reject);
  })); } catch { process.stdout.write(JSON.stringify({ status: 'BLOCKED_METADATA_URI', reason: 'Invalid preview request' })); return; }
  try {
    const result = await createCoreAssetPreview(input, {
      includeInternalSigner: process.env.NFT_HUNTER_CORE_SIGNER_BRIDGE === '1',
    });
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    const message = String(error?.message || 'Preview preparation failed');
    const status = /https|metadata|public URL/i.test(message) ? 'BLOCKED_METADATA_URI'
      : /mainnet|rpc|blockhash|fee estimate|simulated .* account|rent-exempt|failed to fetch|timed out/i.test(message)
        ? 'BLOCKED_RPC' : 'BLOCKED_SIMULATION';
    const reason = status === 'BLOCKED_METADATA_URI' ? 'Metadata URI validation failed.'
      : status === 'BLOCKED_RPC' ? 'Solana mainnet RPC request failed or returned incomplete fee/rent data.'
        : 'Core transaction construction or validation failed.';
    process.stdout.write(JSON.stringify({ status, reason }));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
