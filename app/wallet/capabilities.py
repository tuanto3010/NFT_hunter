"""Documented network reach and NFT mint capability states for wallet adapters."""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WalletCapability:
    network: str
    chain_supported: Optional[bool]
    nft_mint_supported: Optional[bool]
    evidence: str


BINANCE_AGENTIC_CAPABILITIES = {
    "bnb": WalletCapability("BNB Smart Chain", True, None, "Binance docs list BSC; NFT mint is not listed as a supported operation."),
    "ethereum": WalletCapability("Ethereum", True, None, "Binance docs list Ethereum; NFT mint is not listed as a supported operation."),
    "base": WalletCapability("Base", True, None, "Binance docs list Base; NFT mint is not listed as a supported operation."),
    "solana": WalletCapability("Solana", True, None, "Binance docs list Solana; NFT mint is not listed as a supported operation."),
    "robinhood": WalletCapability(
        "Robinhood Chain", None, None,
        "The current Binance Skills Hub wallet chains/address reference includes ID 4663, but the Agentic Wallet overview lists only BNB, Ethereum, Base and Solana; confirm through the installed CLI at runtime.",
    ),
}


def can_use_for_nft_mint(network: str) -> bool:
    """Return true only for an explicitly documented NFT-mint capability."""
    capability = BINANCE_AGENTIC_CAPABILITIES[network]
    return capability.chain_supported is True and capability.nft_mint_supported is True

