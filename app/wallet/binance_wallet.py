"""Read-only Binance Agentic Wallet adapter over the documented ``baw`` CLI.

The adapter does not implement arbitrary command execution, signing, previews,
transaction execution, or broadcasting. Authentication is a user-confirmed QR
flow supported by Binance; wallet reads are exposed as structured data only.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlsplit

from app.wallet.transaction_builder import UnsafeMintRequest, UnsignedEvmMint, validate_prepared_evm_mint
from app.wallet.binance_wallet_types import (
    ConnectionChallenge,
    DeveloperModeStatus,
    EvmMintSimulation,
    MintPreviewRisk,
    WalletAddress,
    WalletBalance,
    WalletNetwork,
    WalletStatus,
)


class BinanceWalletError(RuntimeError):
    """Sanitized adapter error; raw CLI output is never included."""


CommandRunner = Callable[[Sequence[str], float], subprocess.CompletedProcess]


class BinanceAgenticWalletAdapter:
    """Call documented read commands and a gated, non-executing EVM preview."""

    DOCUMENTED_REQUIRED_CLI_VERSION = "1.12.0"
    _PREVIEW_EVM_CHAINS = {"56", "1", "8453"}
    _ADDRESS_TEXT = re.compile(r"0x[0-9a-fA-F]{40}|[1-9A-HJ-NP-Za-km-z]{32,44}")

    _NATIVE_TOKENS = {
        "56": "BNB",
        "1": "ETH",
        "8453": "ETH",
        "4663": "ETH",
        "CT_501": "SOL",
        "42161": "ETH",
        "137": "POL",
    }

    def __init__(
        self,
        executable: Optional[str] = None,
        runner: Optional[CommandRunner] = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if executable is not None:
            self._command_prefix = (executable,)
        elif runner is not None:
            # Keep fake-runner tests independent of the host's installed tools.
            self._command_prefix = ("baw",)
        else:
            package_entry = (
                Path(__file__).resolve().parents[2]
                / ".tools" / "node_modules" / "@binance" / "agentic-wallet" / "dist" / "index.js"
            )
            if package_entry.is_file():
                self._command_prefix = (shutil.which("node") or "node", str(package_entry))
            else:
                self._command_prefix = ("baw",)
        self._runner = runner or self._run_process
        self._timeout_seconds = timeout_seconds

    def verify_cli_version(self) -> str:
        """Fail closed if the CLI is older than the current official skill requires."""
        data = self._invoke((
            "cli-check", "--required-version", self.DOCUMENTED_REQUIRED_CLI_VERSION, "--json",
        ))
        if not isinstance(data, dict):
            raise BinanceWalletError("Binance returned an invalid CLI version result")
        if data.get("needUpdateCli") is not False:
            raise BinanceWalletError("Binance Agentic Wallet CLI needs an update")
        version = data.get("currentCliVersion")
        if not isinstance(version, str) or not version:
            raise BinanceWalletError("Binance returned an invalid CLI version")
        return version

    def begin_connection(self) -> Optional[ConnectionChallenge]:
        """Begin Binance's documented QR sign-in; the user confirms in Binance App."""
        data = self._invoke(("auth", "signin", "--json"))
        if not isinstance(data, dict):
            raise BinanceWalletError("Binance returned an invalid sign-in response")
        if data.get("status") == "ALREADY_CONNECTED":
            return None
        try:
            challenge_id = str(data["qrCodeId"])
            pairing_code = str(data["pairingCode"])
            url = str(data["urlForWeb"])
            parsed_url = urlsplit(url)
            if (not challenge_id or len(challenge_id) > 200 or not pairing_code
                    or parsed_url.scheme != "https"
                    or parsed_url.hostname not in {"web3.binance.com", "app.binance.com"}):
                raise ValueError("invalid sign-in response")
            return ConnectionChallenge(
                challenge_id=challenge_id,
                pairing_code=pairing_code,
                url=url,
                expires_at=str(data["expireAt"]) if data.get("expireAt") is not None else None,
            )
        except (KeyError, TypeError, ValueError):
            raise BinanceWalletError("Binance returned an incomplete sign-in challenge") from None

    def complete_connection(self, challenge_id: str) -> WalletStatus:
        """Wait for the user-approved QR pairing, then verify status with Binance."""
        if not challenge_id or len(challenge_id) > 200:
            raise ValueError("challenge_id must contain 1 to 200 characters")
        verification = self._invoke(
            ("auth", "verify", "--qrCodeId", challenge_id, "--json"), timeout=310.0,
        )
        if not isinstance(verification, dict) or verification.get("status") != "SUCCESS":
            raise BinanceWalletError("Binance QR sign-in was not completed")
        status = self.get_status()
        if not status.connected:
            raise BinanceWalletError("Binance sign-in finished but wallet status is not CONNECTED")
        return status

    def get_status(self) -> WalletStatus:
        data = self._invoke(("wallet", "status", "--json"))
        if not isinstance(data, dict):
            raise BinanceWalletError("Binance returned an invalid wallet status")
        status = data.get("status")
        if not isinstance(status, str):
            raise BinanceWalletError("Binance returned an invalid wallet status")
        return WalletStatus(status=status, connected=status == "CONNECTED")

    def disconnect(self) -> None:
        self._invoke(("auth", "signout", "--json"))

    def get_supported_networks(self) -> tuple[WalletNetwork, ...]:
        data = self._invoke(("wallet", "chains", "--json"))
        if not isinstance(data, list):
            raise BinanceWalletError("Binance returned an invalid supported-network list")
        networks = []
        for item in data:
            if not isinstance(item, dict):
                continue
            chain_id = item.get("binanceChainId")
            name = item.get("name")
            if chain_id is None or not isinstance(name, str):
                continue
            identifier = str(chain_id)
            networks.append(WalletNetwork(
                network_id=identifier,
                name=name,
                simple_name=str(item.get("simpleName") or name),
                family="solana" if identifier == "CT_501" else "evm",
                native_token=self._NATIVE_TOKENS.get(identifier),
            ))
        return tuple(networks)

    def get_addresses(self) -> tuple[WalletAddress, ...]:
        data = self._invoke(("wallet", "address", "--json"))
        records = data.get("addresses") if isinstance(data, dict) else None
        if not isinstance(records, list):
            raise BinanceWalletError("Binance returned an invalid wallet-address list")
        addresses = []
        for item in records:
            if not isinstance(item, dict):
                continue
            chain_id = item.get("binanceChainId")
            address = item.get("address")
            if chain_id is None or not isinstance(address, str) or not address:
                continue
            addresses.append(WalletAddress(
                network_id=str(chain_id),
                network_name=str(item.get("chainName") or chain_id),
                address=address,
            ))
        return tuple(addresses)

    def get_agent_id_handle(self) -> Optional[str]:
        """Read the claimed Agent ID handle for internal eligibility checks.

        Callers must not log or expose this value. It is never a credential.
        """
        data = self._invoke(("wallet", "agent-id", "--json"))
        if not isinstance(data, dict) or data.get("claimed") is not True:
            return None
        handle = data.get("handle")
        if not isinstance(handle, str):
            raise BinanceWalletError("Binance returned an invalid Agent ID result")
        normalized = handle.strip().lower()
        if normalized.endswith(".ai"):
            normalized = normalized[:-3]
        if not normalized or len(normalized) > 128 or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", normalized):
            raise BinanceWalletError("Binance returned an invalid Agent ID result")
        return normalized

    def get_balances(self, network_id: str | int | None = None) -> tuple[WalletBalance, ...]:
        args = ["wallet", "balance"]
        if network_id is not None:
            identifier = str(network_id)
            networks = self.get_supported_networks()
            if identifier not in {network.network_id for network in networks}:
                raise ValueError("network is not currently listed by Binance Agentic Wallet")
            args.extend(("--binanceChainId", identifier))
        # Explicit false requests low-value/unpriced tokens where supported by BAW.
        args.extend(("--hideLowValue", "false", "--json"))
        data = self._invoke(tuple(args))
        if not isinstance(data, list):
            raise BinanceWalletError("Binance returned an invalid wallet-balance list")
        balances = []
        for item in data:
            if not isinstance(item, dict):
                continue
            chain_id = item.get("binanceChainId")
            symbol = item.get("symbol")
            amount = item.get("balance")
            if chain_id is None or not isinstance(symbol, str) or amount is None:
                continue
            balances.append(WalletBalance(
                network_id=str(chain_id),
                symbol=symbol,
                amount=str(amount),
                token_address=str(item["address"]) if item.get("address") else None,
                price=str(item["price"]) if item.get("price") is not None else None,
                value_usd=str(item["value"]) if item.get("value") is not None else None,
            ))
        return tuple(balances)

    def get_developer_mode(self) -> DeveloperModeStatus:
        """Read and validate Developer Mode before any external-call preparation."""
        data = self._invoke(("wallet", "settings", "--json"))
        mode = data.get("devMode") if isinstance(data, dict) else None
        if not isinstance(mode, dict):
            raise BinanceWalletError("Binance returned invalid Developer Mode settings")
        enabled = mode.get("enabled") is True
        balance_exceeded = mode.get("balanceExceeded") is True
        expires_raw = mode.get("expiresAt")
        try:
            expires_at = int(expires_raw) if expires_raw is not None else None
        except (TypeError, ValueError, OverflowError):
            expires_at = None
        active = enabled and not balance_exceeded and expires_at is not None and expires_at > int(time.time())
        return DeveloperModeStatus(
            enabled=enabled,
            active=active,
            expires_at=expires_at,
            balance_exceeded=balance_exceeded,
        )

    def _preview_evm_mint(
        self,
        transaction: UnsignedEvmMint,
        developer_mode: DeveloperModeStatus,
    ) -> EvmMintSimulation:
        """Run Binance's documented simulation only; never executes its requestId."""
        if not developer_mode.active:
            raise BinanceWalletError("Binance Developer Mode is not active; preview was not run")
        if transaction.chain_id not in self._PREVIEW_EVM_CHAINS:
            raise BinanceWalletError("NFT preview is not enabled for this EVM network")
        try:
            validate_prepared_evm_mint(transaction)
        except UnsafeMintRequest as exc:
            raise BinanceWalletError(str(exc)) from None
        data = self._invoke((
            "contract-call", "preview",
            "--binanceChainId", transaction.chain_id,
            "--from", transaction.from_address,
            "--to", transaction.contract_address,
            "--value", "0",
            "--inputData", transaction.calldata,
            "--json",
        ))
        simulation = data.get("simulationResult") if isinstance(data, dict) else None
        if not isinstance(simulation, dict):
            raise BinanceWalletError("Binance returned an invalid simulation result")
        risks_object = data.get("risks") if isinstance(data.get("risks"), dict) else {}
        risk_items: list[dict[str, Any]] = []
        top_level = risks_object.get("riskDetails")
        if isinstance(top_level, list):
            risk_items.extend(item for item in top_level if isinstance(item, dict))
        by_address = risks_object.get("addresses")
        if isinstance(by_address, dict):
            for address_risk in by_address.values():
                if isinstance(address_risk, dict) and isinstance(address_risk.get("riskDetails"), list):
                    risk_items.extend(item for item in address_risk["riskDetails"] if isinstance(item, dict))
        risks = tuple(
            MintPreviewRisk(
                code=self._sanitize_preview_text(item.get("code")),
                title=self._sanitize_preview_text(item.get("title")),
                description=self._sanitize_preview_text(item.get("description")),
            )
            for item in risk_items
        )
        raw_code = simulation.get("simulationCode")
        code = str(raw_code) if raw_code is not None else None
        raw_error = simulation.get("simulationErrorDetail")
        error = self._sanitize_preview_text(raw_error) if raw_error else None
        passed = code in {"0", "000000000", "0000000000"} and error is None
        expires = data.get("expiresAt")
        try:
            expires_at = int(expires) if expires is not None else None
        except (TypeError, ValueError, OverflowError):
            expires_at = None
        require_confirmation = data.get("requireConfirmation")
        if not isinstance(require_confirmation, bool):
            require_confirmation = None
        # The requestId is intentionally discarded. This adapter exposes no execute method.
        return EvmMintSimulation(
            chain_id=transaction.chain_id,
            transaction_digest=transaction.transaction_digest,
            status="SIMULATION_PASSED" if passed else "SIMULATION_FAILED",
            simulation_code=code,
            simulation_error=error,
            risks=risks,
            requires_app_confirmation=require_confirmation,
            expires_at=expires_at,
            mint_price_wei="0",
            native_gas_token=transaction.native_gas_token,
            network_fee_wei=None,
        )

    @classmethod
    def _sanitize_preview_text(cls, value: Any) -> str:
        text = str(value) if value is not None else ""
        return cls._ADDRESS_TEXT.sub("[address]", text)[:500]

    def _invoke(self, args: Sequence[str], timeout: Optional[float] = None) -> Any:
        command = self._command_prefix + tuple(args)
        try:
            result = self._runner(command, timeout or self._timeout_seconds)
        except FileNotFoundError:
            raise BinanceWalletError("Binance Agentic Wallet CLI (baw) is not installed or not on PATH") from None
        except subprocess.TimeoutExpired:
            raise BinanceWalletError("Binance Agentic Wallet command timed out") from None
        except OSError:
            raise BinanceWalletError("Could not start Binance Agentic Wallet CLI") from None
        if result.returncode != 0:
            raise BinanceWalletError("Binance Agentic Wallet command failed")
        try:
            response = json.loads(result.stdout)
        except (TypeError, ValueError):
            raise BinanceWalletError("Binance Agentic Wallet returned invalid JSON") from None
        if not isinstance(response, dict) or response.get("success") is not True or "data" not in response:
            raise BinanceWalletError("Binance Agentic Wallet request was unsuccessful")
        return response["data"]

    @staticmethod
    def _run_process(command: Sequence[str], timeout_seconds: float) -> subprocess.CompletedProcess:
        """Run without a shell, and capture output so CLI secrets are not echoed."""
        return subprocess.run(
            list(command),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
