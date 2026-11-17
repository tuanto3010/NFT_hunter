# NFT Hunter — Master Plan & Resume Playbook v2.1

**Updated:** 2026-11-15
**Local workspace:** `D:\NFT_hunter`
**Dashboard:** `http://127.0.0.1:8767` (last verified by Codex; may change)
**Status:** Planning / evidence-backed research; NOT end-to-end operational
**Primary objective:** Automatically discover legitimate, currently open, **zero-mint-price** third-party NFTs, validate wallet eligibility and total costs, prepare the exact mint transaction, obtain explicit user approval, submit through a supported wallet, and verify ownership.
**First acceptance target:** ONE real third-party free mint confirmed on-chain; no self-created test asset as substitute.
**Budget:** $5 shared across all wallets/chains, inclusive of fees and uncertain transaction reservations. No spending without approval of the exact transaction.

> This is a handoff plan based on user-provided Codex Phase 1 research and prior project reports, **not** a direct audit of the user's local disk. Treat versions, connectivity, account state and project code as needing confirmation when work resumes.

## 1. Executive decision

1. **Do not rewrite NFT Hunter.** Preserve the existing Python/FastAPI/SQLite/dashboard/Telegram, wallet adapters, EVM preview, Solana code, and test suites.
2. **First execution path: BNB Chain + Binance Agentic Wallet (BAW)**, subject to current feasibility. Rationale: a real APASS NFT was previously minted using the BAW CLI manually, demonstrating a workable signing/broadcast path; Hunter itself has not executed that mint. Base + BAW/MetaMask is the fallback, Solana + Phantom remains supported later.
3. **First discovery research path:** OpenSea Drops/SeaDrop where available, plus verified official project/launchpad sources and permitted on-chain discovery. Do not assume OpenSea covers BNB Chain or that any feed contains active free mints; test actual chain coverage and live data.
4. **First supported mint mechanism:** select based on a verified live campaign, likely SeaDrop or a supported EVM mint method. Do not implement arbitrary function probing or dynamic contract execution as a generic shortcut.
5. **Automation boundary:** discovery, verification, ranking, preview preparation and alerts can be unattended. Signing/broadcasting requires explicit approval of the exact transaction until a separate, reviewed bounded-autonomy policy is authorized.
6. **Old Solana unknown submissions:** retain `SUBMISSION_UNKNOWN`, never retry, reconcile read-only when possible. They do not prevent read-only discovery work.

## 2. Source-of-truth and evidence levels

Evidence labels:
- `LIVE_CONFIRMED`: transaction/RPC/API result actually observed with timestamp and chain/block.
- `LOCAL_CODE_CONFIRMED`: code inspected in the current working tree.
- `DOCUMENTED`: official docs describe capability, not yet tested in Hunter.
- `RESEARCH_REPORTED`: Codex research report, not independently revalidated in this handoff.
- `UNVERIFIED`: claim without sufficient primary/on-chain evidence.
- `BLOCKED`: required data or permission unavailable.

### 2.1 Current state from prior reports

| Component | State | Evidence / caveat |
|---|---|---|
| Dashboard FastAPI, SQLite | Previously live | API HTTP 200 on port 8767; startup environment may differ |
| Discovery scheduler/campaign cycle | Partial | Last reported completed cycle yielded 0 leads; scanner stopped |
| EVM scanner | Partial | Observes recent on-chain mint events; does not verify active free campaigns or eligibility |
| EVM transaction builder | Partial | Zero-value and static ABI constraints; no SeaDrop tuples/dynamic proofs |
| BAW adapter | Read-only + preview | No execute method; preview request ID discarded, per Phase 1 |
| BAW CLI manual mint | **One prior success: APASS** | BNB Chain, token #5848; external manual CLI, NOT Hunter automation |
| MetaMask | Previously connected/read-only | Full live mint execution not proven |
| Phantom | Previously connected | Self-created Core preview and signing code; not third-party mint proof |
| Solana submissions | Unresolved | Two records `SUBMISSION_UNKNOWN`, RPC `found:false` insufficient to prove failure |
| Telegram | Implemented | Real test message previously sent; duplicate suppression; no mint approval workflow |
| Tests | Prior audit 180 Python / 25 Node | Re-run on current tree; not proof of live mint |
| Verified active free third-party campaign | **0** | This is the key blocker |

### 2.2 Phase 1 research: three GitHub repositories

**Koporkey/nft-mint-bot** — https://github.com/Koporkey/nft-mint-bot
- Ethers-based EVM mint engine, SeaDrop v1/v2 handling, Merkle allowlist retrieval, simulation, gas estimates, Telegram commands.
- No Binance Agentic Wallet CLI integration. Uses private-key signer; contains unlimited ERC-20 approval (`MaxUint256`) path. No confirmed license; **study architecture only, do not copy source**.
- Key lesson: dedicated SeaDrop builder and proof resolution; do not adopt generic speculative mint probes.

**Devin1-tri/opensea-minter** — https://github.com/Devin1-tri/opensea-minter
- OpenSea Drops stage and transaction-building API plus Ethers signing; useful to understand `to/data/value` boundary.
- No BAW integration; private key in `.env`; generic getter misses are not proof of zero price. MIT claimed in package/README, but verify full license before code reuse.
- Key lesson: OpenSea transaction-building data is an input requiring independent verification, not automatic execution authority.

**blksapphire/NFTScan** — https://github.com/blksapphire/NFTScan
- OpenSea-based discovery, list→filter→enrich, request budgets, dedupe, alerting, scoring based on observed mint activity.
- Not a mint engine, no BAW integration. Scoring is not security certification. MIT claimed in package, verify license before reuse.
- Key lesson: filter cheaply, enrich only promising leads, persist provenance and quota metrics.

**Overall Phase 1 conclusion:** None of the three repos is a plug-and-play NFT Hunter implementation. Reuse concepts, not private-key handling, unsafe approvals, unsupported assumptions or unlicensed code.

### 2.3 Known source/documentation targets to verify again

- Binance Agentic Wallet developer docs and external-sign reference: https://developers.binance.com/en/docs/products/agentic-wallet/welcome and https://github.com/binance/binance-skills-hub/blob/main/skills/binance-web3/binance-agentic-wallet/references/external-sign.md
- OpenSea Drops API / programmatic mint: https://docs.opensea.io/reference/get_drops and https://docs.opensea.io/docs/mint-from-a-drop
- thirdweb NFT claim: https://portal.thirdweb.com/typescript/v5/erc721/claimTo and https://portal.thirdweb.com/typescript/v5/erc1155/claimTo
- Metaplex Core Candy Machine: https://www.metaplex.com/docs/smart-contracts/core-candy-machine/mint
- Alchemy NFT API: https://www.alchemy.com/docs/reference/nft-api-quickstart

These URLs are research starting points, not guarantees of current API availability, terms, chain coverage, or free mints.

## 3. Product requirements

### 3.1 Functional

`FR01 Discovery`: permitted, timestamped official/launchpad/on-chain sources; chain coverage; pagination, quotas, backoff, per-source health; avoid treating secondary-market sales as primary mints.

`FR02 Normalize`: stable campaign identity (`chain_id`, mint program/contract, stage, collection), provenance, dedupe, historical snapshots, confidence and verification freshness.

`FR03 Free price`: explicit mint consideration of **0 native token and 0 other token/NFT payment**. Gas/rent/protocol fee are separately estimated. Unknown payment, approvals, burns, stake, subscriptions or opaque transfer effects => not free.

`FR04 Eligibility`: wallet-specific stage, start/end, supply, caps, Merkle proofs, signatures, guards, backend co-sign, geofencing, CAPTCHA and bot restrictions; never bypass access controls.

`FR05 Risk`: official contract provenance, correct chain, verified ABI/program, calldata semantics, value/token flows, no unlimited approvals, simulation, bot-tax/failure penalty checks, fail closed when unknown.

`FR06 Builder`: dedicated mechanism-specific adapter; deterministic transaction, exact recipient, calldata, value, chain, expected asset/collection; bind a hash of the transaction and evidence to approval.

`FR07 Wallet`: BAW CLI preview and execute with retained request IDs; MetaMask browser EVM; Phantom Solana. Never import or expose seed/private key. Wallet capabilities are chain-specific.

`FR08 Budget`: shared USD cap $5; fresh FX quote and timestamp, maximum expected fee, actual spend and reserved/unknown spend; concurrent atomic reservations, explicit per-tx cap, prevent race conditions. Do not treat USD estimate as a blockchain-enforced hard limit.

`FR09 Execution`: one exact approval, revalidate price, nonce/blockhash, gas, wallet, chain and digest; at-most-once submission attempt; handle wallet/user rejection; no retries on uncertain broadcasts.

`FR10 Confirmation`: transaction finality, mint event/account changes, exact collection and ownership, NFT ID/token ID; avoid counting unrelated inbound spam NFTs or mere successful receipts.

`FR11 Automation`: 15-minute default scan, independent retries for read-only APIs, prioritized candidate queue, expiration, idempotency, notifications, restart recovery, clear pause/resume/kill switch.

`FR12 Observability`: compact dashboard and structured logs; per-source yield, verified-free conversion, estimated and actual spend, transaction state, provenance, reason codes.

### 3.2 Nonfunctional / security

- Windows + VS Code local-first; Python 3.14 existing environment; Node dependencies only when justified.
- Preserve SQLite with reversible migrations/backups and tests; no silent destructive schema changes.
- Local service binds loopback by default; authenticated/CSRF-protected state-changing routes; do not expose signing endpoints to public internet.
- Secrets only in secure local env/config; redact from logs, reports, browser and Git; no external repo code copying without license review.
- API rate limits, cache, source terms, timeouts, bounded concurrency, deterministic failures.
- Test mocks cannot be counted as real opportunities or successful on-chain mints.
- Document risk of token/NFT spam, malicious metadata and links; never automatically interact with received NFT metadata.

## 4. Target architecture and interfaces

```
Sources (OpenSea / launchpads / on-chain)
    -> Source Adapters (read-only)
    -> Candidate Store + Provenance
    -> Cheap Filter + Dedupe
    -> Enrichment + Official/On-chain Verification
    -> Wallet-specific Eligibility + Cost/Risk Engine
    -> Mint Mechanism Adapter (SeaDrop / supported EVM / Solana)
    -> Immutable Transaction Preview + Approval Record
    -> Wallet Executor (BAW / MetaMask / Phantom)
    -> Receipt/Asset Verification
    -> Ledger + Notifications + Scheduler
```

Suggested interfaces (adapt to existing code rather than forcing a rewrite):
- `DiscoverySource.fetch(cursor, budget) -> CandidateBatch`
- `CandidateVerifier.verify(candidate, wallet) -> EvidenceBundle`
- `MintAdapter.detect(candidate) -> SupportDecision`
- `MintAdapter.build(candidate, wallet, proof) -> UnsignedTx + ExpectedMint`
- `RiskEngine.assess(tx, evidence, budget) -> Allow/Block + reasons`
- `WalletAdapter.preview(tx) -> PreviewId + wallet_preview_ref`
- `ApprovalService.approve(exact_digest, user_action) -> OneTimeApproval`
- `WalletAdapter.execute(approved_preview) -> SignatureOrUnknown`
- `ConfirmationService.verify(signature, expected_asset) -> Outcome`

**No generic execute endpoint accepting arbitrary calldata from an untrusted scanner result.**

## 5. Data model (proposed; inspect actual SQLite before migration)

`source_runs`: source, started_at, completed_at, cursor, request_count, quota, result_count, error.

`campaigns`: id, chain, contract/program, collection, official_url, mechanism, stage, start/end, status, last_seen.

`campaign_evidence`: campaign_id, source URL, observed_at, block/slot, claim, verification_method, confidence, raw response hash.

`wallet_eligibility`: campaign_id, wallet, stage, allowed, proof_ref (not secrets), max_mints, checked_at, reason.

`cost_quotes`: campaign_id, wallet, mint_price, payment_assets, network_fee_max, rent, protocol_fee, failure_penalty, USD_fx, fx_timestamp, total_max_usd, expires_at.

`tx_previews`: id, campaign_id, wallet, chain, exact_message_hash, tx payload hash, wallet preview reference, expiry, risk decision, approval state.

`tx_attempts`: preview_id, idempotency_key, signature, nonce/blockhash/last_valid_height if relevant, submit_attempted_at, state, raw public receipt summary.

`budget_ledger`: wallet, chain, reserved_usd, spent_usd, uncertainty_reserve, exchange_rate, tx reference.

`asset_confirmations`: signature, chain, contract/collection, token/asset ID, owner, verification method, finality, checked_at.

`resume_state`: current phase, last completed checkpoint, blocker, next action, code commit, test results, latest report path.

Never store secret keys, signing material, unredacted credentials or sensitive allowlist credentials in logs.

## 6. End-to-end state machine

Candidate states: `DISCOVERED -> NEEDS_EVIDENCE/WATCHLIST/REJECTED/VERIFIED_FREE`.

Transaction states: `PREPARED -> SIMULATED -> APPROVAL_REQUIRED -> APPROVED -> SUBMITTING -> SUBMITTED -> CONFIRMED`.

Terminal/exception states: `REJECTED`, `EXPIRED`, `FAILED`, `SUBMISSION_UNKNOWN`, `ASSET_VERIFIED_SIGNATURE_UNAVAILABLE` (if grounded).

Rules:
- `VERIFIED_FREE` requires fresh evidence of price, active mint, wallet eligibility, and cost/risk decision.
- Approval binds exact bytes/digest, chain, wallet, contract, value, fee bound, mint stage and expiry.
- On stale preview, rebuild and request new approval.
- A signature unknown to the queried RPC is not proof of failure. Query independent RPCs/read-only explorer when available, preserve reservation and never blindly resend.
- A successful receipt without NFT ownership/collection evidence is not `MINT_CONFIRMED`.

## 7. Implementation roadmap — one autonomous Codex assignment, explicit gates

### Phase 0 — Baseline audit & freeze

**Tasks:** inspect actual code and dependencies, git status/branch, backup DB, verify dashboard startup/port, read wallet interfaces and test baseline; document unknown Solana submissions; map existing modules. No signing.

**Gate G0:** reproducible local startup, test baseline, architecture map, risks and current state captured in `docs/nft-hunter/`.

### Phase 1 — Source research and data validation

**Tasks:** inspect official API documentation, live read-only requests where authorized, confirm keys presence (not values), terms, rate limits, chain coverage, candidate counts, active/free counts, contract availability. Include OpenSea Drops, relevant launchpads, BNB on-chain NFT mint discovery, Solana read-only sources. Compare real results, not marketing claims.

**Gate G1:** at least one source returns real, timestamped campaigns with reproducible provenance OR documented evidence that all tested sources are blocked/zero-yield. Continue improving permitted discovery without manufacturing candidates.

### Phase 2 — Robust discovery pipeline

**Tasks:** source adapters, list→filter→enrich, caching, pagination, quota control, normalized campaign DB, dedupe, evidence, source metrics, 15-minute scheduler; retain current UI.

**Gate G2:** live read-only scan reproducible, persistence/dedupe works, source errors isolated, historical listings correctly filtered. Zero verified candidates is valid, but not a mint-ready state.

### Phase 3 — Eligibility, zero-price and cost verification

**Tasks:** read-only contract/guard/stage verification, wallet-specific proof resolution for supported mechanisms, source reconciliation, token-payment detection, USD fee quote, bot-tax assessment, risk classifications, budget reservation model.

**Gate G3:** a candidate can be accurately classified as VERIFIED_FREE or blocked with evidence; tests include false-free and expired cases. No assumption-based zero price.

### Phase 4 — First real-project EVM mint adapter

**Tasks:** choose one supported mechanism from real candidates. Prefer BAW + BNB if candidate supports it; otherwise Base + BAW/MetaMask. Build dedicated SeaDrop adapter if verified, including dynamic tuple/allowlist proofs, exact calldata and preview. Keep static builder restrictions for other unknown contracts. No private-key signer.

**Gate G4:** unsigned transaction for actual third-party campaign; current wallet eligible; simulation pass; price 0; bounded costs; exact transaction approval preview. If no verified candidate, stop this gate and keep discovery operational rather than inventing a mint.

### Phase 5 — BAW execution and other wallet routing

**Tasks:** persist BAW preview request ID; ensure execute consumes the exact approved preview; inspect CLI versions and confirmation semantics; handle `BROADCASTED`, `PENDING_CONFIRMATION`, rejected and unknown; transaction ID, nonce, receipt and restart recovery. MetaMask/Phantom adapters only as required by verified campaigns, not parallel rewrites.

**Gate G5:** offline/mocked end-to-end tests of approved transaction and negative cases; execution remains disabled until user approves one real transaction. No unattended auto-sign.

### Phase 6 — Confirmation, ledger, automation and UI

**Tasks:** ERC-721/1155 mint receipt decoding and owner checks, Solana collection checks when used, budget atomicity, scheduler, approval queue, alert, pause switch, concise source and conversion metrics.

**Gate G6:** scanner→verified candidate→preview→approval queue works autonomously; transaction unknown cannot double-submit; restart recovery tested.

### Phase 7 — One controlled real mint

**Preconditions:** verified official project; active free mint; wallet eligibility; supported mint adapter; correct chain; known maximum fee within remaining $5; no opaque approvals; preview digest and simulation; explicit user authorization for exact transaction.

**Action:** present full preview and ask approval; wallet executes once; verify finality and collection ownership; record actual fee and proof.

**Gate G7:** `REAL_FREE_MINT_CONFIRMED` with chain, wallet, contract, token ID, tx hash, official evidence, timestamp and cost. If no candidate, report blocker honestly; do not force a transaction.

### Phase 8 — Expand breadth after first success

Add Base, BNB, Solana adapters and other wallets only where source yield and mechanisms justify them; evaluate limited unattended authorization separately. Track yield and safety metrics.

## 8. Acceptance test matrix

| Test | Expected outcome |
|---|---|
| Price field absent or `null` | Block as UNKNOWN, never free |
| 0 native value but ERC-20 transfer required | Block as not free |
| Wrong chain/contract/wallet | Block before signing |
| Expired stage, sold out, allowlist mismatch | Block |
| Missing or invalid Merkle proof | Block |
| Bot tax or failure penalty not bounded | Block |
| API transaction payload changes after preview | Invalidate approval |
| Gas/FX quote stale or budget exceeded | Re-quote and reapprove or block |
| Two workers pick same campaign/wallet | One reservation/idempotency key |
| Submission RPC timeout | `SUBMISSION_UNKNOWN`, no automatic retry |
| RPC cannot find signature | Investigate read-only, never infer failure |
| Receipt success but no correct NFT | Not confirmed |
| Incoming unrelated NFT spam | Not counted |
| Scanner restart | Preserve cursors, pending states, budget |
| Missing API key / quota exhausted | Record BLOCKED, continue other sources |
| Self-created NFT success | Not third-party acceptance |

## 9. Research and engineering decisions to document

For each major choice write a short ADR in `docs/nft-hunter/decisions/`:
- Decision, date, alternatives, evidence, safety impact, rollback, next review.
- ADR-001 BAW-first execution based on manual APASS precedent.
- ADR-002 Source priority based on live yield, not popularity.
- ADR-003 No direct private-key signing or unlimited approvals.
- ADR-004 Exact-transaction human approval until explicitly changed.
- ADR-005 Dedicated mint adapters; no arbitrary generic ABI probes.

## 10. Durable work tracking and resume system

Codex should create and maintain these **inside `D:\NFT_hunter\docs\nft-hunter\`**:

- `MASTER_PLAN.md` — this plan, updated when decisions change.
- `PROJECT_STATE.md` — 1-page current status: current phase/gate, verified achievements, active blocker, next safe action, code commit, last tests, dashboard port, wallet/chain scope, budget, no secrets.
- `TASK_BOARD.md` — ordered backlog with IDs, dependencies, priority, owner, status (`TODO/DOING/BLOCKED/DONE`), acceptance evidence.
- `RESEARCH_EVIDENCE.md` — official docs and live data findings with URLs, timestamps, chain/block, confidence and rate limits.
- `ARCHITECTURE.md` — modules, interfaces, state machine, schema, flow and security boundaries.
- `RUNBOOK.md` — Windows PowerShell commands to start, stop, scan, test, inspect status, backup DB, recover from failures.
- `CHANGELOG_WORK.md` — dated sessions, changed files, migrations, test counts, decisions.
- `decisions/ADR-*.md` — architecture decisions.
- `SESSION_HANDOFF.md` — most important handoff, always updated at end of each work session.

### Required `SESSION_HANDOFF.md` template

```
# NFT Hunter Session Handoff
Updated at: <local timestamp>
Repo root: D:\NFT_hunter
Git branch/commit: <...>
Current phase/gate: <...>
Overall goal: 1 verified third-party free mint, then automated scan-to-approval
What is LIVE confirmed: <...>
What is code-only: <...>
What is blocked: <...>
Changes this session: <files/migrations>
Last tests: <commands and pass/fail>
Last live source check: <timestamp, sources, candidates>
Pending transaction IDs/status: <public IDs only; NEVER retry unknown>
Budget: <spent, reserved, unknown; all wallets>
Next 3 actions: <...>
Commands to resume: <...>
Human approval needed: <yes/no, exact reason>
Known hazards: <...>
```

### Resume protocol for any new Codex session

1. Read `docs/nft-hunter/SESSION_HANDOFF.md`, `PROJECT_STATE.md`, `MASTER_PLAN.md`, `TASK_BOARD.md`, and recent `CHANGELOG_WORK.md`.
2. Check `git status`, current commit, environment and actual running processes. Never assume a dashboard is stopped or start a conflicting second server.
3. Re-run the smallest relevant test and one read-only health check; don't re-run all research unless sources changed or evidence expired.
4. Resume the first unfinished task whose dependencies are met. Continue through safe phases without repeated user prompts.
5. Stop for credentials, financial approval, new high-risk authorization, destructive migration or other explicitly blocked actions.
6. Before ending, update all handoff files, evidence and test summary. No vague 'done' claim.

### Compact user resume prompt

> Resume NFT Hunter from `D:\NFT_hunter`. First read `docs/nft-hunter/SESSION_HANDOFF.md`, `PROJECT_STATE.md`, `MASTER_PLAN.md` and `TASK_BOARD.md`. Inspect current git status, verify the relevant environment, then continue the next safe uncompleted task. Follow all safety gates; never sign, broadcast, spend or retry unknown transactions without my explicit approval of the exact transaction. Update the handoff files before stopping. Report only milestones, blockers requiring my action, and acceptance evidence.

## 11. Reporting and token economy

- Work autonomously through multiple safe tasks per session, but do not bypass evidence gates.
- Report in compact format: **gate, live findings, files changed, tests, blocker, next action**.
- Put detailed research and logs in local markdown files, not chat.
- Do not repeatedly paste full test logs or re-explain prior history.
- Checkpoints after a meaningful gate or when user approval is required; do not interrupt for routine coding decisions.
- If a live campaign is unavailable, continue improving verifiable discovery and readiness; do not generate a fake campaign or transaction.

## 12. Immediate next task (as of this handoff)

**Highest-value next engineering task:** implement the *read-only evidence-to-builder bridge* for an EVM NFT drop mechanism, and research real candidate yield in parallel, while preserving existing BAW CLI boundary. Specifically:
1. Audit installed BAW CLI contract preview and how request IDs can be retained; no execute.
2. Verify OpenSea Drops chain coverage, API key availability, and live source yield. Compare BNB Chain official/on-chain discovery sources; avoid assuming OpenSea covers BNB.
3. Normalize official campaign, contract, stage, price and wallet eligibility into a provenance-rich candidate record.
4. Draft dedicated SeaDrop transaction/allowlist adapter requirements and tests based on actual ABI and source evidence, avoiding speculative generic minting.
5. Identify one **currently live** zero-price mint with official contract and wallet eligibility; if none, report 0 and keep the scanner ready.

**Do not** enable Binance Developer Mode, Secure Auto Sign, or sign/broadcast during this task.

## 13. Known unresolved issues

- No verified active third-party zero-price NFT candidate as of the reports.
- Existing Solana transaction records `SUBMISSION_UNKNOWN`; last RPC found no signature, which does not prove failure.
- OpenSea live Drops access, API key, chain coverage, rate limits and live free-mint yield not yet established in this handoff.
- BAW CLI preview request ID currently discarded by Hunter; no Hunter execute integration.
- Existing EVM builder lacks SeaDrop dynamic proof/tuple support.
- Binance Agentic Wallet contract execution with a Hunter-built third-party NFT transaction has not been proven.
- Total $5 budget cannot be assumed unspent until outstanding transaction costs are reconciled.

---

**Definition of done:** A genuine third-party NFT, mint price exactly zero, acquired through an approved real transaction on a supported chain, with correct collection/ownership independently confirmed, public transaction reference, and actual spend recorded. Until then, the product is **not complete**.


## 14. Phase R1 verified baseline (2026-11-15) — supersedes assumptions in §12

**Evidence source:** user-supplied Phase R1 research report. This is a read-only local audit, not an independent live API verification by this document author.

### R1 audit snapshot

| Area | Evidence and status | Required next gate |
|---|---|---|
| Python / Node | Python 3.14.10 in `.venv`; Node 26.13.1 | Preserve existing runtime; check compatibility before installing |
| Latest campaign cycle | 2026-11-15 14:14 UTC: Colony RSS 50/0 leads; HowRare 0/0; Magic Eden 100/0 | Integrate one authenticated high-quality campaign source |
| Existing observations | 16 observations: 13 BNB, 3 Solana; not 16 active campaigns | Keep observations separate from verified campaigns |
| OpenSea Drops | No API key; no authenticated live query; chain coverage for BNB not established | Acquire key by user action; test `/chains`, pagination, current drops and stage prices |
| thirdweb / NFTScan | No keys; documented capabilities only | Use for known contracts / indexing, not automatic proof of free mint |
| BNB RPC | URL configured but read-only probe returned URLError in audit runtime | Diagnose network/endpoint read-only, no credential disclosure |
| Base / Ethereum / Robinhood RPC | Not configured | Read-only health probes only after configuration |
| Solana RPC | Public mainnet fallback returned URLError | Diagnose; avoid speculative retries of old transactions |
| Binance Agentic Wallet | CLI v1.12.0, current UNCONNECTED; Hunter preview only, discards preview request ID | Reconnect via user-approved flow; preserve request ID and exact payload digest; no execute yet |
| MetaMask | Account/chain/balance only | Add EVM send adapter with exact transaction review |
| Phantom | Solana read-only plus self-created Core asset signing path | Separate third-party mint mechanism from signer |
| Solana pending | Two self-created submissions remain SUBMISSION_UNKNOWN | Read-only independent reconciliation; never retry automatically |
| Third-party candidates | 0 qualifying verified active free mints | Do not create a synthetic candidate or claim readiness |
| Tests | None run during R1 | Run baseline tests at next engineering session |

### Architecture decisions frozen after R1

1. **Mechanism adapters are wallet-independent.** SeaDrop / thirdweb Drop / verified custom EVM mint builds an unsigned transaction; Binance or MetaMask routes and signs it. Solana Core Candy Machine / legacy Candy Machine builds instructions; Phantom or supported Binance Solana route signs them. Never infer compatibility merely from network support.
2. **Source records are claims, not verification.** The chain + mint contract + phase + wallet + observed block/slot + source timestamp form the verification context. A drop's API price alone does not prove zero payment or wallet eligibility.
3. **No private-key signing inside Hunter.** Preserve external-wallet signing boundaries; do not copy private-key Ethers minter code or unrestricted approvals from public repositories.
4. **Prioritize evidence-producing integration.** First prove OpenSea authenticated discovery and at least one end-to-end read-only candidate inspection; build a mechanism adapter only when justified by an actual contract and ABI/program evidence.
5. **Separate developer autonomy from spending.** Codex may implement, test, query public APIs/RPC read-only, and maintain documentation; must stop before any signature, broadcast, payment, risky wallet mode or destructive change.

### Integrated execution backlog and gates

| ID | Task | Dependency | Evidence-based acceptance |
|---|---|---|---|
| R2-01 | Bootstrap resume docs and record R1 baseline | None | `MASTER_PLAN.md`, `PROJECT_STATE.md`, `TASK_BOARD.md`, `RESEARCH_EVIDENCE.md`, `SESSION_HANDOFF.md`, `RUNBOOK.md` in repo; no secrets |
| R2-02 | Baseline tests and connectivity diagnostics | R2-01 | Test counts and exact failures; one bounded read-only check per configured RPC; errors classified DNS/TLS/timeout/auth/rate limit |
| R2-03 | OpenSea source adapter with mock fixtures and optional authenticated live checks | R2-02; key for live | Pagination, filters, stage/price/chain normalization, provenance, null price unknown; tests pass; no key logged |
| R2-04 | Campaign/verification schema with safe migrations | R2-01 | Wallet-scoped evidence, chain/contract/stage/block/time, dedupe; backup and migration tests; no historical corruption |
| R2-05 | EVM candidate verifier | R2-03/04 or known official candidate | ABI/source, NFT standard, wallet eligibility, payment paths, availability, `eth_call`, gas, total cost; fail closed |
| R2-06 | One verified mechanism transaction builder | R2-05 + real contract evidence | Deterministic unsigned payload, no unlimited approvals, allowlist proof validation if needed, digest-bound simulation tests |
| R2-07 | Wallet execution boundary | R2-06 | Binance preview request ID preserved and exact payload-bound approval; MetaMask prompt path; no unattended broadcast |
| R2-08 | Confirmation, idempotency, budget ledger | R2-07 | Receipt + NFT ownership; pending/unknown reconciliation; cross-wallet $5 reservation accounting |
| R2-09 | Live candidate acceptance | All relevant gates + user approval | Real third-party zero-price mint, confirmed NFT in expected wallet, fee ledger updated |
| R2-10 | Scheduled operation and alerts | R2-03/04; safe verification path | 15-minute scan, bounded requests, duplicate suppression, health/Telegram; auto-prepare only until separately authorized |

**Sequencing:** R2-01/02/03/04 may be worked on in one safe Codex session; R2-05/06 depend on real contract evidence, not a speculative implementation. R2-07/08 can be tested with fixtures without signing; R2-09 is a human-controlled gate. If OpenSea is blocked by missing credentials, continue R2-04 and documented-source analysis, but mark authenticated live acceptance BLOCKED. Never claim a fully functioning pipeline based only on fixtures.

### Exact next Codex instruction (copy/paste)

> Read `D:\NFT_hunter\docs\nft-hunter\MASTER_PLAN.md` and Phase R1 report. Execute the **R2 safe batch**: establish/update persistent handoff docs, baseline tests, read-only RPC diagnostics, OpenSea Drops adapter with fixtures and optional authenticated live checks, and wallet-scoped evidence schema with tested reversible migration. Keep the existing code and safety gates. Do not sign, execute, broadcast, spend, enable Developer Mode, or retry SUBMISSION_UNKNOWN transactions. If OpenSea key is missing, implement/test the adapter offline and mark live checks BLOCKED; do not invent candidates. Stop after the safe batch and update `SESSION_HANDOFF.md`, `PROJECT_STATE.md`, `TASK_BOARD.md` with exact commands, tests, blockers, and next eligible task. Report only the milestone summary.

### Resume checkpoint template (overwrite each session)

```
R3 execution update: 2026-11-15 23:37 Asia/Saigon
Git commit / branch / dirty files:
Current gate: R2-XX
Completed gates with evidence:
Blocked gates / exact dependencies:
Live API observations with UTC timestamps:
Test commands and results:
Pending unknown transactions (no retry):
Shared budget: confirmed spent / reserved / unresolved / available:
Next eligible safe task:
Human approval required for:
How to start services (only if not already running):
```

**Source-plan checkpoint (when this plan was authored):** Phase R1 was complete as research only and R2 engineering had not yet been claimed. The R2 safe batch was subsequently completed; see `PROJECT_STATE.md` and `TASK_BOARD.md`. R3 connectivity diagnosis on 2026-11-15 found DNS success but TCP 443 denied (WinError 10013) before TLS/HTTP, so OpenSea live authentication and drop data remain unverified. Next action is authorized outbound-policy review, then one bounded cycle. Do not interpret documented API capabilities as live verified mint opportunities.

### Autonomous mission checkpoint — 2026-11-16

- Phase 1 live discovery remains externally blocked: Python, Node, and PowerShell resolve OpenSea DNS, but TCP 443 fails in each. No TLS/HTTP/auth result, no new campaign cycle, zero current verified candidates. Historical feed counts and parsing outcomes are retained in `RESEARCH_EVIDENCE.md`; they are not current campaign confirmation.
- Phase 2 completed only the safe shared-budget accounting foundation. No evidence-supported third-party mint mechanism is available to build yet. Existing self-created Core asset path is not third-party campaign support.
- Phase 3 added read-only budget visibility in API/dashboard. Existing scan controls and source health remain unchanged. No wallet signing or broadcast path activated.
- New additive budget table applied after SQLite backup. Two legacy `SUBMISSION_UNKNOWN` submissions remain liabilities, so usable remaining USD cannot be computed and new reservations are blocked. This preserves the shared USD 5 ceiling without treating unknown outcomes as zero cost.
- Tests after changes: Python 200 passed; Node 26 passed. No dependency installation, scan, Telegram delivery, wallet action, signature, broadcast or spend.
- Next gate: authorized resolution of outbound TCP 443; then exactly one bounded read-only source cycle. Only use a real source-evidenced campaign to choose the next mint adapter.
- Resume continuation: one combined Solana status RPC call for both saved submissions failed before HTTP with WinError 10013. Added sanitized no-retry command `python -m app.solana.status_check` for an authorized local runtime; Python suite 204 and Node suite 26 pass.

## Independent reconciliation continuation — 2026-11-16

- The 32-character identifiers are internal preview IDs. The saved `signature` fields decode as 64-byte base58 transaction signatures. Full transaction identifiers and wallet addresses are omitted.
- User-reported `getSignatureStatuses(searchTransactionHistory=true)` returned HTTP 200 with null for both signatures. The old asset check returned a combined `ASSET_ACCOUNT_NOT_FOUND_OR_WRONG_PROGRAM`; it cannot distinguish an absent account from a different program owner. Neither result proves transaction failure.
- Hunter now requests documented `getTransaction` after a null status, then checks asset account existence and program owner separately. Read failures remain RPC unavailable; no absent-history outcome releases budget or triggers retry.
- Added append-only SQLite `core_asset_reconciliation_checks` with timestamped, sanitized evidence. Two records capture this runtime's bounded Solana attempt: two `getTransaction` calls plus one `getMultipleAccounts` call, all blocked before HTTP with `PermissionError`. Both original transaction rows remain `SUBMISSION_UNKNOWN`.
- OpenSea: one read-only upcoming-page request using configured key presence (secret value not read or logged) failed with `URLError` before HTTP. HTTP/authentication untested; 0 items, 0 normalized/saved leads. No campaign cycle.
- Tests: Python 207 passed; Node 26 passed. No wallet action, transaction, Telegram, signing, broadcast or spending occurred.
- Current local status endpoint rerun command is in `RUNBOOK.md`; use port 8767 after restarting the dashboard. It appends evidence and returns separate `asset_check_status` values. Never retry either transaction.
