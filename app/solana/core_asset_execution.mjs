import { createHash, createPublicKey, verify as verifyEd25519 } from 'node:crypto';
import { Connection, Keypair, PublicKey, VersionedTransaction } from '@solana/web3.js';
import { fetchAsset, mplCore, MPL_CORE_PROGRAM_ID } from '@metaplex-foundation/mpl-core';
import { publicKey } from '@metaplex-foundation/umi';
import { createUmi } from '@metaplex-foundation/umi-bundle-defaults';

const MAINNET_GENESIS = '5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d';

function parse(input) {
  if (!input || input.network !== 'solana-mainnet-beta' || input.walletAddress == null
    || input.assetAddress == null || input.maxTotalLamports == null) throw new Error('Invalid execution context');
  if (new URL(input.rpcUrl).protocol !== 'https:') throw new Error('RPC must use HTTPS');
  const tx = VersionedTransaction.deserialize(Buffer.from(input.transactionBase64, 'base64'));
  const message = tx.message;
  const signerKeys = message.staticAccountKeys.slice(0, message.header.numRequiredSignatures);
  if (message.staticAccountKeys[0]?.toBase58() !== input.walletAddress) throw new Error('Unexpected fee payer');
  if (!message.recentBlockhash || tx.signatures.length !== 2 || signerKeys.length !== 2) throw new Error('Unexpected transaction shape');
  const keys = signerKeys.map((key) => key.toBase58());
  if (!keys.includes(input.walletAddress) || !keys.includes(input.assetAddress)) throw new Error('Unexpected signer set');
  const keypair = Keypair.fromSecretKey(Buffer.from(input.assetSignerSecret, 'base64'));
  if (keypair.publicKey.toBase58() !== input.assetAddress) throw new Error('Asset signer does not match preview');
  return { tx, keypair, connection: new Connection(input.rpcUrl, 'confirmed') };
}

async function revalidate(input, tx, connection, requireSignatures) {
  if (await connection.getGenesisHash() !== MAINNET_GENESIS) throw new Error('RPC is not Solana mainnet-beta');
  const blockHeight = await connection.getBlockHeight('confirmed');
  if (!Number.isSafeInteger(input.lastValidBlockHeight) || blockHeight > input.lastValidBlockHeight
    || !(await connection.isBlockhashValid(tx.message.recentBlockhash, 'confirmed')).value) {
    throw new Error('Preview blockhash expired; create a fresh preview');
  }
  const fee = await connection.getFeeForMessage(tx.message, 'confirmed');
  if (fee.value == null) throw new Error('Current network fee is unavailable');
  const balance = BigInt(await connection.getBalance(new PublicKey(input.walletAddress), 'confirmed'));
  const simulation = await connection.simulateTransaction(tx, {
    commitment: 'confirmed', sigVerify: requireSignatures, replaceRecentBlockhash: false,
    accounts: { encoding: 'base64', addresses: [input.walletAddress, input.assetAddress] },
  });
  if (simulation.value.err != null) throw new Error('Preflight simulation failed');
  if (requireSignatures && !verifyRequiredSignatures(tx)) throw new Error('Required Phantom and asset signatures are not valid');
  const accounts = simulation.value.accounts;
  if (!accounts?.[1]?.data?.[0]) throw new Error('Simulation omitted the asset account state');
  const assetDataLength = Buffer.from(accounts[1].data[0], 'base64').length;
  const rent = BigInt(await connection.getMinimumBalanceForRentExemption(assetDataLength, 'confirmed'));
  const protocol = BigInt(accounts[1].lamports) - rent;
  if (protocol < 0n) throw new Error('Simulated asset balance is below rent minimum');
  const total = BigInt(fee.value) + rent + protocol;
  const cap = BigInt(input.maxTotalLamports);
  if (total > cap || balance < total) throw new Error('Current balance or fee exceeds the preview spending cap');
  return { fee: BigInt(fee.value), rent, protocol, total, balance, units: simulation.value.unitsConsumed ?? null };
}

function verifyRequiredSignatures(tx) {
  const message = tx.message.serialize();
  const signerKeys = tx.message.staticAccountKeys.slice(0, tx.message.header.numRequiredSignatures);
  if (signerKeys.length !== 2 || tx.signatures.length !== signerKeys.length) return false;
  const spkiPrefix = Buffer.from('302a300506032b6570032100', 'hex');
  return tx.signatures.every((signature, index) => {
    if (signature.length !== 64 || signature.every((byte) => byte === 0)) return false;
    try {
      const key = createPublicKey({ key: Buffer.concat([spkiPrefix, signerKeys[index].toBytes()]), format: 'der', type: 'spki' });
      return verifyEd25519(null, message, key, Buffer.from(signature));
    } catch { return false; }
  });
}

export async function preparePhantomSigning(input) {
  const { tx, keypair, connection } = parse(input);
  if (createHash('sha256').update(tx.message.serialize()).digest('hex') !== input.expectedMessageSha256) {
    throw new Error('Prepared transaction does not match the approved preview');
  }
  const costs = await revalidate(input, tx, connection, false);
  tx.sign([keypair]);
  return { transactionBase64: Buffer.from(tx.serialize()).toString('base64'),
    costs: Object.fromEntries(Object.entries(costs).map(([key, value]) => [key, String(value)])) };
}

export async function validatePhantomSigned(input) {
  const { tx: expected, keypair, connection } = parse(input);
  const signed = VersionedTransaction.deserialize(Buffer.from(input.signedTransactionBase64, 'base64'));
  const expectedMessage = expected.message.serialize();
  if (!Buffer.from(signed.message.serialize()).equals(Buffer.from(expectedMessage))) throw new Error('Signed transaction differs from the approved message');
  if (createHash('sha256').update(expectedMessage).digest('hex') !== input.expectedMessageSha256) {
    throw new Error('Approved transaction digest does not match');
  }
  const signerKeys = signed.message.staticAccountKeys.slice(0, signed.message.header.numRequiredSignatures).map((key) => key.toBase58());
  if (signerKeys.length !== 2 || !signerKeys.includes(input.walletAddress)
    || !signerKeys.includes(input.assetAddress) || !verifyRequiredSignatures(signed)) {
    throw new Error('Both valid Phantom and asset signatures are required');
  }
  const costs = await revalidate(input, signed, connection, true);
  const raw = signed.serialize();
  const feePayerSignature = signed.signatures[0];
  if (!feePayerSignature || feePayerSignature.every((byte) => byte === 0)) throw new Error('Phantom fee payer signature is missing');
  return { signedTransactionBase64: raw.toString('base64'),
    signature: Buffer.from(feePayerSignature).toString('base64'),
    costs: Object.fromEntries(Object.entries(costs).map(([key, value]) => [key, String(value)])) };
}

export async function verifyCoreAsset(input) {
  const connection = new Connection(input.rpcUrl, 'confirmed');
  if (await connection.getGenesisHash() !== MAINNET_GENESIS) throw new Error('RPC is not Solana mainnet-beta');
  const account = await connection.getAccountInfo(new PublicKey(input.assetAddress), 'confirmed');
  if (!account) throw new Error('ASSET_ACCOUNT_NOT_FOUND');
  if (account.owner.toBase58() !== MPL_CORE_PROGRAM_ID) throw new Error('WRONG_PROGRAM_OWNER');
  const umi = createUmi(input.rpcUrl).use(mplCore());
  const asset = await fetchAsset(umi, publicKey(input.assetAddress));
  return { exists: true, owner: asset.owner, metadataUri: asset.uri, name: asset.name };
}

async function main() {
  try {
    const chunks = [];
    for await (const chunk of process.stdin) chunks.push(chunk);
    const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    const output = input.operation === 'prepare' ? await preparePhantomSigning(input)
      : input.operation === 'validate' ? await validatePhantomSigned(input)
        : input.operation === 'verify_asset' ? await verifyCoreAsset(input) : null;
    if (!output) throw new Error('Unsupported operation');
    process.stdout.write(JSON.stringify({ ok: true, ...output }));
  } catch (error) {
    const rawReason = String(error?.message || 'Execution preflight failed');
    const reason = rawReason.replace(/https?:\/\/[^\s"']+/gi, '[RPC URL redacted]').slice(0, 240);
    process.stdout.write(JSON.stringify({ ok: false, reason }));
    process.exitCode = 1;
  }
}

if (process.argv[1]?.endsWith('core_asset_execution.mjs')) await main();
