import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';
import { mplCore } from '@metaplex-foundation/mpl-core';
import { VersionedTransaction } from '@solana/web3.js';
import { toWeb3JsTransaction } from '@metaplex-foundation/umi-web3js-adapters';
import {
  buildCoreCreateBuilder, calculateCostBreakdown, inspectCoreCreateBuilder,
  simulationFailure, validatePreviewInput,
} from '../app/solana/core_asset_preview.mjs';

const validInput = {
  walletAddress: '11111111111111111111111111111111',
  metadataUri: 'https://assets.example.org/nft-hunter-test-001.json',
  rpcUrl: 'https://api.mainnet-beta.solana.com',
  metadata: {
    name: 'NFT Hunter Test #001', symbol: 'HUNTER',
    description: 'First Solana NFT created by NFT Hunter', image: 'nft-hunter-test-001.svg',
  },
};

test('metadata input validates exact test metadata and public HTTPS URI', () => {
  assert.equal(validatePreviewInput(validInput).metadataUri, validInput.metadataUri);
  assert.throws(() => validatePreviewInput({ ...validInput, metadataUri: 'http://assets.example.org/a.json' }), /HTTPS/);
  assert.throws(() => validatePreviewInput({ ...validInput, metadataUri: 'https://assets.example.org/a.json?token=secret' }), /query/);
  assert.throws(() => validatePreviewInput({ ...validInput, metadata: { ...validInput.metadata, name: 'not the test name' } }), /Metadata JSON/);
  assert.throws(() => validatePreviewInput({ ...validInput, maxTotalLamports: 0 }), /limit/);
});

test('official Core builder includes payer, owner, asset signer and only Core instruction', () => {
  const umi = createUmi(validInput.rpcUrl).use(mplCore());
  const { builder, assetSigner } = buildCoreCreateBuilder(umi, validInput.walletAddress, validInput.metadataUri);
  const inspected = inspectCoreCreateBuilder(builder, validInput.walletAddress, assetSigner.publicKey);
  assert.equal(inspected.programId, 'CoREENxT6tW1HoK8ypY1SxRMZTcVPm7R94rH4PZNhX7d');
  assert.equal(inspected.instructionCount, 1);
  assert.deepEqual(inspected.requiredSigners.sort(), [validInput.walletAddress, assetSigner.publicKey].sort());
  assert.ok(inspected.accountMetas.some((meta) => meta.address === validInput.walletAddress && meta.isSigner && meta.isWritable));
  assert.ok(inspected.accountMetas.some((meta) => meta.address === validInput.walletAddress && !meta.isSigner));
  assert.ok(inspected.accountMetas.some((meta) => meta.address === assetSigner.publicKey && meta.isSigner && meta.isWritable));
  const transaction = builder.setBlockhash('11111111111111111111111111111111').build(umi);
  assert.equal(transaction.message.header.numRequiredSignatures, 2);
  assert.equal(transaction.signatures.filter((signature) => signature.every((byte) => byte === 0)).length, 2);
});

test('Umi preview serialization round-trips through the version-aware wallet transaction decoder', () => {
  const umi = createUmi(validInput.rpcUrl).use(mplCore());
  const { builder } = buildCoreCreateBuilder(umi, validInput.walletAddress, validInput.metadataUri);
  const umiTransaction = builder.setBlockhash('11111111111111111111111111111111').build(umi);
  const web3Transaction = toWeb3JsTransaction(umiTransaction);
  const serialized = Buffer.from(umi.transactions.serialize(umiTransaction));
  const decoded = VersionedTransaction.deserialize(serialized);
  assert.equal(decoded.version, web3Transaction.version);
  assert.equal(decoded.message.serialize().toString('hex'), web3Transaction.message.serialize().toString('hex'));
  assert.equal(decoded.message.header.numRequiredSignatures, 2);
  assert.equal(decoded.signatures.every((signature) => signature.every((byte) => byte === 0)), true);
});

test('cost estimate separates fee, rent and Core protocol fee, and enforces lamport cap', () => {
  const costs = calculateCostBreakdown(5000n, 1000000n, 2500000n, 3000000n);
  assert.equal(costs.protocolFee, 1500000n);
  assert.equal(costs.total, 2505000n);
  assert.equal(costs.withinLimit, true);
  assert.equal(calculateCostBreakdown(5000n, 1000000n, 2500000n, 2500000n).withinLimit, false);
  assert.throws(() => calculateCostBreakdown(1n, 10n, 9n, 100n), /rent-exempt/);
});

test('simulation error remains blocked', () => {
  assert.equal(simulationFailure({ err: null, logs: [] }), null);
  assert.deepEqual(simulationFailure({ err: { InstructionError: [0, 'Custom'] }, logs: ['log'] }), {
    error: { InstructionError: [0, 'Custom'] }, logs: ['log'],
  });
});

test('preview source has no transaction submission or broadcast call', async () => {
  const source = await readFile(new URL('../app/solana/core_asset_preview.mjs', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\.sendTransaction\s*\(|\.sendAndConfirm\s*\(|\.send\s*\(/);
  assert.match(source, /simulateTransaction\s*\(/);
});
