"""A local wallet simulator with no chain connection or transaction methods."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Dict, Mapping
from uuid import uuid4

from app.wallet.wallet_types import (
    NetworkInfo,
    PreparedTransaction,
    SimulatedBalance,
    SimulatedOutcome,
)


SIMULATED_NETWORKS = {
    56: NetworkInfo(56, "BNB Smart Chain (simulated)", "BNB"),
    1: NetworkInfo(1, "Ethereum (simulated)", "ETH"),
    8453: NetworkInfo(8453, "Base (simulated)", "ETH"),
    4663: NetworkInfo(4663, "Robinhood Chain (simulated)", "ETH"),
    "solana-mainnet-beta": NetworkInfo("solana-mainnet-beta", "Solana mainnet-beta (simulated)", "SOL"),
}

SIMULATED_BALANCES = {
    "BNB": "1.25000000",
    "ETH": "0.75000000",
    "SOL": "4.00000000",
}


class DummyWalletAdapter:
    """Pure in-memory fake wallet; cannot sign or broadcast transactions.

    It has no RPC, SDK, key store, signer, or network transport. All data and
    outcomes returned by this class are fake and explicitly marked simulated.
    """

    def __init__(self, initial_network: int | str = 56) -> None:
        if initial_network not in SIMULATED_NETWORKS:
            raise ValueError("unsupported simulated network")
        self._network = SIMULATED_NETWORKS[initial_network]
        self._connected = False
        self._transactions: Dict[str, PreparedTransaction] = {}
        self._address = "SIMULATED-WALLET-0x0000000000000000000000000000000000000000"

    def connect(self) -> str:
        self._connected = True
        return self._address

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def get_address(self) -> str:
        self._require_connected()
        return self._address

    def get_network(self) -> NetworkInfo:
        return self._network

    def switch_network(self, network_id: int | str) -> NetworkInfo:
        if network_id not in SIMULATED_NETWORKS:
            raise ValueError("unsupported simulated network")
        self._network = SIMULATED_NETWORKS[network_id]
        return self._network

    def get_balance(self) -> SimulatedBalance:
        self._require_connected()
        return SimulatedBalance(SIMULATED_BALANCES[self._network.native_token], self._network.native_token)

    def prepare_transaction(
        self,
        destination: str,
        method: str,
        parameters: Mapping[str, str],
        value: str = "0",
    ) -> PreparedTransaction:
        self._require_connected()
        if not destination.strip() or not method.strip():
            raise ValueError("destination and method are required")
        try:
            amount = Decimal(value)
        except (InvalidOperation, TypeError):
            raise ValueError("value must be a non-negative decimal string") from None
        if not amount.is_finite() or amount < 0:
            raise ValueError("value must be a non-negative decimal string")
        safe_parameters = tuple(sorted((str(key), str(item)) for key, item in parameters.items()))
        transaction_id = "sim-tx-" + uuid4().hex
        transaction = PreparedTransaction(
            transaction_id=transaction_id,
            network_id=self._network.network_id,
            destination=destination,
            method=method,
            parameters=safe_parameters,
            value=str(amount),
        )
        self._transactions[transaction_id] = transaction
        return transaction

    def approve_transaction(self, transaction_id: str) -> PreparedTransaction:
        return self._decide(transaction_id, "approved")

    def reject_transaction(self, transaction_id: str) -> PreparedTransaction:
        return self._decide(transaction_id, "rejected")

    def simulate_transaction(self, transaction_id: str) -> SimulatedOutcome:
        self._require_connected()
        transaction = self._get_transaction(transaction_id)
        if transaction.decision == "pending":
            raise ValueError("transaction needs an explicit simulated approval or rejection")
        status = "simulated_success" if transaction.decision == "approved" else "simulated_rejected"
        return SimulatedOutcome(
            transaction_id=transaction_id,
            status=status,
            simulated_reference="SIMULATED-" + uuid4().hex,
        )

    def _decide(self, transaction_id: str, decision: str) -> PreparedTransaction:
        self._require_connected()
        transaction = self._get_transaction(transaction_id)
        if transaction.decision != "pending":
            raise ValueError("transaction already has a decision")
        updated = PreparedTransaction(
            transaction_id=transaction.transaction_id,
            network_id=transaction.network_id,
            destination=transaction.destination,
            method=transaction.method,
            parameters=transaction.parameters,
            value=transaction.value,
            decision=decision,
        )
        self._transactions[transaction_id] = updated
        return updated

    def _get_transaction(self, transaction_id: str) -> PreparedTransaction:
        try:
            return self._transactions[transaction_id]
        except KeyError:
            raise ValueError("unknown simulated transaction") from None

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("dummy wallet is disconnected")
