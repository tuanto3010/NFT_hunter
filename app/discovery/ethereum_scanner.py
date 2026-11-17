"""Ethereum mainnet adapter (EVM shared implementation)."""
from app.discovery.chain_adapter import CHAIN_SPECS
from app.discovery.evm_scanner import EvmScanner


class EthereumScanner(EvmScanner):
    def __init__(self, rpc_url: str, **kwargs):
        super().__init__(rpc_url, CHAIN_SPECS["ethereum"], **kwargs)

