"""Preview-only wallet simulation orchestration.

No method in this module signs, executes, or broadcasts a transaction. Binance's
Developer Mode setting is checked before calldata is built, per official CLI
guidance. Solana is deliberately fail-closed until its message/program parser
can establish that an unsigned message contains only a supported NFT mint.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
import re
from typing import Any, Mapping, Protocol, Sequence

from app.discovery.project_sources import ProjectObservation
from app.wallet.binance_wallet import BinanceAgenticWalletAdapter, BinanceWalletError
from app.wallet.binance_wallet_types import EvmMintSimulation
from app.wallet.transaction_builder import (
    MintTransactionBuilder,
    UnsignedEvmMint,
    UnsafeMintRequest,
)


class DeveloperModeRequired(BinanceWalletError):
    """Binance Developer Mode is disabled or expired; no preview was run."""


class SolanaMintPreviewUnavailable(BinanceWalletError):
    """No verified Solana unsigned-message parser is available yet."""


class UnknownMintPrice(UnsafeMintRequest):
    """The project has no explicit evidence that its current mint price is zero."""


class JsonRpcReader(Protocol):
    def call(self, method: str, params: list[Any]) -> Any: ...


@dataclass(frozen=True)
class EvmNetworkFeeEstimate:
    chain_id: str
    native_token: str
    gas_limit: int
    gas_price_wei: int
    fee_wei: int
    fee_native: str
    estimate_source: str = "eth_estimateGas × eth_gasPrice"


class EvmRpcFeeEstimator:
    """Estimate network cost with read-only EVM JSON-RPC calls."""

    _EXPECTED_CHAIN_IDS = {"56": 56, "1": 1, "8453": 8453}

    def __init__(self, clients: Mapping[str, JsonRpcReader]) -> None:
        self._clients = dict(clients)

    def estimate(self, transaction: Any) -> EvmNetworkFeeEstimate:
        chain_id = str(transaction.chain_id)
        expected_chain_id = self._EXPECTED_CHAIN_IDS.get(chain_id)
        client = self._clients.get(chain_id)
        if expected_chain_id is None or client is None:
            raise ValueError("No configured read-only RPC is available for this network")
        actual_chain_id = int(client.call("eth_chainId", []), 16)
        if actual_chain_id != expected_chain_id:
            raise ValueError("Configured RPC returned a different network")
        call = {
            "from": transaction.from_address,
            "to": transaction.contract_address,
            "value": "0x0",
            "data": transaction.calldata,
        }
        gas_limit = int(client.call("eth_estimateGas", [call]), 16)
        gas_price_wei = int(client.call("eth_gasPrice", []), 16)
        if gas_limit <= 0 or gas_price_wei < 0:
            raise ValueError("RPC returned an invalid gas estimate")
        fee_wei = gas_limit * gas_price_wei
        fee_native = format(Decimal(fee_wei) / Decimal(10**18), "f")
        fee_native = fee_native.rstrip("0").rstrip(".") if "." in fee_native else fee_native
        return EvmNetworkFeeEstimate(
            chain_id=chain_id,
            native_token=transaction.native_gas_token,
            gas_limit=gas_limit,
            gas_price_wei=gas_price_wei,
            fee_wei=fee_wei,
            fee_native=fee_native or "0",
        )


class BinanceMintPreviewService:
    """Compose Developer Mode preflight, deterministic builder and preview API."""

    def __init__(
        self,
        wallet: BinanceAgenticWalletAdapter,
        builder: MintTransactionBuilder | None = None,
        fee_estimator: EvmRpcFeeEstimator | None = None,
    ) -> None:
        self._wallet = wallet
        self._builder = builder or MintTransactionBuilder()
        self._fee_estimator = fee_estimator

    def preview_evm_mint(
        self,
        observation: ProjectObservation,
        function_name: str,
        arguments: Mapping[str, Any] | Sequence[Any] = (),
    ) -> EvmMintSimulation:
        # The official Binance flow requires this check before calldata building.
        mode = self._wallet.get_developer_mode()
        if not mode.active:
            raise DeveloperModeRequired("Binance Developer Mode is not active; no mint preview was run")
        chain_id = str(observation.chain_id)
        if chain_id not in {"56", "1", "8453"}:
            raise UnsafeMintRequest("This wallet adapter has no documented NFT preview path for this network")
        networks = self._wallet.get_supported_networks()
        network = next((item for item in networks if item.network_id == chain_id), None)
        if network is None or network.family != "evm":
            raise UnsafeMintRequest("The requested EVM network is not listed by the connected wallet")
        addresses = self._wallet.get_addresses()
        wallet_address = next(
            (item.address for item in addresses if item.network_id == chain_id and item.address),
            None,
        )
        if wallet_address is None:
            raise UnsafeMintRequest("The connected wallet has no address for this network")
        # Building happens only after Developer Mode and network/address checks.
        transaction = self._builder.build_evm_mint(
            observation, wallet_address, function_name, arguments,
        )
        estimate = self._fee_estimator.estimate(transaction) if self._fee_estimator else None
        preview = self._wallet._preview_evm_mint(transaction, mode)
        if estimate is not None:
            preview = replace(
                preview,
                network_fee_wei=str(estimate.fee_wei),
                network_fee_native=estimate.fee_native,
                estimated_gas_limit=estimate.gas_limit,
                gas_price_wei=str(estimate.gas_price_wei),
            )
        return preview

    def preview_solana_mint(self, *_args: Any, **_kwargs: Any) -> None:
        raise SolanaMintPreviewUnavailable(
            "Solana mint preview is disabled until unsigned Metaplex instructions can be validated deterministically"
        )


@dataclass(frozen=True)
class WalletBoundMintPreview:
    """Preview or preparation bound to one provider, address, chain and calldata."""

    provider_id: str
    wallet_address: str = field(repr=False)
    chain_id: str
    transaction_digest: str
    status: str
    eligibility_status: str
    mint_price_status: str
    mint_price_wei: str
    native_gas_token: str
    estimated_gas_limit: int | None = None
    gas_price_wei: str | None = None
    network_fee_wei: str | None = None
    network_fee_native: str | None = None
    transaction: UnsignedEvmMint | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        transaction = self.transaction
        return {
            "provider_id": self.provider_id,
            "wallet_address": self.wallet_address,
            "chain_id": self.chain_id,
            "transaction_digest": self.transaction_digest,
            "status": self.status,
            "eligibility_status": self.eligibility_status,
            "mint_price_status": self.mint_price_status,
            "mint_price_wei": self.mint_price_wei,
            "native_gas_token": self.native_gas_token,
            "estimated_gas_limit": self.estimated_gas_limit,
            "gas_price_wei": self.gas_price_wei,
            "network_fee_wei": self.network_fee_wei,
            "network_fee_native": self.network_fee_native,
            "transaction": ({
                "from_address": transaction.from_address,
                "contract_address": transaction.contract_address,
                "function_signature": transaction.function_signature,
                "calldata": transaction.calldata,
                "value_wei": transaction.value_wei,
            } if transaction else None),
        }


class WalletAwareMintPreviewService:
    """Route EVM read-only preparation by explicit provider and selected address."""

    _EVM_CHAINS = {"56", "1", "8453"}
    _ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")

    def __init__(
        self,
        binance_wallet: BinanceAgenticWalletAdapter,
        builder: MintTransactionBuilder | None = None,
        fee_estimator: EvmRpcFeeEstimator | None = None,
    ) -> None:
        self._binance_wallet = binance_wallet
        self._builder = builder or MintTransactionBuilder()
        self._fee_estimator = fee_estimator

    def prepare(
        self,
        observation: ProjectObservation,
        provider_id: str,
        wallet_address: str,
        chain_id: str | int,
        function_name: str,
        arguments: Mapping[str, Any] | Sequence[Any] = (),
    ) -> WalletBoundMintPreview:
        selected_chain = str(chain_id)
        if selected_chain != str(observation.chain_id):
            raise UnsafeMintRequest("Selected wallet chain does not match this opportunity")
        if selected_chain not in self._EVM_CHAINS:
            raise UnsafeMintRequest("Only supported EVM networks can use this preview path; Solana is separate")
        if not self._ADDRESS.fullmatch(wallet_address):
            raise UnsafeMintRequest("Selected wallet address is invalid")
        if provider_id not in {"binance_agentic", "metamask"}:
            raise UnsafeMintRequest("Unknown wallet provider; no fallback is permitted")

        mint_price = observation.mint_price
        if mint_price is None:
            mint_price = observation.details.get("mint_price")
        if observation.details.get("mint_price_verified") is not True or mint_price is None:
            raise UnknownMintPrice("Mint price is UNKNOWN; explicit verified zero-price evidence is required")
        try:
            parsed_price = Decimal(str(mint_price))
        except (InvalidOperation, TypeError, ValueError):
            raise UnknownMintPrice("Mint price is UNKNOWN; explicit verified zero-price evidence is required") from None
        if not parsed_price.is_finite():
            raise UnknownMintPrice("Mint price is UNKNOWN; explicit verified zero-price evidence is required")
        if parsed_price != 0:
            raise UnsafeMintRequest("Mint is PAID; this preview path accepts only verified zero-price mints")

        if provider_id == "binance_agentic":
            return self._preview_binance(
                observation, wallet_address, selected_chain, function_name, arguments,
            )

        # This unsigned calldata is provisional. MetaMask must simulate it from
        # the exact selected account before the UI calls it eligible or a preview.
        prepared_observation = replace(observation, eligibility="eligible")
        transaction = self._builder.build_evm_mint(
            prepared_observation, wallet_address, function_name, arguments,
        )
        return WalletBoundMintPreview(
            provider_id="metamask", wallet_address=wallet_address,
            chain_id=selected_chain, transaction_digest=transaction.transaction_digest,
            status="AWAITING_METAMASK_READ_ONLY_SIMULATION",
            eligibility_status="pending_selected_wallet_simulation",
            mint_price_status="VERIFIED_ZERO", mint_price_wei="0",
            native_gas_token=transaction.native_gas_token, transaction=transaction,
        )

    def _preview_binance(
        self,
        observation: ProjectObservation,
        wallet_address: str,
        chain_id: str,
        function_name: str,
        arguments: Mapping[str, Any] | Sequence[Any],
    ) -> WalletBoundMintPreview:
        wallet = self._binance_wallet
        if not wallet.get_status().connected:
            raise UnsafeMintRequest("Selected Binance wallet is disconnected")
        network = next((item for item in wallet.get_supported_networks()
                        if item.network_id == chain_id and item.family == "evm"), None)
        if network is None:
            raise UnsafeMintRequest("Selected Binance wallet does not support this EVM network")
        matches = [item.address for item in wallet.get_addresses()
                   if item.network_id == chain_id and item.address]
        if not any(address.lower() == wallet_address.lower() for address in matches):
            raise UnsafeMintRequest("Selected address does not match the Binance wallet on this network")
        mode = wallet.get_developer_mode()
        if not mode.active:
            raise DeveloperModeRequired("Binance Developer Mode is not active; no preview was run")

        prepared_observation = replace(observation, eligibility="eligible")
        transaction = self._builder.build_evm_mint(
            prepared_observation, wallet_address, function_name, arguments,
        )
        estimate = self._fee_estimator.estimate(transaction) if self._fee_estimator else None
        # This is the existing Binance simulation path, now bound to the explicit
        # and CLI-verified selected address. It still discards the request ID.
        simulation = wallet._preview_evm_mint(transaction, mode)
        passed = simulation.status == "SIMULATION_PASSED"
        return WalletBoundMintPreview(
            provider_id="binance_agentic", wallet_address=wallet_address,
            chain_id=chain_id, transaction_digest=transaction.transaction_digest,
            status=simulation.status,
            eligibility_status="eligible" if passed else "unknown",
            mint_price_status="VERIFIED_ZERO", mint_price_wei=simulation.mint_price_wei,
            native_gas_token=simulation.native_gas_token,
            estimated_gas_limit=estimate.gas_limit if estimate else simulation.estimated_gas_limit,
            gas_price_wei=str(estimate.gas_price_wei) if estimate else simulation.gas_price_wei,
            network_fee_wei=str(estimate.fee_wei) if estimate else simulation.network_fee_wei,
            network_fee_native=estimate.fee_native if estimate else simulation.network_fee_native,
            transaction=transaction,
        )

    def preview_solana_mint(self, *_args: Any, **_kwargs: Any) -> None:
        raise SolanaMintPreviewUnavailable(
            "Solana preview cannot use the EVM transaction builder; a separate validated instruction simulator is required"
        )

