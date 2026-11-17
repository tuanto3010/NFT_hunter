"""Common interface and chain registry for read-only discovery adapters."""

from dataclasses import dataclass
from typing import List, Protocol
from urllib.parse import urlsplit

from app.discovery.project_sources import ProjectObservation


@dataclass(frozen=True)
class ChainSpec:
    key: str
    name: str
    chain_id: int | str
    family: str
    native_token: str
    rpc_env: str
    explorer_api: str = ""
    explorer_env: str = ""


CHAIN_SPECS = {
    "bnb": ChainSpec("bnb", "BNB Smart Chain", 56, "evm", "BNB", "BNB_RPC_URL", explorer_env="BNB_EXPLORER_API_URL"),
    "ethereum": ChainSpec("ethereum", "Ethereum", 1, "evm", "ETH", "ETHEREUM_RPC_URL", explorer_env="ETHEREUM_EXPLORER_API_URL"),
    "base": ChainSpec("base", "Base", 8453, "evm", "ETH", "BASE_RPC_URL", explorer_env="BASE_EXPLORER_API_URL"),
    "solana": ChainSpec("solana", "Solana mainnet-beta", "solana-mainnet-beta", "solana", "SOL", "SOLANA_RPC_URL"),
    "robinhood": ChainSpec("robinhood", "Robinhood Chain", 4663, "evm", "ETH", "ROBINHOOD_RPC_URL", explorer_env="ROBINHOOD_EXPLORER_API_URL"),
}


class ChainAdapter(Protocol):
    spec: ChainSpec

    def scan(self) -> List[ProjectObservation]:
        """Return read-only observations from this chain."""


def safe_rpc_source(endpoint: str) -> str:
    """Return a source label without credentials, path tokens, or query values."""
    try:
        parts = urlsplit(endpoint)
        if not parts.scheme or not parts.hostname:
            return "configured RPC endpoint"
        host = parts.hostname
        if parts.port:
            host = "%s:%d" % (host, parts.port)
        return "%s://%s" % (parts.scheme, host)
    except ValueError:
        return "configured RPC endpoint"


def validate_chain_id(expected: int, actual: int | str) -> None:
    if expected != actual:
        raise ValueError("RPC chain ID mismatch: expected %s, received %s" % (expected, actual))

