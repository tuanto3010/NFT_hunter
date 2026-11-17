# Binance NFT Hunter

A Windows-first, read-only multi-chain NFT discovery foundation. It supports
bounded recent mint-event discovery on BNB Smart Chain, Ethereum, Base, and
Robinhood Chain, plus Solana launchpad discovery, optional DAS indexing,
candidate-mint inspection, and known Core Candy Machine inspection.
It does not sign or submit transactions. A scan is not a complete index of any
chain and an empty result does not mean there are no opportunities.

## Development plan

1. **Bootstrap:** package layout, configuration, SQLite, base read-only scan.
2. **Multi-chain adapters (implemented foundation):** shared EVM JSON-RPC
   scanners for BNB, Ethereum, Base, Robinhood; separate Solana mint inspection;
   bounded concurrent scheduler and adapter tests.
3. **Discovery coverage:** bounded EVM block cursors; Solana candidate, DAS,
   and public launchpad-index discovery are implemented, but not chain-complete.
4. **AI summaries:** add the official OpenAI SDK behind usage limits; outputs
   remain untrusted and cannot make security or transaction decisions.
5. **Contract analysis:** fuller deterministic verification, mint conditions,
   and risk classification.
6. **Telegram (gated alerts implemented):** environment-backed Bot API sender
   with a persistent duplicate ledger; Solana alerts require deterministic
   on-chain zero-price and wallet-specific eligibility evidence.
7. **Approval workflow:** transaction-bound approvals with expiration.
8. **Wallet (read-only plus gated preview):** use the documented Binance
   `baw` CLI for connection and reads, with a Developer-Mode-gated EVM mint
   preview. Solana campaign mint previews remain fail-closed; a separate
   self-created Metaplex Core asset preview is being added below.
9. **Dashboard (implemented basic version):** localhost-only FastAPI page and
   read-only APIs backed by the existing SQLite database.
10. **Integration testing and production readiness:** mocked providers,
    simulation/testnet, operational review, and Windows packaging.

## Architecture

`app.main` loads settings, builds configured chain adapters, initializes SQLite,
and runs one scan or the periodic scheduler. `app.scheduler` runs chain scans
with bounded concurrency and uses a SQLite process-identity lease to prevent
overlapping scanner instances. `app.discovery`
contains a chain adapter interface, one shared EVM JSON-RPC implementation, and
small per-network EVM adapters. `solana_scanner` is independent and uses Solana
RPC plus Metaplex Token Metadata parsing. `app.database` stores unified chain,
asset, standard, price, gas token, eligibility, verification, risk, source, and
timestamp fields. `app.wallet.capabilities` records documented network support
without inferring that a network-supported wallet can mint NFTs.
`app.dashboard.api` exposes read-only summary, discovery-list, source-health, and detail
endpoints and serves static HTML/CSS/JavaScript. Its launcher binds to
`127.0.0.1`; it does not expose credentials or create sample records.
`app.notifications.telegram_bot` sends explicit test messages and qualified
plain-text discovery alerts. `app.notifications.solana_alerts` applies a
deterministic evidence gate before the scheduler can notify. `app.wallet.dummy_wallet`
implements a separate in-memory simulation interface; its fake addresses,
balances, decisions, and outcomes are marked simulated. `app.wallet.binance_wallet`
wraps Binance Agentic Wallet connection, read commands, and a gated preview.
`transaction_builder` prepares only verified, eligible, low-risk, zero-price
ERC-721/ERC-1155 mint calls on BNB, Ethereum, and Base. `transaction_simulator`
checks Developer Mode before building calldata, checks the runtime network and
wallet address, and requests Binance's preview. Robinhood remains read-only;
third-party Solana campaign preview remains fail-closed. A separate Core asset
creation flow never routes campaign records through the self-created asset builder.

Scanner and dashboard notifications default to `TELEGRAM_MODE=DRY_RUN`; this
logs that an alert would have been sent but never calls Telegram's HTTP sender.
Real delivery requires both `TELEGRAM_MODE=LIVE` and
`TELEGRAM_LIVE_ENABLED=true`, plus valid credentials. `DISABLED` suppresses
delivery and dry-run notification logs. The standalone explicit Telegram test
command remains a separate user-invoked action.

Each scan records its PID and OS process start identity. Ctrl+C is recorded as
`interrupted`. A later run can mark a scan `abandoned` only after confirming
that exact process instance is gone; elapsed time alone is never used. Legacy
`running` rows from older versions lack an owner identity, so they remain
unchanged and block new scans until ownership can be safely resolved.

## Basic code flow

For `--scan`, settings select enabled chains and configured RPC URLs. EVM
adapters validate `eth_chainId`, query Transfer logs over a bounded recent
block window, recognize mint events where the `from` address is zero, check
ERC-721/ERC-1155 interface IDs, and optionally query a configured Blockscout
compatible explorer for verification and ABI data. Solana uses a separate RPC
adapter to inspect candidate mint addresses, supply, authorities, and Metaplex
Token Metadata; it does not apply EVM standards. The scheduler stores unique
observations and scan history in SQLite.

The Solana adapter can inspect explicit candidate mints via
`SOLANA_MINT_CANDIDATES`. For indexed discovery, configure
`SOLANA_DAS_RPC_URL`; it queries the optional DAS `searchAssets` method for
recent Token Metadata, programmable, Core, and compressed asset interfaces.
It also fetches one bounded page (up to 500 records) from Magic Eden's public
Solana launchpad collections index per scan and keeps only future-dated Solana
records. The scanner performs one bounded API request per Solana scan (up to
the endpoint's documented 500-record page limit), with transient-failure retry
and backoff.
That index is a marketplace listing, not an official project announcement or
on-chain mint configuration: its price is retained only as an unverified claim,
wallet eligibility remains unknown, and fees/rent remain unknown until an
unsigned mint message can be constructed from verified campaign data.
Known Core Candy Machine addresses can be supplied in
`SOLANA_CANDY_MACHINE_CANDIDATES`; the scanner verifies the owning program and
published fixed account header, reads collection/supply/authority fields, and
checks whether the mint authority is owned by Candy Guard. The supported Candy
Guard decoder recognizes only the exact empty default configuration. Other
guard layouts, schedules, payment-token rules, and wallet eligibility stay
UNKNOWN unless independently verified.
Per-source health and request counts are persisted in SQLite and available at
`/api/source-health`.
The plain public Solana RPC is not a chain-wide NFT index. DAS is an indexed
provider extension, and the scanner currently samples only the first page per
interface. Metaplex Core inspection uses DAS `getAsset` and reports unsupported
when no compatible DAS endpoint is configured.

Each EVM scanner stores its last successful block in SQLite and overlaps the
next scan by 12 blocks to tolerate short reorgs. Its first scan is limited by
`EVM_LOOKBACK_BLOCKS`. This is not a historical backfill or a finality-proof
cursor.

## Windows setup

Python 3.12 or newer is recommended. The current code uses the standard library
for discovery, plus FastAPI and Uvicorn for the dashboard. From PowerShell,
create a venv using your installed interpreter (adjust the path if needed):

```powershell
& 'C:\Users\1tuan\AppData\Local\Programs\Python\Python314\python.exe' -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install "fastapi>=0.111,<1" "uvicorn[standard]>=0.30,<1"
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
python -B -m unittest discover -s tests -v
```

## Telegram test notification

The independent Telegram sender uses Telegram's official HTTPS Bot API
[`sendMessage`](https://core.telegram.org/bots/api#sendmessage) method. It does
not call the scanner or discovery modules. Configure these
values in your local `.env` (never paste the bot token into chat or commit
`.env`):

```dotenv
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
```

Create a bot through Telegram's official BotFather, open a conversation with
it, and send `/start`. Obtain the destination chat ID through Telegram's
official Bot API `getUpdates` using a trusted local method; the response can
contain private message content, so keep it local. Put the ID in `.env`. After
you have configured both values and explicitly decided to send the test, run
from the project terminal:

```powershell
python -m app.notifications.telegram_bot --test-notification
```

The command sends one plain-text test message and reports a message ID. It
stores no token, chat ID, or message body in SQLite. Delivery attempts are
deduplicated in the configured `DATABASE_PATH`; each explicit test run uses a
new key. Connection errors and explicit HTTP 429 responses receive at most one
retry. A timeout is treated as unknown because Telegram may already have
accepted the message; the same key is then suppressed to avoid duplicates.
Automated tests use a mock HTTP transport and do not send messages.

## Dummy wallet simulation

The dummy adapter does not access a real wallet or blockchain. From Python, it
can be exercised locally:

```python
from app.wallet.dummy_wallet import DummyWalletAdapter

wallet = DummyWalletAdapter()
address = wallet.connect()  # starts with SIMULATED-WALLET-
balance = wallet.get_balance()  # simulated=True, fake BNB balance
transaction = wallet.prepare_transaction("demo-target", "mint", {"quantity": "1"})
wallet.approve_transaction(transaction.transaction_id)  # or reject_transaction(...)
outcome = wallet.simulate_transaction(transaction.transaction_id)
print(balance.simulated, outcome.status, outcome.broadcast)
```

All networks and values are fake, and `broadcast` is always false. The adapter
has no methods for signing, submitting, or broadcasting. This is a local
interface exercise only; it does not validate a transaction against any chain.

## Binance Agentic Wallet adapter and preview boundary

The real adapter uses Binance's documented `baw` CLI rather than a Python SDK.
It wraps QR sign-in initiation/verification, status, supported-chain list,
addresses, balances, and sign-out. Pairing data is hidden from object
representations and is never logged. EVM preview requires verified source,
confirmed eligibility, deterministic low risk, exactly zero mint price, an
allowlisted ABI mint function, and active Developer Mode. Prepared calls bind
the chain, wallet, contract, zero value, calldata, and a digest. Preview results
discard Binance's request ID. The adapter exposes no execute, signing, transfer,
or broadcast method. The CLI owns its session; the application never requests
or stores wallet secrets.

The read-only CLI integration was verified from the user's VS Code terminal:
status was `CONNECTED`, and the runtime chain list included 56, 1, 8453,
`CT_501`, 4663, 137, and 42161. A separate process can resolve a different CLI
session; the adapter inherits its caller's working directory and environment.
Node.js is present, and the official installer requires Node.js 18 or newer.
The approved local package
is installed in `.tools`; the adapter invokes its Node entrypoint without
adding a global executable to PATH. The current official skill documentation
names `@binance/agentic-wallet` and currently requires CLI 1.12.0; the adapter
has a corresponding version check. Wallet balance queries select a chain
with `--binanceChainId`; the documented CLI does not provide a network-switch
command.

The current Skills Hub `wallet chains` reference lists BNB (56), Ethereum (1),
Base (8453), Solana (`CT_501`), plus Robinhood (4663), Arbitrum, and Polygon.
The Agentic Wallet overview page still lists only the first four. The adapter
reads the installed CLI's runtime chain list and does not hard-code Robinhood
as supported. Mint execution is not implemented, and preview capability does
not establish that a particular mint is safe.

Binance documents `contract-call preview` as a generic simulation, not an
NFT-specific operation. Its current instructions require Developer Mode to be
active and unexpired before calldata construction or preview. This application
checks that setting but never enables it. No live mint preview was run because
the discovery pipeline does not currently produce an opportunity that is
verified, deterministically low risk, eligible, and exactly zero price. EVM
network cost is estimated separately with `eth_estimateGas` × `eth_gasPrice`
when a read-only fee estimator is configured; mint price remains a separate
zero-value field. Solana fee estimation and Metaplex instruction parsing remain
outstanding.

Set RPC URLs for BNB, Ethereum, and Base in `.env`. Robinhood Chain defaults to
its documented public RPC and explorer. Solana defaults to the public mainnet
RPC, which is rate limited; automated discovery still requires an external
candidate source. Some shared BNB RPCs reject `eth_getLogs` even when
`eth_chainId` works. For BNB, use a log-capable BNB RPC endpoint in
`BNB_RPC_URL`; BNB Chain documents a free BSCTrace/MegaNode tier. Etherscan V2
is not a free BNB fallback. Source verification for BNB is checked against the
public Sourcify API. The optional `ETHERSCAN_API_KEY` fallback is limited to
Ethereum and Base. Keep provider keys in `.env` and do not buy a plan without
approval. Then run a one-time network scan:

```powershell
python -m app.main --scan
```

Or start the 15-minute scheduler:

```powershell
python -m app.main --schedule
```

The `ENABLED_CHAINS` setting selects a subset of `bnb,ethereum,base,solana,
robinhood`. The public Robinhood and Solana RPCs may impose limits; use a
provider endpoint for regular operation.

Start the local dashboard from another terminal:

```powershell
python -m app.dashboard.api
```

Open `http://127.0.0.1:8000`. The dashboard reads the same `DATABASE_PATH` as
the scanner. To see scan status and next-run timing, run the scheduler in a
separate terminal with `python -m app.main --schedule`. The API is read-only;
it exposes `GET /api/dashboard`, `GET /api/discoveries`, and
`GET /api/discoveries/{id}` only.

In VS Code, open this project folder, run **Python: Select Interpreter**, and
choose the Python 3.14 installation (or the project's `.venv`). Then use the
Run and Debug panel and select **Binance NFT Hunter: read-only scan** or
**Binance NFT Hunter: unit tests**. Select **Binance NFT Hunter: local
dashboard** to start the loopback-only web server.

## Current limitations

- EVM discovery has a persistent per-chain block cursor and short reorg overlap,
  but no complete historical backfill or chain-specific finality policy.
- Only the Robinhood chain explorer is configured by default. Other networks
  need a Blockscout-compatible explorer URL to verify source and inspect ABI.
- Name/symbol and metadata calls are best effort; EVM mint price, eligibility,
  maximum quantity, and a definitive list of mint methods are not established.
- Solana can inspect supplied mint candidates, query an optional DAS first
  page per interface, and query one Magic Eden launchpad page. None of these
  sources currently provides a verified zero-price campaign plus a wallet's
  allowlist proof. Candy Machine inspection requires a known account address;
  there is no global on-chain Candy Machine indexer in this app. Zero-price listing claims remain `UNKNOWN`; unknown fees,
  priority fees, and rent are not estimated. Collection/creator/authority
  fields are best-effort Metaplex Token Metadata parsing. DAS `getAsset` is an
  optional extension for Core, not a standard Solana RPC method.
- Gas methods estimate a caller-supplied read-only candidate only. They do not
  build or submit transactions.
- Telegram notifications are connected to the scheduler but blocked unless a
  deterministic on-chain zero-price proof, verified official source, low-risk
  report, and eligibility proof bound to a specific Phantom address are all
  present. Current sources do not produce that evidence, so no Solana alert is
  presently eligible. No Telegram command or approval workflow exists. The Binance
  read-only adapter has not been tested with the actual CLI or wallet. No
  transaction signing, OpenAI, approvals, or opportunity-qualification
  workflow is present.
  The local dashboard reports stored discoveries only; it does not imply that
  records are free mint opportunities.

## External capability references

- [Robinhood Chain network configuration](https://docs.robinhood.com/chain/connecting/)
  and [official mainnet setup](https://docs.robinhood.com/chain/add-network-to-wallet/)
  document chain ID 4663, ETH gas, the public RPC, and Blockscout explorer.
- [Binance Agentic Wallet documentation](https://developers.binance.com/en/docs/products/agentic-wallet/welcome)
  documents QR authentication and its BNB, Ethereum, Base, and Solana overview.
- [Binance Skills Hub wallet reference](https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/binance-agentic-wallet/references/wallet-view.md)
  documents the `baw` status, chains, address, and balance commands. It currently
  lists Robinhood 4663, which conflicts with the narrower overview network list.
- [Binance external-sign reference](https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/binance-agentic-wallet/references/external-sign.md)
  documents generic EVM contract-call and Solana unsigned-transaction preview,
  and requires Developer Mode before preview. It is not a dedicated NFT-mint
  operation; this project has no execute path.
- [Ethereum JSON-RPC documentation](https://ethereum.org/developers/docs/apis/json-rpc/)
  documents the common read methods used by the EVM adapters.
- [ERC-1155 standard](https://eips.ethereum.org/EIPS/eip-1155) defines the mint
  Transfer events used for event filtering.
- [Solana Metaplex Metadata documentation](https://solana.com/docs/tokens/metaplex)
  describes metadata PDAs, authority, creators, and collection fields.
- [Metaplex DAS `getAsset`](https://www.metaplex.com/docs/dev-tools/das-api/methods/get-asset)
  describes the optional indexed-asset method used for Core inspection.
- [Solana `getFeeForMessage`](https://solana.com/docs/rpc/http/getfeeformessage)
  documents fee estimation in lamports for a serialized message.
- [Magic Eden launchpad collections API](https://docs.magiceden.io/reference/get_launchpad-collections)
  documents the public, paginated listing endpoint; [its API-key/rate-limit
  guidance](https://docs.magiceden.io/reference/solana-api-keys) lists a public
  rate limit of 120 requests per minute and notes that some endpoints require
  API authorization.
- [Metaplex DAS `searchAssets`](https://www.metaplex.com/docs/dev-tools/das-api/methods/search-assets)
  documents indexed NFT discovery and asset interface filters.
- [Metaplex Core Candy Machine account layout](https://github.com/metaplex-foundation/mpl-core-candy-machine/blob/main/programs/candy-machine-core/README.md)
  documents the fixed header fields parsed for known machine addresses; the
  [Candy Guard account and guard rules](https://github.com/metaplex-foundation/mpl-core-candy-machine/blob/main/programs/candy-guard/README.md)
  remain unparsed and are not used to claim free mint or eligibility.

## Safety and data

The database records scan runs, observations, and Telegram deduplication keys;
it never stores private keys or seed phrases. Never put secrets in logs or
Telegram. Binance CLI read operations were live-verified. Generic campaign
preview remains distinct from the explicit, one-time Phantom flow for a
self-created Core test asset. Automated tests use mocks and do not sign or
broadcast; the new execution path has not been live tested. Signing/submission
requires a fresh simulated preview, explicit dashboard approval, and Phantom's
own signature confirmation.

# Solana project watchlist

The local dashboard includes a persistent Solana watchlist. Add a project with
its official site/announcement, a candidate mint or Core Candy Machine address,
and the URL that supplied the candidate. Address kinds distinguish an NFT mint,
collection mint, Core Candy Machine, and unclassified launchpad address. A
single-supply historical NFT mint is explicitly rejected as mint infrastructure.
Optional announcement date, future mint date, price claim, and allowlist claim
are stored independently; manual claims remain unverified until source review
or on-chain evidence supports them. Candidate addresses are validated as
canonical 32-byte Solana public keys and deduplicated in SQLite. A running
scanner checks the watchlist on its next scheduled cycle. The dashboard filters
Solana records by UPCOMING, ANNOUNCED, PRICE_UNKNOWN, and VERIFIED_FREE. Official
websites and social channels are not scraped automatically; announcements are
registered from supplied source URLs and manually reviewed.

Candy Guard decoding is intentionally narrow. Hunter currently verifies only
the official Core Candy Guard account layout with an empty default guard set,
zero guard groups, and no trailing account data. Enabled guards, guard groups,
unknown discriminators, and non-exact account layouts remain UNKNOWN. In
particular, an AllowList Merkle root alone does not verify an individual wallet.
The dashboard requires a manual official-source review and a read-only check
for the selected Phantom mainnet public key before it can emit a
`VERIFIED_OPPORTUNITY` Telegram alert.

Telegram uses separate persistent dedupe keys for `NEW_DISCOVERY` and
`VERIFIED_OPPORTUNITY`. New candidate alerts explicitly label price, schedule,
and eligibility as unverified. No Solana transaction is built, signed, or sent
by this workflow.

## Self-created Solana Core test asset preview

This separate dashboard control prepares a Metaplex Core asset creation
transaction for the connected Phantom mainnet address. It is not a third-party
campaign mint. After PREVIEW_READY, a separate unchecked approval control
displays the reviewed transaction costs and details. The approval action asks
the local backend to revalidate it, partially signs with the generated asset
key, and then asks Phantom to sign. Hunter checks the returned signatures,
transaction message, mainnet, fresh blockhash, simulation, live fee, balance,
and 5,000,000-lamport cap before one `sendTransaction` request. A submission
with an ambiguous response is never retried. Confirmation polling verifies
the Metaplex Core account owner and metadata URI before marking it CONFIRMED.

The generated asset keypair is retained only in server RAM for up to 15
minutes; its secret is never returned to the browser, logged, or written to
SQLite or a file. SQLite stores only the preview/transaction ID, public wallet
and asset addresses, metadata URI, message digest, signature, costs, timestamps,
and status. A server restart discards an unsubmitted signer and preview. Rebuild
after restart, expiry, Phantom account change, or stale blockhash.

The sample files are in `data/metadata/`. Upload both files to a public HTTPS
static host you control, with the JSON and SVG beside one another, and update
the JSON's relative `image` path if your host requires an absolute URL. No
hosting destination is configured by Hunter, so the local files are not a
usable on-chain metadata URI until you publish them. Enter the real public JSON
URL in Wallets, connect Phantom, verify the mainnet balance, and confirm the
displayed test address. Hunter fetches the JSON and image once each, builds the
official Core instruction, reads live fee/rent data, and simulates without
signatures. `PREVIEW_READY` is shown only if those live checks pass. No signing
or submission occurs during preview generation. To initiate one live test,
review the displayed wallet, asset address, metadata URL, instruction accounts,
and total; check the separate exact-transaction approval; choose **Approve
exact transaction & open Phantom**; then approve only the matching request in
Phantom. Hunter submits only after Phantom returns both valid signatures and
the backend revalidates the unchanged transaction. Do not refresh/retry if the
page reports an uncertain submission; use its transaction status/signature.

The preview uses a hard maximum of 5,000,000 lamports (0.005 SOL) for estimated
fee, rent, and Core protocol fee; `CORE_ASSET_PREVIEW_MAX_LAMPORTS` may lower
this limit but is capped at 5,000,000 lamports. This is a SOL-denominated
technical limit, not a live USD quote. It grants no authority to spend by
itself. Signing and the one-time submission require the exact dashboard
approval plus Phantom's own confirmation.

Start the dashboard from the project root in VS Code PowerShell:

```powershell
& '.\.venv\Scripts\python.exe' -B -m app.dashboard.api
```

Open the local Wallets section and use **Build & simulate preview**. The
published test JSON and SVG must remain publicly available over HTTPS. The
Node-only unit suite is `npm run test:core-preview`; the complete
Python suite uses `& '.\.venv\Scripts\python.exe' -B -m unittest discover -s tests`.
