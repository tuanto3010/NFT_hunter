"""Build enabled read-only chain adapters from explicit RPC configuration."""

import logging
import os
from typing import List

from app.config import Settings
from app.discovery.base_scanner import BaseScanner
from app.discovery.bnb_scanner import BnbScanner
from app.discovery.chain_adapter import CHAIN_SPECS, ChainAdapter
from app.discovery.ethereum_scanner import EthereumScanner
from app.discovery.robinhood_scanner import RobinhoodScanner
from app.discovery.solana_scanner import SOLANA_MAINNET_RPC, MagicEdenLaunchpadSource, SolanaScanner

logger = logging.getLogger(__name__)


def build_adapters(settings: Settings, solana_watchlist_provider=None) -> List[ChainAdapter]:
    adapters: List[ChainAdapter] = []
    logs_api_key = os.getenv("ETHERSCAN_API_KEY", "").strip()
    logs_api_url = os.getenv("ETHERSCAN_LOGS_API_URL", "https://api.etherscan.io/v2/api").strip()
    for key in settings.enabled_chains:
        spec = CHAIN_SPECS[key]
        endpoint = os.getenv(spec.rpc_env, "").strip()
        explorer_url = os.getenv(spec.explorer_env, "").strip() if spec.explorer_env else ""
        if key == "robinhood" and not endpoint:
            endpoint = "https://rpc.mainnet.chain.robinhood.com"
        if key == "robinhood" and not explorer_url:
            explorer_url = "https://robinhoodchain.blockscout.com"
        if key == "solana" and not endpoint:
            endpoint = SOLANA_MAINNET_RPC
        if not endpoint:
            logger.info("Skipping %s: %s is not configured", spec.name, spec.rpc_env)
            continue
        if key == "bnb":
            adapter = BnbScanner(endpoint, lookback_blocks=int(os.getenv("EVM_LOOKBACK_BLOCKS", "500")), explorer_api_url=explorer_url,
                                 logs_api_key=logs_api_key, logs_api_url=logs_api_url)
        elif key == "ethereum":
            adapter = EthereumScanner(endpoint, lookback_blocks=int(os.getenv("EVM_LOOKBACK_BLOCKS", "500")), explorer_api_url=explorer_url,
                                      logs_api_key=logs_api_key, logs_api_url=logs_api_url)
        elif key == "base":
            adapter = BaseScanner(endpoint, lookback_blocks=int(os.getenv("EVM_LOOKBACK_BLOCKS", "500")), explorer_api_url=explorer_url,
                                  logs_api_key=logs_api_key, logs_api_url=logs_api_url)
        elif key == "robinhood":
            adapter = RobinhoodScanner(endpoint, lookback_blocks=int(os.getenv("EVM_LOOKBACK_BLOCKS", "500")), explorer_api_url=explorer_url)
        else:
            candidates = [item.strip() for item in os.getenv("SOLANA_MINT_CANDIDATES", "").split(",") if item.strip()]
            candy_machines = [item.strip() for item in os.getenv("SOLANA_CANDY_MACHINE_CANDIDATES", "").split(",") if item.strip()]
            adapter = SolanaScanner(endpoint, candidate_mints=candidates,
                                    das_rpc_url=os.getenv("SOLANA_DAS_RPC_URL", "").strip(),
                                    launchpad_source=MagicEdenLaunchpadSource(),
                                    candy_machine_candidates=candy_machines,
                                    watchlist_provider=solana_watchlist_provider)
        adapters.append(adapter)
    return adapters
