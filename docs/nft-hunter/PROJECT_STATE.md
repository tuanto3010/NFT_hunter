# NFT Hunter Project State — R2 Safe Batch

Updated: 2026-11-15 (Asia/Saigon)

## Product boundary

The target remains a real third-party NFT campaign workflow. Discovery and verification are read-only until a separate exact-transaction approval. No wallet execution was attempted in this batch.

## R2 status

- R2-01 handoff docs: DONE. This folder contains the v2.1/R1 master plan, state, task board, research evidence, runbook, and session handoff.
- R2-02 baseline and read-only diagnostics: DONE. Python 3.14.10 baseline passed 180 tests; Node 26.13.1 baseline passed 25 tests. One `eth_chainId` request to the configured BNB RPC was blocked by socket policy (`WinError 10013`). Other chain RPC URLs were not configured.
- R2-03 OpenSea Drops: IMPLEMENTED + FIXTURE-TESTED. After the user configured `OPENSEA_API_KEY`, one live bounded cycle was attempted; network transport failed with `URLError` before HTTP response. Live candidate validation remains BLOCKED; no candidate was claimed.
- R2-04 wallet-scoped evidence persistence: IMPLEMENTED + MIGRATED. Additive table created after a verified SQLite backup. Existing scan, observation, campaign-lead, and transaction row counts are unchanged. The two `SUBMISSION_UNKNOWN` Core transaction records were preserved; neither was retried or queried.
- R2-05 onward: NOT STARTED. The next eligible task is a bounded live OpenSea source check after the user configures an API key locally, followed by campaign verification only for sufficiently evidenced leads.

Final regression after changes: Python 3.14.10 `unittest` 193 passed; Node `node --test tests/*.test.mjs` 25 passed.

After the key was added locally, one live campaign cycle was attempted: key presence was confirmed without reading it back; Colony, HowRare, Magic Eden, and OpenSea returned `URLError` with no HTTP responses; no items or leads were received/saved. No key/auth diagnosis is possible without an HTTP response. Adapter now stops remaining OpenSea listing types after the first transport/HTTP/schema failure.

## R3 connectivity continuation (2026-11-15 23:37 Asia/Saigon)

- `.venv` Python 3.14.10: OpenSea key presence confirmed without reading the value; no proxy configured.
- DNS for `api.opensea.io` passed (4 unique addresses).
- TCP connect to port 443 failed in Python with `PermissionError`, `WinError 10013`.
- PowerShell `Test-NetConnection` also failed (`TcpTestSucceeded=false`; remote/source address unavailable).
- TLS, HTTPS HTTP status, API authentication, drop retrieval, and lead persistence were not reached/tested. No campaign cycle was run in R3.
- Classification: outbound socket/TCP permission policy before TLS/application. Evidence is consistent with a process/host network restriction; exact firewall/endpoint control owner is not identifiable from this runtime.
- Corrective action: ask the Windows/endpoint-security administrator to review the outbound TCP 443 policy for the local Python/VS Code process and PowerShell. Do not disable protections. Once authorized connectivity is confirmed, rerun the bounded cycle once.
- Updated handoff set: `MASTER_PLAN.md`, `PROJECT_STATE.md`, `TASK_BOARD.md`, `RESEARCH_EVIDENCE.md`, `RUNBOOK.md`, and `SESSION_HANDOFF.md`.

## Current workspace facts

- Workspace: `D:\NFT_hunter`; this copy has no `.git` directory, so there is no local branch/commit baseline to report.
- Python: `.venv\Scripts\python.exe`, Python 3.14.10.
- Node/npm: v26.13.1 / 11.20.0.
- SQLite: `data\nft_hunter.sqlite3`.
- After migration and the previous failed network-bound cycle: 22 scan-history rows, 16 observations, 0 campaign source leads, 2 Core transaction rows, 0 wallet evidence rows. R3 did not run a cycle.
- Both Core transaction rows remain `SUBMISSION_UNKNOWN`. No transaction was retried, signed, or broadcast.
- Backup: `data\backups\nft_hunter_r2_pre_migration_20261115T161544Z.sqlite3`; `PRAGMA integrity_check` returned `ok` before and after migration.

## New implementation

- `app/discovery/opensea_drops.py`: authenticated official Drops API adapter; bounded to six requests, two pages per listing type, 50 records/page, 1 MiB/response, eight-second timeout, no retries; normalizes `upcoming`, `featured`, and `recently_minted`; deduplicates and preserves API claims as unverified.
- Successful OpenSea response status codes are now retained per listing type/page in source metrics, so later live reports can distinguish HTTP success from transport or authentication failure.
- `app/discovery/campaign_feed.py`: records per-source OpenSea metrics/errors and persists normalized leads without upgrading safety or verification status.
- `app/database.py`: additive `wallet_verification_evidence` table with provider, chain, contract, stage, wallet, block, timestamp, status, price/availability/eligibility fields, provenance and content digest. Unique identity includes provider, wallet, stage and block. Evidence sanitizer drops secret/proof-like fields. It does not update collection-level risk or price.
- `app/dashboard/api.py` and `app/main.py`: persist generic EVM verification outcomes per selected wallet/provider while preserving the existing campaign JSON history.
- `.env.example`: documents optional `OPENSEA_API_KEY` and `OPENSEA_CHAINS` placeholders. `.env` was not modified or exposed.
- Dependencies: no Python or npm package was installed or added; the adapter uses Python standard-library HTTP/JSON functionality. Live OpenSea use requires a user-provisioned API key.

## Safety state

No signatures, broadcasts, wallet settings, Developer Mode changes, Telegram deliveries, scans, paid services, or cryptocurrency spending occurred in R2.

## Autonomous mission continuation — 2026-11-15

- Fresh outbound checks in this runtime: `api.opensea.io` DNS resolved (4 addresses) for Python, Node and PowerShell. Python 3.14.10 TCP 443 failed with `PermissionError` / WinError 10013; Node v26.13.1 failed TCP with `EACCES`; PowerShell TCP test failed. No proxy was configured. TLS, HTTPS, API authentication and drop retrieval were not reached. No campaign cycle was run because the preflight failed.
- OpenSea API-key presence is configured, but its value was not read or logged. Authentication remains unverified. Existing live source history remains zero drops/leads; prior sources had zero actionable leads. No current real candidate or verified-free mint is established.
- Added `app/budget.py`, `/api/budget`, and a compact dashboard budget status. The shared cap is USD 5. Existing unresolved submissions are counted as unpriced liabilities; if any exist, available USD is `null` and new reservations fail closed. Reservations are atomic, require a recent HTTPS-sourced quote, prevent duplicate refs/over-cap, and support explicit settlement or a confirmed-unsubmitted release. The ledger does not sign, submit, or spend.
- Before the additive SQLite schema initialization, backup created at `data/backups/nft_hunter_pre_budget_20261115T165508Z.sqlite3`; backup integrity was `ok`. Post-migration integrity is `ok`; counts remain 22 scans, 16 observations, 0 campaign leads, 2 Core transactions, 0 wallet evidence rows, 0 budget-ledger entries. Both Core rows remain `SUBMISSION_UNKNOWN`.
- Budget API reports cap `$5.00`, confirmed spent `$0`, reserved `$0`, unknown liabilities `2`, available amount unknown, state `BLOCKED_UNKNOWN_LIABILITY`. This is accounting status only, not a claim that the shared budget is unused.
- Validation: `.venv` Python 3.14.10 full suite 200 passed; Node v26.13.1 26 passed. Budget tests cover unknown liabilities, quote freshness, duplicate prevention, cap enforcement, settlement/release, and concurrent reservations.
- No mechanism-specific third-party mint builder was added: no evidenced live candidate justifies one. Self-created Core asset code remains separate from third-party campaign support. No wallet actions, scans, scheduler, Telegram delivery, signing, broadcast, or spend occurred.

## Resume continuation — 2026-11-16

- Network recheck remains blocked at TCP 443: DNS resolves OpenSea (4 addresses), Python 3.14.10 raises WinError 10013, Node v26.13.1 raises EACCES, and PowerShell reports TCP failure. OpenSea API-key presence is configured, but no HTTPS/authentication request was made; no campaign cycle was run.
- One read-only Solana status request for both existing unknown signatures failed at socket permission (WinError 10013), before HTTP. No rows changed, no retry occurred.
- Added `app/solana/status_check.py` for user-side reconciliation: one request total, SQLite read-only, output masks signature/address/URL, no transaction-send capability. Command: `& '.\.venv\Scripts\python.exe' -B -m app.solana.status_check`.
- Focused status/budget tests: 10 passed. Final full suite: Python 204 passed; Node 26 passed. See the latest `SESSION_HANDOFF.md` entry.

## User-provided status result — 2026-11-16

- Local one-shot Solana RPC result: HTTP 200, one request for two signatures; both `NOT_FOUND_IN_RPC_HISTORY`, with null confirmation/slot/error fields.
- This is not proof of failure. Both rows stay `SUBMISSION_UNKNOWN`; no retry. Budget remains blocked by two unknown liabilities.
- Next: use the existing status endpoint to inspect each saved asset address and verify the Core program, owner and metadata URI. If unavailable or absent, retain unknown; do not release the budget.
- The old endpoint returned only `not_found_by_rpc`, so it did not distinguish asset absence from a failed asset RPC read. Updated API returns sanitized `asset_check_status` categories and leaves the lifecycle row `SUBMISSION_UNKNOWN` on read failure. Budget now counts every signed Core transaction lacking a settled/released ledger entry, independent of lifecycle label.
- Regression after this adjustment: Python 205 passed; Node 26 passed.
- User reran the updated endpoint: both saved assets returned `ASSET_ACCOUNT_NOT_FOUND_OR_WRONG_PROGRAM`; signatures remain absent from RPC history. This is not a failed-transaction receipt and the state remains `SUBMISSION_UNKNOWN`; no retry. Budget remains blocked.

## Independent reconciliation continuation — 2026-11-16

- The 32-character identifiers are internal preview IDs. The saved `signature` fields decode as 64-byte base58 transaction signatures. Full transaction identifiers and wallet addresses are omitted.
- User-reported `getSignatureStatuses(searchTransactionHistory=true)` returned HTTP 200 with null for both signatures. The old asset check returned a combined `ASSET_ACCOUNT_NOT_FOUND_OR_WRONG_PROGRAM`; it cannot distinguish an absent account from a different program owner. Neither result proves transaction failure.
- Hunter now requests documented `getTransaction` after a null status, then checks asset account existence and program owner separately. Read failures remain RPC unavailable; no absent-history outcome releases budget or triggers retry.
- Added append-only SQLite `core_asset_reconciliation_checks` with timestamped, sanitized evidence. Two records capture this runtime's bounded Solana attempt: two `getTransaction` calls plus one `getMultipleAccounts` call, all blocked before HTTP with `PermissionError`. Both original transaction rows remain `SUBMISSION_UNKNOWN`.
- OpenSea: one read-only upcoming-page request using configured key presence (secret value not read or logged) failed with `URLError` before HTTP. HTTP/authentication untested; 0 items, 0 normalized/saved leads. No campaign cycle.
- Tests: Python 207 passed; Node 26 passed. No wallet action, transaction, Telegram, signing, broadcast or spending occurred.
- Current local status endpoint rerun command is in `RUNBOOK.md`; use port 8767 after restarting the dashboard. It appends evidence and returns separate `asset_check_status` values. Never retry either transaction.

## Local reconciliation validation — 2026-11-16

- Port 8767 answered the dashboard route with HTTP 200, but its reconciliation responses used the old `not_found_by_rpc` field and returned `ASSET_READ_FAILED`. This is not the updated status behavior; do not treat the asset as absent. The API is reachable, but the updated app is not confirmed running on 8767.
- Started the current workspace code temporarily on port 8768, confirmed `/api/dashboard` returned HTTP 200, ran both saved read-only status endpoints, then stopped only that temporary process.
- Both current-code responses: `SUBMISSION_UNKNOWN`, `confirmation_status=rpc_unavailable`, `asset_check_status=RPC_UNAVAILABLE`, sanitized exception category `RuntimeError`, `confirmed_at=null`. No account data was returned, so neither `ACCOUNT_NOT_FOUND` nor `WRONG_PROGRAM_OWNER` can be reported. The scanner wraps the underlying transport cause; no HTTP status was received.
- The documented endpoint calls appended current timestamped unavailable evidence; original transaction rows remain 2/2 `SUBMISSION_UNKNOWN`. SQLite `integrity_check=ok`. Prior audit rows remain append-only; no transaction row was overwritten and no budget reservation was released.
- OpenSea auth check was skipped because Solana RPC connectivity did not succeed (as requested). The prior one-request OpenSea attempt remains `URLError` before HTTP, zero items/leads, authentication unknown; no full discovery cycle ran.
- Regression after the diagnostic change: 14 focused Python tests passed (`test_core_asset_execution_api`, `test_solana_status_check`). Node code was not changed in this continuation; last full suite: 26 passed. No signing, broadcast, retry, Telegram delivery, or spend.
- To use updated status code on port 8767, stop the stale dashboard from its owning terminal with Ctrl+C, then start via the `.venv` command above. No process was killed by this run.

## Validation result addendum — 2026-11-16 (latest)

- Port 8767 returned local dashboard HTTP 200, but both status responses used legacy `not_found_by_rpc` / `ASSET_READ_FAILED`; this is not the updated reconciliation response. The listener was left untouched.
- Updated workspace code was launched temporarily on 8768 and served HTTP 200. Both exact sanitized results: `status=SUBMISSION_UNKNOWN`, `confirmation_status=rpc_unavailable`, `asset_check_status=RPC_UNAVAILABLE`, `diagnostic_error_category=RuntimeError`, `confirmed_at=null`. No account result exists; neither account absence nor wrong owner can be claimed. RuntimeError is the scanner's sanitized wrapper; no Solana RPC HTTP result was received.
- Two timestamped checks appended. SQLite: integrity `ok`, two transaction rows, both `SUBMISSION_UNKNOWN`, six reconciliation audit rows total. The temporary 8768 server was stopped; the existing 8767 listener was not stopped or replaced.
- OpenSea authentication was not checked in this pass because the Solana connectivity condition did not pass. Earlier one-request test received `URLError`, no HTTP status, 0 drops/leads; no full campaign cycle.
- Final regression in `.venv` Python 3.14.10: 208 passed. Node v26.13.1: 26 passed.

## Local reconciliation recheck — 2026-11-16 (user-requested retry; read-only)

- Updated dashboard verified on port 8767: local `/api/dashboard` returned HTTP 200; status responses included the updated `rpc_history_unavailable` and distinct `asset_check_status` fields.
- Both saved records returned `SUBMISSION_UNKNOWN`, `confirmation_status=rpc_history_unavailable`, `asset_check_status=ACCOUNT_NOT_FOUND`, `diagnostic_error_category=null`, and `confirmed_at=null`.
- Interpretation: the history lookup did not return a transaction (`getSignatureStatuses` null and `getTransaction` null at confirmed commitment); the expected asset account was absent at the account read. Neither observation is a transaction failure receipt. No slot/receipt was returned, and no wrong-program-owner result was observed. Keep both records and budget reservations unresolved.
- SQLite integrity is `ok`; 2 transaction rows, both unknown. Two new timestamped append-only reconciliation entries recorded `RPC_HISTORY_UNAVAILABLE` + `ACCOUNT_NOT_FOUND`; no transaction row was overwritten.
- Since read-only RPC connectivity returned usable responses, one OpenSea upcoming-page request was made: API key presence true, one request, `URLError` before HTTP, no HTTP status, 0 items and 0 leads. Authentication remains unverified. No full campaign cycle was started.
- Regression: focused Python API/status tests 14 passed. Latest complete suites remain Python 208 and Node 26 passed. No signing, broadcast, retry, spending, Telegram, or wallet-setting change.

## Network recovery and live discovery check — 2026-11-16

- Diagnostics targeted only `api.opensea.io:443`; no firewall, endpoint security, TLS or proxy setting was changed.
- DNS succeeded: PowerShell resolved 2 A records; `.venv` Python resolved 4 addresses; Node resolved 4. No proxy variables/configuration were present in any client.
- TCP 443 failed in all clients before TLS/HTTP: Python `.venv` returned `PermissionError` / WinError 10013; Node returned `EACCES`; PowerShell `TcpClient` failed with a PowerShell `MethodInvocationException` wrapper. This isolates the observed failure to outbound TCP/socket permission. It does not identify whether Windows Firewall, endpoint security, or another host policy owns the block. TLS and API authentication were not reached.
- One bounded authenticated OpenSea `upcoming` GET was made using configured key presence (key value never displayed): one request, `URLError`, no HTTP status, zero items, zero normalized leads. Authentication result is unknown; no discovery cycle ran and nothing was persisted.
- App-level improvement: OpenSea transport errors now retain only sanitized underlying exception class, numeric OS code and a normalized state (`SOCKET_PERMISSION_DENIED`, `TCP_TIMEOUT`, or `TCP_CONNECTION_REFUSED`). Error text/URLs/secrets are not emitted. Fixture verifies WinError 10013 handling and redaction; this does not repair a host-level block.
- Alternative-source research: official HowRare docs expose a public but explicitly experimental drops calendar with date/time, website, claimed price and socials, but no mint contract; its existing adapter remains a lead source only. Magic Eden's documented launchpad-collections endpoint supports `offset` and `limit`, not a future-only filter; previously retrieved 100 records included 46 Solana items, all dated in the past. The Colony RSS is also already integrated and previously yielded 50 items with no actionable lead. The local database still has zero campaign leads. No additional source met the evidence bar for integration; no parser or verification gate was loosened.
- OpenSea docs describe `upcoming`, `featured`, and `recently_minted` drop queries and include contract/stage fields, so it remains the best existing campaign feed once authorized TCP connectivity is restored. API authentication is not validated until an HTTP response is received.
- Regression: `.venv` Python 3.14.10 full suite 209 passed; Node v26.13.1 suite 26 passed. No campaign cycle, signing, broadcast, wallet action, Telegram, retry, or spend.
- Sources reviewed: [OpenSea Drops API](https://docs.opensea.io/reference/get_drops), [HowRare public API](https://howrare.is/api), [Magic Eden launchpad collections API](https://docs.magiceden.io/reference/get_launchpad-collections), [Solana public RPC guidance](https://solana.com/docs/references/clusters).
- Safe next action: authorized administrator reviews outbound TCP 443 for the three local client processes. Do not disable protections or route around policy. When policy permits, make one authenticated OpenSea request; only on HTTP success should a single bounded campaign cycle be considered.

## Connectivity recovery attempt — 2026-11-16

- Read-only probes to `api.opensea.io`, `www.google.com`, and `github.com`, port 443, from PowerShell and `.venv` Python. DNS succeeded for all six client/host combinations (PowerShell A-record counts: 2, 8, 1; Python: 2, 8, 1). No proxy configuration was present.
- TCP failed for all six combinations before TLS/HTTP. PowerShell reported `SocketException` native code 10013; Python reported `PermissionError`, WinError 10013. This is outbound socket/TCP permission denial across destinations, not an OpenSea-only issue. TLS, HTTP, and API authentication were not reached. Firewall versus endpoint-security ownership cannot be identified from these probes; no controls were changed.
- Since TCP remained blocked, no OpenSea API-key request and no campaign cycle were run in this attempt. No discovery leads were added; the local campaign lead count remains 0. Do not repeat these same probes until an administrator reports a policy change.
- SQLite read-only check: integrity `ok`, 2 Core transaction rows, both `SUBMISSION_UNKNOWN`; 2 unresolved signed transaction liabilities remain. Budget ledger has 0 explicit rows, and available budget must not be inferred from that; the unknown-liability guard remains in force. No transaction or budget row was changed.
- Relevant source regression: Python `tests.test_opensea_drops` + `tests.test_campaign_sources`: 13 passed. No live cycle, wallet operation, transaction retry, signing, broadcast, Telegram, or spend.
- Next action: authorized administrator reviews outbound TCP 443 policy for the local PowerShell and Python processes without disabling protections. Retry OpenSea only after a confirmed policy change.

## Connectivity recovery follow-up — 2026-11-16

- User's VS Code PowerShell reported TCP 443 PASS to `api.opensea.io` and `www.google.com` over IPv6. Follow-up one-shot checks: Python TCP 443 PASS to `www.google.com` and `github.com`; PowerShell TCP 443 PASS to `github.com`.
- One bounded authenticated request using the configured OpenSea key was sent by the project adapter. It received **HTTP 400** for `type=upcoming`; `requests=1`, no drops or normalized leads. The request reached the OpenSea HTTP application, so TCP/TLS connectivity works in this runtime. This response does not establish API-key authentication; the body was not surfaced by the adapter. No second request was made.
- No campaign cycle ran because OpenSea authentication was not confirmed. Zero live candidates/leads were found or persisted by this attempt.
- Python source tests: `tests.test_opensea_drops` and `tests.test_campaign_sources`: 13 passed. Database check: SQLite integrity `ok`; campaign leads 0; two Core transactions both `SUBMISSION_UNKNOWN`; budget ledger unchanged (0 ledger rows; two unknown signed liabilities remain blocking). No transaction or budget record changed.
- Do not repeat the OpenSea request or start discovery until the HTTP 400 is understood through the API provider/configuration or a separately authorized next check. No network/security settings were changed; no signing, broadcast, retry, or spend.

## OpenSea live discovery activation — 2026-11-16

- Added `app/discovery/opensea_live_check.py`, a local preflight intended for the user's normal VS Code terminal (not the Codex offline sandbox). It makes one GET to the existing official `/api/v2/drops` endpoint with the adapter's first-page params (`type=upcoming`, `limit=50`, plus `OPENSEA_CHAINS` only when configured), `x-api-key`, an 8-second timeout, and a 256 KiB response cap. The API key is read from `.env`/environment and never printed.
- The report includes endpoint, non-secret parameters, request count, HTTP status, content type, response size/time, response schema, drop count, and a truncated sanitized error body. For JSON errors it redacts credential-named fields; for text errors it redacts the configured key and common credential assignments. 401 is reported as rejected; 403 as key accepted but forbidden; 400 remains request failure and does not prove key validity.
- With `--run-cycle-on-success`, the existing bounded read-only `run_campaign_discovery_cycle` runs only after preflight returns HTTP 200 and valid JSON containing a `drops` array. It saves genuine source leads and reports source metrics; it does not call wallet verification, send Telegram, sign, submit, or broadcast. Zero/missing API price remains unverified.
- Official docs confirm `GET https://api.opensea.io/api/v2/drops`, `type=upcoming`, `limit` 1–100, optional comma-separated `chains`, `x-api-key`, and 400/401/403 response distinctions: https://docs.opensea.io/reference/get_drops and https://docs.opensea.io/docs/mint-from-a-drop. The existing adapter's URL, auth header and limit 50 match documented syntax; no production endpoint/parser change was justified without the actual 400 body. The local diagnostic omits no configured filter, so it reproduces the current first request and exposes whether the chain filter is implicated.
- Exact PowerShell command from `D:\NFT_hunter`:
  `& '.\.venv\Scripts\python.exe' -B -m app.discovery.opensea_live_check --run-cycle-on-success`
  This command is for the user's normal VS Code terminal. It is not run here because the Codex sandbox cannot access that terminal and has the explicit offline outbound block.
- Live status: not yet executed in the user's terminal. No actual HTTP status, OpenSea authentication result, drops, saved leads, supported-chain findings, or free-mint candidates are claimed. After running, share the sanitized JSON output only; it contains no API key. If it returns 400, the output's sanitized body and request params are the next diagnosis. If it returns valid 200, the same command runs one bounded campaign cycle and reports its persisted results.
- Validation: `.venv` Python 3.14.10 full suite: 214 passed; Node v26.13.1: 26 passed. New focused OpenSea diagnostic tests: 5 passed. No live network request or campaign cycle was run from this sandbox.
- Both legacy Solana transactions remain `SUBMISSION_UNKNOWN`; budget reservations remain blocked/unreleased. No wallet operation, signing, broadcast, transaction retry, Telegram delivery, spend, or system-security change occurred.

### OpenSea diagnostic output refinement — 2026-11-16

- The preflight report now includes top-level/first-drop schema keys, per-chain counts and up to three bounded sample drops with only documented public fields (project slug/name, chain, contract, mint status, stage schedule and claimed price). HTTP error output includes JSON field names and a 1,200-character sanitized body excerpt; the configured API key and credential fields are redacted.
- The exact local command remains `& '.\.venv\Scripts\python.exe' -B -m app.discovery.opensea_live_check --run-cycle-on-success`. It makes one exact adapter-shaped first request and only runs one existing campaign cycle after valid 200 JSON schema. It does not invoke campaign wallet verification.
- Final validation after this refinement: Python 3.14.10 full suite 214 passed; Node v26.13.1 suite 26 passed. No live request was made in the Codex sandbox. Live counts/authentication remain pending the user's local command output.

## OpenSea HTTP 400 root cause and chain-filter correction — 2026-11-16

- User supplied one bounded response from the official Drops endpoint: HTTP 400, JSON (455 bytes), one request, 10.3 seconds. Sanitized body says `Unrecognized chain: bnb,ethereum,base,solana,robinhood` and lists recognized slugs including `bsc`, `ethereum`, `base`, `solana`, and `robinhood`. Therefore the error was the `chains` filter, not evidence of a bad API key. Authentication remains technically unconfirmed until a successful HTTP response; 400 is documented as request validation failure, while 401 is Unauthorized.
- Implemented the narrow correction: OpenSea client now maps Hunter aliases `bnb`→`bsc`, `solana-mainnet-beta`→`solana`, and `robinhood-chain`→`robinhood`; known recognized slugs pass through unchanged. Unknown configured slugs are rejected locally before network I/O. Discovery response mapping still normalizes API `bsc` to BNB chain ID 56 and `solana` to Solana mainnet-beta.
- The local diagnostic now applies the same normalization. With the current configured five chains, the request is `type=upcoming&limit=50&chains=bsc,ethereum,base,solana,robinhood`. Re-run the local preflight/cycle command below; this sandbox did not make another live request.
- Exact PowerShell command from `D:\NFT_hunter`:
  `& '.\.venv\Scripts\python.exe' -B -m app.discovery.opensea_live_check --run-cycle-on-success`
  It makes exactly one preflight request. Only after HTTP 200 plus a valid Drops JSON schema does it invoke one existing bounded read-only campaign cycle. No wallet verification, Telegram delivery, signing, or broadcast is invoked.
- Live status after correction: pending user-side rerun. No new actual drops, saved leads, supported-chain activity, or free-mint candidates are claimed yet. Price values from the OpenSea feed remain unverified claims.
- Regression after alias correction: Python 3.14.10 full suite 217 passed; Node v26.13.1 26 passed. No live request was issued by this sandbox. Both `SUBMISSION_UNKNOWN` Solana transactions and budget restrictions are unchanged.

## User-run OpenSea preflight and campaign-cycle result — 2026-11-16

- The user's normal VS Code run sent one authenticated Drops preflight using normalized filter `bsc,ethereum,base,solana,robinhood`. It returned HTTP 200, `application/json`, 1,996 bytes in 1,169.4 ms; the key was accepted for this endpoint. The response had a valid `{drops,next}` schema and two records, both Ethereum.
- Live response samples: `Sections by Grant Yun`, contract `0xe9d9d14d858cf9cda2d320493726af7fc907309a`, not currently minting, `signed_presale`, advertised price `0`, stage Nov 18 2026 17:20–17:39 UTC. This is only an OpenSea claim; zero price is not on-chain verified, and signed-presale eligibility is unknown. `Nocturnal`, contract `0xa99ac8b5053bc2ced4c4c83bb8c6153018f84fc8`, marked currently minting, public-sale stage, advertised `0.05 ETH`, Nov 15 16:00–Nov 18 16:00 UTC; it is a paid claim, not a free-mint candidate.
- The same command conditionally ran one existing campaign cycle. Other source metrics: The Colony 50 items / 0 leads; HowRare 0 / 0; Magic Eden 100 items, 46 Solana / 0 leads. OpenSea made 3 requests (one per drop type) but the cycle recorded `AttributeError`, 0 items processed, 0 leads saved, 0 verified. Overall cycle status `partial`; no candidate was persisted from this cycle.
- Root cause of the OpenSea cycle `AttributeError` is not yet conclusive from the summarized output because the cycle records the exception class only. The adapter now tracks a sanitized `failure_phase` and campaign metrics expose it, so one rerun will identify request/decode/normalization/persistence/health phase without exposing exception text or credentials. A fixture built from the two supplied response records parsed and persisted both as unverified leads; it did not reproduce the live error, which may involve another page/type or local runtime state.
- No parsing/security gates were weakened. The claimed-zero Sections presale remains unverified; Nocturnal is claimed paid. No source lead was saved from the user-reported cycle. No wallet transaction, signing, broadcast, retry, Telegram delivery, or spend occurred. The two Solana `SUBMISSION_UNKNOWN` records and budget restrictions remain unchanged.
- After restarting with current workspace code if necessary, rerun the same local command once to obtain `failure_phase`: `& '.\.venv\Scripts\python.exe' -B -m app.discovery.opensea_live_check --run-cycle-on-success`. It will make one preflight and then one bounded cycle only on valid preflight; share only the sanitized JSON.
- Regression: Python 3.14.10 full suite 219 passed; Node v26.13.1 26 passed. Added a fixture matching the two supplied Drops records; it verifies parsing, price-claim classification, chain normalization, and SQLite persistence without calling the live network.

## OpenSea recently-minted item failure handling — 2026-11-16

- The user's second local cycle reproduced the OpenSea error and narrowed it to `failure_phase=normalize_items:recently_minted`, after all three type requests were made. The report did not contain the offending record or underlying message, so the exact malformed field/value is still unknown.
- Updated the Drops adapter to isolate a per-record `AttributeError` during normalize/deduplicate. It now records only the drop type, item index, field names, exception category and phase; it skips that malformed record and preserves other valid API leads as unverified. The source can return `partial` with successfully parsed records instead of discarding the whole multi-type result. No exception message or credential is included.
- Added a regression using an injected malformed recently-minted record to prove valid leads survive and remain unverified. It also checks the reported item index/keys and secret-safe error output.
- Run once more from the normal VS Code terminal to verify current live persistence and see any offending item index:
  `& '.\.venv\Scripts\python.exe' -B -m app.discovery.opensea_live_check --run-cycle-on-success`
  Only share the sanitized JSON output. The cycle still stays read-only and skips wallet verification/signing/submission.
- No new live candidates or database inserts are claimed from the reproduced failing cycle. The preflight's two Ethereum records remain unverified claims: Sections presale price 0 with unknown eligibility; Nocturnal price claim 0.05 ETH. No confirmed free mint.
- Tests after the item-isolation change: Python 3.14.10 full suite 220 passed; Node v26.13.1 26 passed. No live request in this execution. Solana unknown submissions and budget reservations remain untouched.

## OpenSea cycle attachment analysis and error-location instrumentation — 2026-11-16

- The user attachment contains 37 sanitized `AttributeError` records from `recently_minted` normalization, indexed at individual items. Every record has the same top-level Drops field names; the attachment does not include a source line or raw error, so the failing operation is still not identified.
- The attached `opensea_persisted_leads` list contains 31 records observed/upserted in that cycle (this is not the count of newly inserted rows). Most mint-window dates are historical. The list includes the already observed Sections by Grant Yun zero-price signed-presale claim (Nov 18) and Nocturnal paid claim (0.05 native ETH; OpenSea marked minting). Neither is verified free; no wallet eligibility or on-chain price proof exists. The excerpt does not expose the cycle-level `new_leads_saved` count, so that count remains unknown.
- Added sanitized `failure_location` (source basename and line number only) to each per-record `AttributeError` diagnostic. It reveals the shared code location without exception text, response contents, or credentials. The next local cycle should isolate the precise failing expression while preserving valid lead processing.
- Re-run from the user's normal VS Code terminal only if continuing diagnostics: `& '.\.venv\Scripts\python.exe' -B -m app.discovery.opensea_live_check --run-cycle-on-success`. It is still one preflight plus one bounded cycle conditional on success. Share only sanitized output including `failure_location`.
- Validation after instrumentation: Python 3.14.10 full suite 220 passed; Node v26.13.1 26 passed. No live network call was made here. No verified-free candidate, transaction, signing, broadcast, retry, Telegram delivery, or spend. The two Solana `SUBMISSION_UNKNOWN` rows and budget restrictions are unchanged.

## Root cause fix: recent Drops with no stage — 2026-11-16

- The latest attachment confirms 37 `recently_minted` per-record failures at `opensea_drops.py:161`. That line accessed `stage.get("price_currency_address")` without guarding `stage`; many completed/recently-minted records have both `active_stage` and `next_stage` null. This is the concrete `AttributeError` cause.
- Fixed the field to return `None` when stage is absent. Added a regression test ensuring a no-stage recently-minted record normalizes with price `UNKNOWN`, no mint date, and `DEFERRED_MISSING_EVIDENCE`.
- Attachment summary: 31 OpenSea records were present in the cycle's observed/persisted list; this is not a count of newly inserted records, and no cycle-level insert count was available in the excerpt. Most listed dates are historical. Sections by Grant Yun remains a future signed-presale, zero-price API claim with eligibility unknown; Nocturnal remains an API-reported 0.05 ETH active/public claim. Neither is a verified free mint.
- Full tests after fix: Python 3.14.10: 221 passed; Node v26.13.1: 26 passed. No live call was made from this runtime after the fix.
- Local live verification command: `& '.\.venv\Scripts\python.exe' -B -m app.discovery.opensea_live_check --run-cycle-on-success`. It performs one preflight and conditionally one bounded cycle. Share sanitized output to confirm the fixed live path and exact new-insert count.
- No wallet action, signing, broadcast, transaction retry, Telegram delivery or spend. Both Solana `SUBMISSION_UNKNOWN` records and budget reservations remain unchanged.

## Latest OpenSea cycle excerpt and Robinhood chain normalization — 2026-11-16

- Latest user attachment shows no remaining `AttributeError` or `failure_location` entries after the absent-stage guard. It contains 95 OpenSea persisted-list entries (the attachment begins mid-record, so one project name is omitted): 64 price `UNKNOWN`/no mint-window entries, 29 API-claimed paid entries, and 2 API-claimed free entries. These are observed/upserted records, not a count of new inserts; the cycle summary with new-vs-duplicate counts is not present in the attachment.
- The two zero-price claims are Koi No Yokan (mint window 2026-09-15, expired) and Sections by Grant Yun (signed presale scheduled 2026-11-18 17:20–17:39 UTC, future). Sections remains `CLAIMED_FREE_UNVERIFIED`; wallet/allowlist eligibility and on-chain price are unknown. Nocturnal is an OpenSea-claimed 0.05 native ETH public stage and is paid. Do not mint or describe any as verified free.
- The response includes a Robinhood contract stored under `opensea:robinhood`. Added chain mapping `robinhood` → Hunter chain ID `4663`; future normalized records now use the target chain ID. Existing stored rows were not migrated or deleted, so a later cycle could identify an old alias row as distinct; handle any deduplication as an additive/safe migration if needed.
- Regression after Robinhood mapping: Python 3.14.10 full suite 222 passed; Node v26.13.1 26 passed. Focused OpenSea/source suite 26 passed.
- The attachment omits complete cycle/source metrics and does not establish the exact number of new rows. No further live cycle, wallet verification, Telegram delivery, signing, broadcast, retry, or spend was performed. Both Solana `SUBMISSION_UNKNOWN` records and budget reservations remain unchanged.

## OpenSea wallet-scoped verification milestone — 2026-11-16

### Official API findings

- Drop details: https://docs.opensea.io/reference/get_drop_by_slug . It returns chain, contract, active/next stages, stage schedule and supply. Details and stage price are OpenSea data claims.
- Wallet eligibility: https://docs.opensea.io/reference/get_drop_eligibility . A wallet-specific request needs the API key plus an OpenSea wallet JWT with `read:eligibility`; OpenSea stage eligibility does not itself establish that the stage is currently active.
- Mint transaction builder: https://docs.opensea.io/reference/build_drop_mint_transaction . The endpoint returns unsigned `to`, `data`, `value`, chain and stage metadata; it does not sign or broadcast. Current documentation describes SeaDrop V1 ERC-721 and V2 ERC-1155 support, with restrictions. Hunter's narrow builder validator currently accepts only Ethereum SeaDrop V1 public `mintPublic` calldata, quantity one, native zero price. Signed-presale, allowlist proof, ERC-1155, token-priced stages, unknown calldata, wrong recipient/contract/value/stage and non-canonical targets remain blocked.
- Official SeaDrop source/deployment reference: https://github.com/ProjectOpenSea/seadrop . Ethereum SeaDrop V1 deployment: `0x00005EA00Ac477B1030CE78506496e8C2dE24bf5`.

### Implemented in Hunter

- Added `app/discovery/opensea_drop_verifier.py`: maximum three no-retry OpenSea requests per check; bounded timeout/response size; sanitized errors; exact chain/contract/stage matching; per-stage wallet eligibility; configured wallet-address pinning; five-minute evidence expiry; fail-closed response schema handling.
- Added read-only dashboard endpoint `POST /api/campaign-leads/{lead_id}/opensea-wallet-check`. It validates the saved OpenSea lead, Ethereum chain, selected provider/address and Binance wallet session where applicable. It checks contract bytecode, ERC-165 standard and public Sourcify verification when an Ethereum RPC is configured. It never signs, sends, executes, or uses the transaction execute APIs.
- Added a campaign table wallet selector and “Check eligibility” action. MetaMask account/network is refreshed before submission; Binance address/network is checked server-side. Results show masked wallet identity and are kept wallet scoped.
- Added strict public SeaDrop V1 mint payload validation and read-only `eth_call`, gas estimate, gas-price, and balance arithmetic helpers. These helpers are gated by active stage, wallet eligibility, native zero price claim, verified contract source, ERC-721 evidence and existing LOW risk. No gate lowers risk. Signed presale is explicitly unsupported by this builder.
- Added `OPENSEA_WALLET_JWT` and `OPENSEA_WALLET_ADDRESS` placeholders to `.env.example`. Never paste a token into chat or logs. Store only a short-lived token with the `read:eligibility` scope and pin it to its exact wallet address in local `.env`.
- Wallet evidence is appended to `wallet_verification_evidence`, includes check time/expiry/stage/source/API outcomes, and is exposed in campaign results with masked addresses. API price claims are not written into verified price columns. Collection-level risk/price and source evidence are not promoted by one wallet check.

### Sections by Grant Yun evaluation

- Existing saved lead: Ethereum chain ID 1, contract `0xe9d9d14d858cf9cda2d320493726af7fc907309a`, status `BLOCKED_UNSUPPORTED`, risk `HIGH`, claimed zero price remains unverified.
- Previously supplied OpenSea response claimed a `signed_presale` stage for 2026-11-18 17:20–17:39 UTC; this was not refreshed during this implementation. As of the workspace date 2026-11-16, it is an upcoming stage, not a current mint. The implemented builder intentionally refuses signed-presale calldata.
- Local configuration inspection confirmed `OPENSEA_API_KEY` is present, but no value was displayed. `OPENSEA_WALLET_JWT` and its pinned wallet address are not configured. Therefore no wallet-specific eligibility request and no live OpenSea verification occurred in this runtime. No transaction preview was generated for this candidate.
- Until a wallet-scoped token is configured and the live stage is rechecked, wallet eligibility is `UNKNOWN`; the OpenSea zero-price claim is not `VERIFIED_FREE`; gas/total cost remain unknown. Shared $5 budget remains unavailable while both historical Solana submissions remain `SUBMISSION_UNKNOWN`.

### Validation and safety

- Complete regression suites after implementation: Python 3.14.10 `.venv` `python -B -m unittest discover -s tests` — 232 passed; Node `node --test tests/*.test.mjs` — 27 passed, 0 failed. The output included expected mocked RPC failures and unrelated external-service fixture ResourceWarnings; all tests passed.
- SQLite integrity was read-only checked as `ok`; both existing Solana records remain `SUBMISSION_UNKNOWN`. No database migration, source lead mutation, wallet action, Telegram delivery, signing, broadcast, retry, spend, or service restart occurred.
- Next user action for wallet-specific OpenSea eligibility: create a short-lived OpenSea wallet JWT with only `read:eligibility`, set `OPENSEA_WALLET_JWT` and `OPENSEA_WALLET_ADDRESS` locally in `.env` to the matching selected public address, restart the dashboard, and use **Check eligibility** on the Sections lead. This does not authorize any mint. A stage-status refresh is still required when the announced window arrives.

### Final execution note — 2026-11-16

- This runtime made no live OpenSea HTTP request and no live Ethereum RPC request. OpenSea API-key configuration presence was confirmed without reading its value. No `OPENSEA_WALLET_JWT` or pinned wallet address was configured, so wallet-specific eligibility for Sections remains untested and unknown.
- SQLite was opened read-only for final verification: `integrity_check=ok`; exactly two `SUBMISSION_UNKNOWN` Core asset transaction rows remain; wallet verification evidence row count remains zero. No production DB records were changed in this milestone.
- Changed code: `app/discovery/opensea_drop_verifier.py`, `app/dashboard/api.py`, `app/database.py`, `app/dashboard/static/dashboard.js`, `app/dashboard/static/dashboard.css`, `.env.example`; tests: `tests/test_opensea_drop_verifier.py`, `tests/test_dashboard_api.py`, `tests/dashboard_campaign_ui.test.mjs`.
- No signing, broadcast, retry, mint, Telegram delivery, asset spending, dependency installation, scan, scheduler start, or existing service restart occurred. No API build request is made for Sections while its stage is upcoming; even once active, its `signed_presale` is unsupported by this initial SeaDrop public-mint adapter and its saved risk remains HIGH.
