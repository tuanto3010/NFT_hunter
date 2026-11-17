"""Typed results returned by documented Binance Agentic Wallet CLI commands."""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class WalletStatus:
    status: str
    connected: bool


@dataclass(frozen=True)
class DeveloperModeStatus:
    """Sanitized Developer Mode preflight result; contains no wallet secrets."""

    enabled: bool
    active: bool
    expires_at: Optional[int]
    balance_exceeded: bool


@dataclass(frozen=True)
class MintPreviewRisk:
    code: str
    title: str
    description: str


@dataclass(frozen=True)
class EvmMintSimulation:
    """Sanitized Binance simulation result; excludes request IDs and addresses."""

    chain_id: str
    transaction_digest: str
    status: str
    simulation_code: Optional[str]
    simulation_error: Optional[str]
    risks: tuple[MintPreviewRisk, ...]
    requires_app_confirmation: Optional[bool]
    expires_at: Optional[int]
    mint_price_wei: str = "0"
    native_gas_token: str = ""
    network_fee_wei: Optional[str] = None
    network_fee_native: Optional[str] = None
    estimated_gas_limit: Optional[int] = None
    gas_price_wei: Optional[str] = None


@dataclass(frozen=True)
class ConnectionChallenge:
    challenge_id: str = field(repr=False)
    pairing_code: str = field(repr=False)
    url: str = field(repr=False)
    expires_at: Optional[str] = None


@dataclass(frozen=True)
class WalletNetwork:
    network_id: str
    name: str
    simple_name: str
    family: str
    native_token: Optional[str]
    simulated: bool = False


@dataclass(frozen=True)
class WalletAddress:
    network_id: str
    network_name: str
    address: str
    simulated: bool = False


@dataclass(frozen=True)
class WalletBalance:
    network_id: str
    symbol: str
    amount: str
    token_address: Optional[str] = None
    price: Optional[str] = None
    value_usd: Optional[str] = None
    simulated: bool = False
