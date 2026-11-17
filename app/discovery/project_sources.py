"""Provider contract. A source must return sourced observations, never guesses."""

from dataclasses import dataclass, field
from typing import Any, List, Optional, Protocol, Union


@dataclass(frozen=True)
class ProjectObservation:
    chain_id: Union[int, str]
    contract_address: str
    source_url: str
    observed_at: str
    project_name: str = ""
    verified: bool = False
    details: dict[str, Any] = field(default_factory=dict)
    nft_standard: str = "unknown"
    mint_price: Optional[str] = None
    native_gas_token: str = ""
    eligibility: str = "unknown"
    verification_status: str = "unknown"
    risk_assessment: str = "high"

    @property
    def asset_address(self) -> str:
        """Address of an EVM contract or Solana mint, retained under legacy field name."""
        return self.contract_address


class ProjectSource(Protocol):
    name: str

    def fetch(self) -> List[ProjectObservation]:
        """Fetch read-only, provenance-bearing observations from a source."""


class NoSourcesConfigured:
    """Explicit empty source set; it makes no claim about ecosystem coverage."""

    name = "none-configured"

    def fetch(self) -> List[ProjectObservation]:
        return []

