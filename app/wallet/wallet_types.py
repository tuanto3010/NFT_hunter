"""Data types for the local, non-broadcasting dummy wallet."""

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class NetworkInfo:
    network_id: int | str
    name: str
    native_token: str
    simulated: bool = True


@dataclass(frozen=True)
class SimulatedBalance:
    amount: str
    token: str
    simulated: bool = True


@dataclass(frozen=True)
class PreparedTransaction:
    transaction_id: str
    network_id: int | str
    destination: str
    method: str
    parameters: Tuple[Tuple[str, str], ...]
    value: str
    decision: str = "pending"
    simulated: bool = True


@dataclass(frozen=True)
class SimulatedOutcome:
    transaction_id: str
    status: str
    simulated_reference: str
    broadcast: bool = False
    simulated: bool = True
