"""Conservative address validation for provider observations."""

import re


_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_contract_address(value: str) -> bool:
    """Check address shape only; this does not prove a deployed contract exists."""
    return bool(_ADDRESS.fullmatch(value))

