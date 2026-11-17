"""BNB Smart Chain adapter (EVM shared implementation)."""
from app.discovery.chain_adapter import CHAIN_SPECS
from app.discovery.evm_scanner import EvmScanner


class BnbScanner(EvmScanner):
    def __init__(self, rpc_url: str, **kwargs):
        super().__init__(rpc_url, CHAIN_SPECS["bnb"], **kwargs)

