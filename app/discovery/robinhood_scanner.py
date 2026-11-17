"""Robinhood Chain mainnet adapter; discovery only, no Binance wallet claim."""
from app.discovery.chain_adapter import CHAIN_SPECS
from app.discovery.evm_scanner import EvmScanner

OFFICIAL_RPC = "https://rpc.mainnet.chain.robinhood.com"
EXPLORER_API = "https://robinhoodchain.blockscout.com"


class RobinhoodScanner(EvmScanner):
    def __init__(self, rpc_url: str = OFFICIAL_RPC, **kwargs):
        kwargs.setdefault("explorer_api_url", EXPLORER_API)
        super().__init__(rpc_url, CHAIN_SPECS["robinhood"], **kwargs)

