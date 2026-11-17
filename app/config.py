"""Environment-backed application settings (no secrets are logged)."""

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Tuple


def load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE entries without overriding process environment."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not (key[0].isalpha() or key[0] == "_") or not all(char.isalnum() or char == "_" for char in key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    chain_id: int = 56
    scan_interval_minutes: int = 15
    database_path: Path = Path("data/nft_hunter.sqlite3")
    log_level: str = "INFO"
    enabled_chains: Tuple[str, ...] = ("bnb", "ethereum", "base", "solana", "robinhood")
    max_concurrent_scans: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        """Load and validate non-secret runtime settings from environment."""
        load_env_file(Path(__file__).resolve().parents[1] / ".env")
        try:
            chain_id = int(os.getenv("BNB_CHAIN_ID", "56"))
            interval = int(os.getenv("SCAN_INTERVAL_MINUTES", "15"))
            concurrency = int(os.getenv("MAX_CONCURRENT_SCANS", "3"))
        except ValueError as exc:
            raise ValueError("BNB_CHAIN_ID and SCAN_INTERVAL_MINUTES must be integers") from exc
        if chain_id != 56:
            raise ValueError("Only BNB Smart Chain mainnet (chain ID 56) is supported")
        if interval < 1:
            raise ValueError("SCAN_INTERVAL_MINUTES must be at least 1")
        if concurrency < 1 or concurrency > 5:
            raise ValueError("MAX_CONCURRENT_SCANS must be between 1 and 5")
        enabled = tuple(dict.fromkeys(
            name.strip().lower() for name in os.getenv(
                "ENABLED_CHAINS", "bnb,ethereum,base,solana,robinhood"
            ).split(",") if name.strip()
        ))
        known_chains = {"bnb", "ethereum", "base", "solana", "robinhood"}
        if not set(enabled).issubset(known_chains):
            raise ValueError("ENABLED_CHAINS contains an unsupported chain name")
        level = os.getenv("LOG_LEVEL", "INFO").upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be a standard logging level")
        return cls(
            chain_id=chain_id,
            scan_interval_minutes=interval,
            database_path=Path(os.getenv("DATABASE_PATH", "data/nft_hunter.sqlite3")),
            log_level=level,
            enabled_chains=enabled,
            max_concurrent_scans=concurrency,
        )

