"""Provider-neutral wallet contracts, separated by live and simulated use."""

from typing import Protocol, Sequence

from app.wallet.binance_wallet_types import (
    ConnectionChallenge,
    WalletAddress,
    WalletBalance,
    WalletNetwork,
    WalletStatus,
)
from app.wallet.wallet_types import NetworkInfo, PreparedTransaction, SimulatedBalance, SimulatedOutcome


class ReadOnlyWallet(Protocol):
    def get_chain_id(self) -> int | str: ...

    def get_address(self) -> str: ...

    def get_balance(self) -> str: ...


class FutureRobinhoodWallet(ReadOnlyWallet, Protocol):
    """Separate future adapter boundary; intentionally has no Binance implementation."""


class WalletAdapter(Protocol):
    """Read-only/provider connection contract; it contains no signing method."""

    def get_status(self) -> WalletStatus: ...

    def begin_connection(self) -> ConnectionChallenge | None: ...

    def complete_connection(self, challenge_id: str) -> WalletStatus: ...

    def disconnect(self) -> None: ...

    def get_supported_networks(self) -> Sequence[WalletNetwork]: ...

    def get_addresses(self) -> Sequence[WalletAddress]: ...

    def get_balances(self, network_id: str | int | None = None) -> Sequence[WalletBalance]: ...


class SimulatedWalletAdapter(Protocol):
    """Separate local mock contract; its outcomes are never network transactions."""

    def connect(self) -> str: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def get_address(self) -> str: ...

    def get_network(self) -> NetworkInfo: ...

    def switch_network(self, network_id: int | str) -> NetworkInfo: ...

    def get_balance(self) -> SimulatedBalance: ...

    def prepare_transaction(self, destination: str, method: str, parameters: dict,
                            value: str = "0") -> PreparedTransaction: ...

    def approve_transaction(self, transaction_id: str) -> PreparedTransaction: ...

    def reject_transaction(self, transaction_id: str) -> PreparedTransaction: ...

    def simulate_transaction(self, transaction_id: str) -> SimulatedOutcome: ...

