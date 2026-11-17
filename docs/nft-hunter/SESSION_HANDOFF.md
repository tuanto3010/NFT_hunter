# NFT Hunter Session Handoff — R2 Safe Batch

Updated: 2026-11-15 23:37 Asia/Saigon

## Completed

- Read the v2.1/R1 master plan and copied the exact document to this handoff folder as `MASTER_PLAN.md`.
- Captured the pre-change baseline: `.venv` Python 3.14.10; 180 Python tests and 25 Node tests passed.
- Ran one configured BNB RPC diagnostic. `eth_chainId` was denied by local socket policy (`WinError 10013`); there was no HTTP status. Other RPC URLs were not configured.
- Added an official OpenSea Drops adapter and campaign-cycle integration. Fixture tests pass. Live validation is blocked: no `OPENSEA_API_KEY`; no live OpenSea request or campaign claim was made.
- Added provider/chain/contract/stage/wallet/block/time-scoped verification evidence persistence and integrated the generic EVM API and CLI verification paths.
- Created a SQLite backup before live additive migration. Integrity check passed; 22 scan history rows, 16 observations, 0 campaign source leads, 2 transaction rows, and 0 evidence rows were preserved. The two `SUBMISSION_UNKNOWN` rows remain unchanged.
- No scanner, scheduler, wallet, Telegram sender, or transaction execution was run.

## Changed files

- Added: `app/discovery/opensea_drops.py`, `tests/test_opensea_drops.py`, `tests/test_wallet_verification_evidence.py`.
- Updated: `app/discovery/campaign_feed.py`, `app/database.py`, `app/dashboard/api.py`, `app/main.py`, `tests/test_campaign_sources.py`, `tests/test_dashboard_api.py`, `.env.example`.
- Added docs: `docs/nft-hunter/MASTER_PLAN.md`, `PROJECT_STATE.md`, `TASK_BOARD.md`, `RESEARCH_EVIDENCE.md`, `RUNBOOK.md`, `SESSION_HANDOFF.md`.
- No new Python/npm dependencies. No Git metadata exists in this workspace copy.
- OpenSea API key is a runtime configuration dependency only; presence was confirmed after the user added it, and its value was never logged. Python adapter uses only the standard library.

## Final validation

- Final Python: `& '.\.venv\Scripts\python.exe' -B -m unittest discover -s tests` — 193 tests passed in 11.369 seconds.
- Final Node: `node --test tests/*.test.mjs` — 25 tests passed; 0 failed.
- Focused R2 suite: 43 tests passed; OpenSea-specific fixture/migration/campaign-source suite passed.
- Baseline before edits: 180 Python tests passed; 25 Node tests passed.
- Exact one-shot BNB diagnostic: `BnbRpcDiagnostics.from_env().test_once()`; state `NETWORK_BLOCKED`, sanitized WinError 10013, no HTTP status.
- Live OpenSea: the user added the key; one bounded campaign cycle attempted the request, but OpenSea and the existing feeds returned `URLError` without HTTP responses. Zero records/leads. The key remains private and its validity is unverified.
- `git` status unavailable: workspace directory is not a Git repository (`.git` absent).
- `git diff --check` is not applicable without Git metadata; files were inspected and the full suites passed.

## R3 continuation — connectivity only

- Read this handoff and `RUNBOOK.md` before testing.
- Project interpreter: Python 3.14.10. OpenSea API key presence is true; the value was not read back or displayed. Python proxy settings: none.
- `api.opensea.io` DNS: PASS, 4 unique addresses.
- Python TCP 443: FAIL, `PermissionError` / WinError 10013. PowerShell `Test-NetConnection`: FAIL, `TcpTestSucceeded=false`, no remote/source address.
- TLS and HTTP were not reached. R3: HTTP status N/A, API authentication untested, 0 drops retrieved, 0 leads saved (no campaign request was sent). The previous cycle's `URLError` does not prove the key is invalid.
- Campaign cycle was not run in R3 because the preflight established TCP socket denial. No source request, wallet action, scan, scheduler, Telegram delivery, signing, broadcast, or spend occurred in R3.
- Corrective action: ask the authorized administrator to review outbound TCP 443 policy for VS Code/Python and PowerShell; do not disable firewall or antivirus. Once authorized, rerun the bounded campaign cycle once and capture actual OpenSea status/counts.

All current progress documents are updated for the R3 diagnosis. `MASTER_PLAN.md` retains the original R1 plan and includes a dated execution addendum; no secrets or network-setting changes were made.

R3 offline code change: OpenSea source metrics now keep successful HTTP status codes per drop type/page; a fixture asserts that 200 is visible. No live request was made. Run the final regression suites before the next checkpoint.

## Next eligible task

The key is configured, but this process cannot reach the feed hosts (`URLError`). Next, use the user's VS Code Python 3.14.10 runtime only if it has authorized outbound HTTPS, to run one bounded source check. Do not retry from the restricted runtime. Do not infer eligibility or free price from the Drops API. Only then select a candidate for the existing generic verifier. Leave transaction execution and the two old unknown submissions untouched.

## Autonomous mission checkpoint — 2026-11-16

- Reviewed plan, state, task board, research evidence, runbook, source, tests and SQLite state. No `.git` metadata is present.
- Cross-runtime transport check to `api.opensea.io`: DNS passed (4 addresses); Python TCP 443 failed with WinError 10013, Node TCP with EACCES, and PowerShell TCP test failed. No proxy configured. TLS/HTTP/authentication were not reached; OpenSea API-key presence is configured but validity remains unknown. No campaign cycle was run after the TCP preflight failed.
- Previously reported source evidence: Colony RSS 50 items and no actionable campaigns; Magic Eden 100 records, with all 46 Solana records past their launch dates; HowRare produced no usable records. No current real campaign or verified-free opportunity is evidenced.
- Implemented shared USD 5 accounting and read-only `/api/budget`. Backup before additive migration: `data/backups/nft_hunter_pre_budget_20261115T165508Z.sqlite3`, integrity `ok`. Post-migration counts: 22 scans, 16 observations, 0 campaign leads, 2 Core transactions, 0 wallet-evidence rows, 0 budget entries. Integrity `ok`. Both Core rows remain `SUBMISSION_UNKNOWN`; not queried or retried.
- Budget status: `$5.00` cap; `$0` confirmed spend / `$0` reserved ledger amount; 2 unknown liabilities; available USD unknown, not asserted to be `$5`; new reservations blocked until reconciliation. Accounting does not sign, submit or spend.
- Validation: `.venv` Python 3.14.10 full suite 200 passed; Node v26.13.1 suite 26 passed. No dependencies installed. No scan, wallet call, Telegram delivery, signature, broadcast or spend occurred.
- Resume: have authorized Windows/endpoint-security administration review outbound TCP 443 policy for local Python, Node and PowerShell. After connectivity is permitted, run one bounded campaign cycle and inspect actual source metrics. Only implement a mechanism-specific builder when a real campaign provides sufficient source and on-chain evidence.

## Resume continuation — 2026-11-16

- Rechecked `api.opensea.io`: DNS passed (4 addresses), but TCP 443 still fails in this runtime across Python (WinError 10013), Node (EACCES), and PowerShell (`TcpTestSucceeded=false`). OpenSea key presence remains true, value hidden; no HTTPS/auth request or campaign cycle was made.
- Attempted one read-only Solana `getSignatureStatuses` request containing both persisted `SUBMISSION_UNKNOWN` signatures using the existing public mainnet default. It failed before HTTP with `PermissionError` / WinError 10013. No signature, URL, wallet address or credential was displayed. The two database rows were not modified and no retry/broadcast occurred.
- Added `app/solana/status_check.py`, a reusable one-shot local command: `& '.\.venv\Scripts\python.exe' -B -m app.solana.status_check`. It reads signatures from SQLite read-only, submits one status request total, prints sanitized states only, and has no submission path. `NOT_FOUND_IN_RPC_HISTORY` is explicitly not treated as failure.
- Added tests in `tests/test_solana_status_check.py` covering a single RPC call for multiple signatures, no request for invalid/no-op cases, sanitized transport errors, and read-only DB loading. Focused tests: 10 passed (including budget tests). Final full suites: Python 204 passed; Node 26 passed.
- Next: have the user run the one-shot status command in their authorized VS Code runtime if RPC access works. Keep both records unresolved otherwise. For discovery, await authorized TCP 443 resolution before a bounded source cycle.

## User-provided Solana status result — 2026-11-16

- User ran the local one-shot status utility. Reported `HTTP 200`, `request_count=1`, `unknown_records=2`; both items were `NOT_FOUND_IN_RPC_HISTORY` with no slot, confirmation state, or execution error.
- This establishes only that the queried RPC history did not return either signature. It does not prove the transactions failed or were never processed. Both database records remain `SUBMISSION_UNKNOWN`; do not retry.
- Next safe check: call the existing read-only Hunter status endpoint for each saved preview ID. When the signature status is null, its code also inspects the saved Metaplex Core asset address and verifies Core program ownership, asset owner, and metadata URI. Only if the RPC can verify that asset should the local status change to the corresponding signature-unavailable asset-verified state. If unavailable, preserve unknown.
- Budget remains `BLOCKED_UNKNOWN_LIABILITY`; do not infer available budget from `NOT_FOUND_IN_RPC_HISTORY`.
- The supplied status response came from the prior dashboard process and had no asset-check category. Updated `core_asset_status` now returns sanitized `asset_check_status` values while preserving `SUBMISSION_UNKNOWN` on read failures. Budget accounting now treats every saved signed Core transaction as a liability unless an explicit ledger record settles/releases it; a status change such as `OWNERSHIP_UNVERIFIED` cannot make the budget appear available.
- Focused core-lifecycle/budget tests passed; final regression after these changes: Python 205 passed, Node 26 passed.

## User-provided asset verification result — 2026-11-16

- After restarting the dashboard, the user reran both read-only status endpoints. Both returned `SUBMISSION_UNKNOWN`, `not_found_by_rpc`, and `ASSET_ACCOUNT_NOT_FOUND_OR_WRONG_PROGRAM`.
- No saved asset could be verified as a Metaplex Core account at either expected asset address. The status category combines absent account and wrong program owner; it is not an execution failure receipt. Keep both transaction rows unresolved and do not retry.
- Shared budget remains blocked because neither uncertain signed transaction has an independently reconciled cost/outcome. No funds are declared available from this result.

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

## Interactive Windows network diagnosis — 2026-11-16

- Read-only diagnostics ran in the Codex execution environment, not the user's interactive VS Code window: process identity was `MSI\CodexSandboxOffline`, not elevated (`Administrator=false`). The CUA bridge could not access the VS Code UI, so no command was run inside the user's actual terminal. User-provided PowerShell `Test-NetConnection` results for `api.opensea.io:443` and `www.google.com:443` reported `TcpTestSucceeded=True` over IPv6.
- Effective Windows Firewall profiles (readable in this process): Domain, Private, and Public are enabled, each with `DefaultOutboundAction=Allow` and local rules allowed. An explicit active outbound block named `codex_sandbox_offline_block_outbound` applies to any program, any protocol/port, and all non-loopback IPv4/IPv6 destinations. Separate named rules block loopback TCP/UDP. This is direct evidence for why Codex sandbox socket attempts can return WinError 10013; it is a sandbox-wide policy rule, not an OpenSea-specific application rule. It must not be removed or bypassed.
- The blocking rule's owner/management provenance was not independently queried; its name and scope identify the Codex offline sandbox policy. Do not attribute this to the user's normal Windows firewall configuration. The user's own PowerShell TCP results indicate their normal terminal path can reach the tested hosts.
- `BFE` (Base Filtering Engine), `MpsSvc` (Windows Defender Firewall), `WinDefend`, `WdNisSvc`, and `MDCoreSvc` were running. Security Center registered Microsoft Defender Antivirus. This is not proof that no additional endpoint product is installed. No matching third-party VPN/filter drivers appeared in the bounded visible inventory; driver inventory can be restricted.
- Proxy checks from the prior local diagnostic record showed no proxy environment variables, WinINET proxy disabled, and WinHTTP direct. WFP/security event log access was denied; no event-level attribution is available. This run did not change firewall, antivirus, VPN, proxy, service, or network settings and did not repeat endpoint probes.
- OpenSea API authentication and a discovery cycle were not tested in this diagnostic pass. A prior bounded adapter request reached OpenSea and returned HTTP 400; that establishes transport only, not successful authentication. Diagnose the HTTP 400 at the application/API request layer before starting discovery.
- No wallet operation, signing, broadcast, retry, Telegram delivery, or spending occurred. The two Solana `SUBMISSION_UNKNOWN` records and their budget restrictions remain unchanged.
- Safe next action: use the user's normal VS Code terminal for the existing bounded OpenSea request after resolving the HTTP 400 request/configuration issue. Do not alter the Codex sandbox firewall rule or disable protections. No administrator elevation was used; WFP audit logs remain unavailable without permissions.

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

## OpenSea live eligibility integration test — 2026-11-16

- Official OpenSea authentication documentation was reviewed. The Drops eligibility endpoint requires both `X-API-KEY` and `Authorization: Bearer <wallet JWT>`; its documented scope is `read:eligibility`. OpenSea's supported user flow is Settings → Developer, create a minimally scoped short-expiry token. If the UI returns a scoped PAT, official docs require exchanging that PAT at `/api/v2/auth/tokens/exchange` for a short-lived wallet JWT. Hunter must never use a PAT as the bearer token and must not create/extract a token by signing through Hunter.
- Current OpenAPI schema for `GET /api/v2/drops/{slug}/eligibility` is `DropEligibilityResponse.stages[]`; required stage fields are `stage_uuid` and `is_eligible`. `mints_remaining` is nullable and null may mean ineligible, ERC-1155, or unreadable on-chain count. It is not by itself a definitive eligibility flag. Hunter's parser accepts `stage_uuid` / `is_eligible`; it now preserves explicit eligibility separately from remaining mint capacity. A transaction preview additionally requires a known positive remaining cap.
- Safe local configuration-presence check (values withheld): `OPENSEA_API_KEY` present; `OPENSEA_WALLET_JWT` absent; `OPENSEA_WALLET_ADDRESS` absent; `ETHEREUM_RPC_URL` absent. Consequently no wallet-specific API call, Ethereum RPC check, transaction build, or simulation was attempted. No live response-schema comparison is possible until the user locally configures the scoped JWT and matching public wallet address.
- Narrow verifier correction: preview now rejects expired/malformed eligibility evidence and unknown/exhausted wallet mint capacity before making the OpenSea mint-builder request. Wrong chain remains rejected, wallet identity must match configured pin, and `signed_presale` remains unsupported. Zero price remains an OpenSea claim until existing independent contract and simulation gates pass.
- Tests: focused OpenSea verifier tests, 13 passed; complete Python 3.14.10 suite, 236 passed; Node suite, 27 passed. Tests use fixtures/mocks for remote behavior. No live OpenSea eligibility or Ethereum RPC result is claimed. SQLite was not modified this step; previous integrity result was `ok`, and both Solana `SUBMISSION_UNKNOWN` records and reservations remain unchanged.
- No wallet connection, signing, broadcast, transaction retry, Telegram delivery, scan, spend, or dependency installation occurred. Shared budget remains restricted.
- Next user action: create a short-lived OpenSea token with only `read:eligibility` through the official UI, exchange a PAT for a wallet JWT if needed, then set `OPENSEA_WALLET_JWT`, `OPENSEA_WALLET_ADDRESS` (exact selected public address), and `ETHEREUM_RPC_URL` in local `.env`; never send those values in chat. Restart the local dashboard and run one wallet eligibility check. Sections is still a future `signed_presale` with HIGH saved risk, so it is not a candidate for this public SeaDrop preview builder even if eligibility is reported true.

## OpenSea live configuration and bounded check — 2026-11-17

- Official instructions and OpenAPI schema were rechecked. The correct scope is `read:eligibility`. OpenSea distinguishes API key (`X-API-KEY`) from wallet JWT (`Authorization: Bearer`). If Settings → Developer supplies a PAT, exchange it at `/api/v2/auth/tokens/exchange` using `subjectTokenType=ACCESS_TOKEN`; never use the PAT itself as a Bearer token. JWT lifetime is short (about 12 hours). The user must retain credentials in local `.env`, never chat or logs.
- Safe presence check: `OPENSEA_API_KEY`, `OPENSEA_WALLET_JWT`, `OPENSEA_WALLET_ADDRESS`, and `ETHEREUM_RPC_URL` are configured; no values were printed. The configured wallet was not the Binance address returned by the local read-only wallet endpoint; it was used as the pinned EVM address in the OpenSea check (masked `0x4d79…6cfe`).
- Dashboard at port 8767 was stale: its OpenAPI had no wallet-check route. `GET /api/campaigns` on that process also surfaced a database compatibility error (`provider_id` missing from the older wallet evidence table). A backup was made before migration: `data/backups/nft_hunter_pre_opensea_provider_migration_20261116T170113Z.sqlite3`, integrity `ok`.
- Added pre-schema additive SQLite migration in `app/database.py`: old wallet-evidence table receives `provider_id='unknown'`; the old provider-agnostic identity index is replaced with the current provider-scoped index. It preserves table rows. Legacy table had 0 evidence rows before migration. Post-migration integrity is `ok`, with both Solana `SUBMISSION_UNKNOWN` transaction rows unchanged.
- Current code was started temporarily on `127.0.0.1:8768`; it exposed the correct eligibility route and found Sections lead #1 with HIGH risk and `CLAIMED_FREE_UNVERIFIED`. One bounded request was attempted through the current endpoint. OpenSea details request ended in sanitized `TRANSPORT_ERROR` after one request; no HTTP response was returned and eligibility API was not reached. Independent Ethereum RPC state was `RPC_UNAVAILABLE`. Persisted result is `DEFERRED_MISSING_EVIDENCE`; eligibility `UNKNOWN`, price `UNKNOWN`, and current stage `UNKNOWN`. The five-minute evidence expiry was set. The temporary server was stopped; the older port-8767 process was left untouched.
- The Windows computer-use bridge was unavailable, so no browser window or live MetaMask account/network could be inspected. The endpoint call used the pinned configured address and `provider_id=metamask`; that provider label was request metadata, not independently verified by the browser. No eligibility outcome was returned, so this did not establish eligibility or a valid preview. Before any later check, the user should confirm MetaMask is connected to the same masked address in Hunter.
- The earlier user-provided announcement still says Sections is a `signed_presale` scheduled Nov 18, 2026 17:20–17:39 UTC, with a claimed zero price. It was not refreshed by this check. The project lead remains HIGH risk; signed-presale mint preparation remains unsupported even if eligibility is later reported true. No unsigned transaction was prepared.
- Regression after migration and parser/gate changes: Python 3.14.10 full suite 237 passed; Node suite 27 passed. New migration regression confirms existing evidence persists, legacy provider defaults to unknown, and index now includes provider. No signing, broadcast, mint, spend, transaction retry, Telegram delivery, reservation release, or campaign scan occurred.
- Next manual action: stop the old dashboard in its owning PowerShell with Ctrl+C and restart current code on 8767 using `$env:DASHBOARD_PORT='8767'; & '.\.venv\Scripts\python.exe' -B -m app.dashboard.api`. Use the dashboard’s MetaMask selection with the same pinned account and click **Check eligibility** once from the normal VS Code environment. If it fails, share only sanitized HTTP status/category and response fields. Do not share JWT/API key.

## Resume eligibility debugging — 2026-11-17

### Confirmed findings

- Read this handoff and `RUNBOOK.md` before resuming. Sections by Grant Yun is source lead `1`, Ethereum chain `1`, saved risk `HIGH`, and global status `BLOCKED_UNSUPPORTED`.
- **Dropdown reset root cause:** after a successful API response, `loadCampaignResults()` rebuilds the table and creates a fresh selector at its blank placeholder. The old code kept the selection only in the DOM. Fixed `dashboard.js` to remember provider plus exact address per lead, restore it after reload only when that same wallet is still connected, and clear it if the provider/address changed.
- **Saved response/evidence:** SQLite was opened read-only. Four wallet evidence rows exist. The newest is MetaMask, `DEFERRED_MISSING_EVIDENCE`, no stage ID, wallet eligibility `null`, one OpenSea request, sanitized `HTTP_ERROR` / HTTP `403`; neither details-specific nor eligibility-specific status was recorded, so the eligibility endpoint was not reached. Price stays unknown. No response body or credential was read/displayed.
- **Wallet pin mismatch:** the configured OpenSea API key, wallet JWT, pinned wallet address, and Ethereum RPC are all present (values withheld). The latest three saved wallet attempts do not match the pinned address; the oldest MetaMask record does. Thus the latest saved wallet does not match the configured OpenSea identity. Before this fix, `evaluate_drop()` requested public drop details before checking the JWT/address pin, so the HTTP 403 masked the mismatch. It now rejects absent JWT or non-matching/invalid pin before any OpenSea request. Added regression assertions that mismatch and missing auth make zero requests.
- **Transport vs provider errors:** one older evidence row (2026-11-16 17:02 UTC) records `TRANSPORT_ERROR`, no HTTP status, and `RPC_UNAVAILABLE`. The three later rows record OpenSea HTTP 403 and Ethereum RPC `READS_OK`; the newest RPC observation had bytecode present, ERC-721 true, ERC-1155 false, and source verification `UNKNOWN`. This separates the earlier transport denial from the later OpenSea HTTP response and RPC state. An HTTP 403 is a provider response, not proof of a wallet-auth mismatch; its body was intentionally not retained, so the exact OpenSea reason (API key/access/policy) is unknown. The latest recorded request reached the backend and OpenSea details endpoint, so it was not a browser connection failure for that check.
- **Current browser/server build:** the Python computer-use bridge failed to initialize, so no live browser account, network, or rendered assets could be inspected. This shell sees no listener on ports 8767 or 8768; the visible dashboard build therefore cannot be confirmed from this environment. Current disk source contains the eligibility route and the UI fix. Restart the server from the project VS Code terminal and hard-refresh the browser before relying on it.
- **Eligibility/independent verification:** no wallet-scoped eligibility response is saved. The latest attempt stopped at details HTTP 403. RPC reads succeeded, but contract source verification remains unknown. The saved `signed_presale` strategy is unsupported by the current public SeaDrop builder and risk remains HIGH; Sections stays blocked regardless of the dropdown fix.

### Fixes and validation

- Added wallet-selection persistence and exact-address invalidation in `app/dashboard/static/dashboard.js`; added UI regression coverage in `tests/dashboard_campaign_ui.test.mjs`.
- Moved wallet-JWT/pinned-address validation ahead of the OpenSea request in `app/discovery/opensea_drop_verifier.py`; updated tests to assert zero requests for missing auth and pin mismatch in `tests/test_opensea_drop_verifier.py`.
- Focused tests: Python verifier 13 passed; dashboard UI 4 passed. Full suites: Python 3.14.10 `.venv` 237 passed; Node 28 passed, 0 failed.
- No live network request, browser wallet connection, migration, evidence write, mint preview, transaction, signing, broadcast, retry, budget release, or spend occurred. Sections remains blocked.

### Next user action

1. In the normal VS Code terminal at `D:\NFT_hunter`, stop the old dashboard in its owning terminal with Ctrl+C, then run `$env:DASHBOARD_PORT='8767'; & '.\.venv\Scripts\python.exe' -B -m app.dashboard.api`.
2. Open `http://127.0.0.1:8767`, hard-refresh with Ctrl+F5, and choose the wallet whose full public address matches local `OPENSEA_WALLET_ADDRESS`. Do not paste credentials here.
3. Click **Check eligibility** once. Share only the sanitized status, HTTP status/category, masked selected wallet, `independent_rpc.status`, and whether the dropdown remained selected. Keep Sections blocked until wallet-scoped eligibility and independent mint verification both succeed.

## Dashboard layout refresh — 2026-11-17

- Replaced the long single-scroll workspace with six focused pages: Overview, Campaigns, Discoveries, Watchlist, Wallets, and Activity. Campaign scan and saved campaign leads now share one page. Navigation updates the page title, description, active state, and URL fragment; existing data loading and safety gates remain in place.
- Simplified the campaign table from nine columns to six by grouping contract/source and Mint/Price/Risk checks. Contract addresses are shortened visually, with the full value available in the title. Status text and wallet-check feedback now use plain-language labels; technical status remains available as badge title/status detail.
- Added a softer cream, rose, and mauve palette, card-based summaries, friendlier labels, and mobile adjustments. The advanced self-created Solana asset controls are collapsed under an explicit disclosure.
- Changed the CSS/JS query version to campaign-row-layout-2 to avoid stale browser cache.
- Browser preview was unavailable because the computer-use bridge did not initialize. The previous workspace snapshot also had no listener on 8767/8768; restart from the normal VS Code terminal and hard-refresh to see the new page. No tests were run for this visual-only update; no API, wallet, database, or transaction behavior was changed.
- Campaign results and Discoveries now show six rows initially with a “Show more” control in six-row batches and “Show fewer” after expanding all visible results. Changing filters/search returns to the first batch.
- Follow-up from the screenshot: fixed campaign table column widths, raised its minimum width so the table scrolls instead of overlapping, and changed Mint/Price/Risk chips to wrap. The wallet result now omits the long timestamp from inline text (full time remains in its tooltip); Updated uses a compact local date/time with the full timestamp in its tooltip.


## Source publication — 2026-11-17

- The NFT Hunter source checkpoint was published to the GitHub repository `https://github.com/tuanto3010/NFT_hunter` on branch `codex/nft-hunter-checkpoint`; the remote accepted commit `7d24c8778b796999d62d79c0d0fd0d5738e48c1c` (subject: `Add NFT Hunter source and checkpoint docs`). The branch was created from `main` at `e02ea8c7c86620e17952410b1d74188c7a2909d2`; `main` was not modified.
- This destination is GitHub, not Gerrit, so no Gerrit `refs/for/` review change was created.
- The published checkpoint contains application source, tests, documentation, safe `.env.example`, project configuration, and metadata fixtures. `.env`, credentials, private keys, databases, backups, logs, caches, and local tool/runtime directories were excluded.
- Verification before publication: Python suite 237 passed; Node suite 28 passed; the OpenSea drops test module passed 15 tests after replacing an unlabeled credential-like test literal with an explicit placeholder; staged secret scan reported zero findings; `git fsck --full --no-reflogs` exited successfully.
- GitHub branch: `https://github.com/tuanto3010/NFT_hunter/tree/codex/nft-hunter-checkpoint`.
