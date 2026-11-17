"""Shared JSON-RPC discovery for EVM-compatible NFT contracts."""

import json
import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.discovery.chain_adapter import ChainSpec, safe_rpc_source, validate_chain_id
from app.discovery.nft_detector import is_contract_address
from app.discovery.project_sources import ProjectObservation

logger = logging.getLogger(__name__)

TRANSFER_721 = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TRANSFER_SINGLE_1155 = "0xc3d58168c5ae7397731d063d5bbf3d657854427343f4c083240f7aacaa2d0f62"
TRANSFER_BATCH_1155 = "0x4a39dc06d4c0dbc64b70af90fd698a233a518aa5d07e595d983b8c0526c8f7fb"
ZERO_TOPIC = "0x" + "0" * 64
_INDEXED_LOGS_LOCK = threading.Lock()
_LAST_INDEXED_LOGS_REQUEST = 0.0


class RpcError(RuntimeError):
    pass


class RpcClient(Protocol):
    def call(self, method: str, params: list[Any]) -> Any: ...


class JsonRpcClient:
    """Bounded-timeout JSON-RPC client with capped retries and request pacing."""

    def __init__(self, endpoint: str, timeout: float = 12.0, retries: int = 2,
                 min_interval: float = 0.05) -> None:
        if not endpoint.startswith("https://") and not endpoint.startswith("http://127.0.0.1") and not endpoint.startswith("http://localhost"):
            raise ValueError("RPC endpoint must use HTTPS (HTTP is allowed only for localhost)")
        self.endpoint = endpoint
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            with self._lock:
                wait = self.min_interval - (time.monotonic() - self._last_request)
                if wait > 0:
                    time.sleep(wait)
                self._request_id += 1
                request_id = self._request_id
                self._last_request = time.monotonic()
            body = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode()
            request = Request(self.endpoint, body, {"Content-Type": "application/json"})
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                if "error" in payload:
                    raise RpcError("%s returned JSON-RPC error code %s" % (method, payload["error"].get("code", "unknown")))
                if "result" not in payload:
                    raise RpcError("%s: response missing result" % method)
                return payload["result"]
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, RpcError) as exc:
                error = exc
                if isinstance(exc, RpcError) and not isinstance(exc, (HTTPError, URLError)):
                    break
                if attempt < self.retries:
                    time.sleep(min(0.2 * (2 ** attempt), 1.0))
        if isinstance(error, HTTPError):
            detail = "HTTP %d" % error.code
        elif isinstance(error, URLError):
            detail = "network error"
        elif isinstance(error, TimeoutError):
            detail = "timeout"
        elif isinstance(error, json.JSONDecodeError):
            detail = "invalid JSON response"
        elif isinstance(error, RpcError):
            detail = str(error)
        else:
            detail = "unknown transport error"
        raise RpcError("%s failed after retries (%s)" % (method, detail)) from None


def _decode_abi_string(value: str) -> str:
    try:
        raw = bytes.fromhex(value.removeprefix("0x"))
        if len(raw) >= 64:
            offset = int.from_bytes(raw[:32], "big")
            if offset + 32 <= len(raw):
                length = int.from_bytes(raw[offset:offset + 32], "big")
                return raw[offset + 32:offset + 32 + length].decode("utf-8", "replace").rstrip("\x00")
        return ""
    except (ValueError, UnicodeError):
        return ""


class EvmScanner:
    """Fetch recent mint-transfer logs and verify NFT interfaces via eth_call.

    This scans a bounded recent block window. It is not a historical indexer.
    """

    spec: ChainSpec

    def __init__(self, rpc_url: str, spec: ChainSpec, rpc: Optional[RpcClient] = None,
                 lookback_blocks: int = 500, block_chunk: int = 100,
                 explorer_api_url: str = "", overlap_blocks: int = 12,
                 logs_api_key: str = "",
                 logs_api_url: str = "https://api.etherscan.io/v2/api") -> None:
        if lookback_blocks < 1 or block_chunk < 1:
            raise ValueError("lookback_blocks and block_chunk must be positive")
        self.spec = spec
        self.rpc = rpc or JsonRpcClient(rpc_url)
        self.lookback_blocks = lookback_blocks
        self.block_chunk = block_chunk
        self.overlap_blocks = max(1, overlap_blocks)
        self.explorer_api_url = explorer_api_url.rstrip("/")
        self.logs_api_key = logs_api_key.strip()
        self.logs_api_url = logs_api_url
        self.last_scanned_block: Optional[int] = None

    def _eth_call(self, address: str, data: str) -> str:
        return self.rpc.call("eth_call", [{"to": address, "data": data}, "latest"])

    def _supports(self, address: str, interface_id: str) -> bool:
        try:
            result = self._eth_call(address, "0x01ffc9a7" + interface_id.lower().removeprefix("0x").zfill(64))
            return int(result, 16) == 1
        except (RpcError, ValueError, TypeError):
            return False

    def inspect_contract(self, address: str) -> Dict[str, Any]:
        """Return best-effort standard/name/symbol and optional verification info."""
        if not is_contract_address(address):
            raise ValueError("Invalid EVM contract address")
        code = self.rpc.call("eth_getCode", [address, "latest"])
        if not code or code == "0x":
            return {"nft_standard": "unknown", "verification_status": "unverified", "risk_assessment": "high"}
        standard = "unknown"
        if self._supports(address, "0x80ac58cd"):
            standard = "ERC-721"
        elif self._supports(address, "0xd9b67a26"):
            standard = "ERC-1155"
        name = symbol = uri = ""
        try:
            if standard == "ERC-721":
                name = _decode_abi_string(self._eth_call(address, "0x06fdde03"))
                symbol = _decode_abi_string(self._eth_call(address, "0x95d89b41"))
            elif standard == "ERC-1155":
                uri = _decode_abi_string(self._eth_call(address, "0x0e89341c" + "0" * 64))
        except RpcError:
            logger.info("Optional NFT metadata calls failed for %s", address)
        verification = self.check_verification(address)
        return {
            "nft_standard": standard,
            "project_name": name or symbol,
            "metadata": {"name": name, "symbol": symbol, "uri_template": uri},
            "verification_status": verification["status"],
            "risk_assessment": "high" if verification["status"] != "verified" else "needs_review",
            "mint_functions": self.inspect_mint_functions(verification.get("abi")),
        }

    def check_verification(self, address: str) -> Dict[str, Any]:
        """Read explicit explorer verification data; never infer verified."""
        if self.explorer_api_url:
            url = "%s/api/v2/smart-contracts/%s" % (self.explorer_api_url, address)
            request = Request(url, headers={"Accept": "application/json"})
            try:
                with urlopen(request, timeout=8) as response:
                    data = json.loads(response.read().decode("utf-8"))
                verified = data.get("is_verified") is True
                abi = data.get("abi") if verified else None
                return {"status": "verified" if verified else "unverified", "abi": abi}
            except Exception as exc:
                logger.info("Explorer verification unavailable for %s (%s)", address, type(exc).__name__)
                return {"status": "unknown", "abi": None}

        chain_id = str(self.spec.chain_id)
        # BNB Chain's Etherscan V2 API no longer has a free tier. Use the
        # public Sourcify repository for BNB source/ABI matching instead.
        if self.logs_api_key and chain_id in {"1", "8453"}:
            try:
                data = self._etherscan_v2_request({
                    "module": "contract", "action": "getsourcecode", "address": address,
                })
                records = data.get("result") if data.get("status") == "1" else None
                if not isinstance(records, list) or not records or not isinstance(records[0], dict):
                    return {"status": "unknown", "abi": None}
                record = records[0]
                source = record.get("SourceCode")
                abi_text = record.get("ABI")
                if (not isinstance(source, str) or not source.strip()
                        or not isinstance(abi_text, str)
                        or abi_text.lower().startswith("contract source code not verified")):
                    return {"status": "unverified", "abi": None}
                try:
                    abi = json.loads(abi_text)
                except json.JSONDecodeError:
                    return {"status": "unknown", "abi": None}
                if not isinstance(abi, list):
                    return {"status": "unknown", "abi": None}
                return {"status": "verified", "abi": abi}
            except Exception as exc:
                logger.info("Indexed source verification unavailable for %s (%s)", address, type(exc).__name__)
                return {"status": "unknown", "abi": None}

        try:
            url = "https://sourcify.dev/server/v2/contract/%s/%s?fields=abi" % (chain_id, address)
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "Binance-NFT-Hunter/0.1"})
            with urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            if not isinstance(data, dict) or data.get("match") not in {"match", "exact_match"}:
                return {"status": "unknown", "abi": None}
            abi = data.get("abi")
            if not isinstance(abi, list):
                return {"status": "unknown", "abi": None}
            return {"status": "verified", "abi": abi}
        except Exception as exc:
            logger.info("Sourcify verification unavailable for %s (%s)", address, type(exc).__name__)
            return {"status": "unknown", "abi": None}


    def _etherscan_v2_request(self, parameters: Dict[str, str]) -> Dict[str, Any]:
        """Call Etherscan V2 without ever logging its API key or response text."""
        chain_id = str(self.spec.chain_id)
        if chain_id == "56":
            raise RpcError(
                "Etherscan V2 BNB logs are not used; configure BNB_RPC_URL with a log-capable endpoint"
            )
        if chain_id not in {"1", "8453"}:
            raise RpcError("Etherscan V2 fallback is not enabled for this chain")

        global _LAST_INDEXED_LOGS_REQUEST
        with _INDEXED_LOGS_LOCK:
            wait = 0.5 - (time.monotonic() - _LAST_INDEXED_LOGS_REQUEST)
            if wait > 0:
                time.sleep(wait)
            _LAST_INDEXED_LOGS_REQUEST = time.monotonic()

        query = urlencode({
            **parameters,
            "chainid": chain_id,
            "apikey": self.logs_api_key,
        })
        request = Request(
            "%s?%s" % (self.logs_api_url.rstrip("?"), query),
            headers={"Accept": "application/json", "User-Agent": "Binance-NFT-Hunter/0.1"},
        )
        try:
            with urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise RpcError("Etherscan V2 request failed (%s)" % type(exc).__name__) from None
        if not isinstance(payload, dict):
            raise RpcError("Etherscan V2 returned invalid data")
        return payload

    @staticmethod
    def _is_mint_function(name: str) -> bool:
        normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
        return "mint" in re.split(r"[^a-z0-9]+", normalized.lower())

    @staticmethod
    def inspect_mint_functions(abi: Any) -> List[Dict[str, Any]]:
        if not isinstance(abi, list):
            return []
        return [
            {"name": item.get("name"), "inputs": item.get("inputs", []),
             "payable": item.get("payable", item.get("stateMutability") == "payable"),
             "stateMutability": item.get("stateMutability")}
            for item in abi if isinstance(item, dict) and item.get("type") == "function"
            and EvmScanner._is_mint_function(str(item.get("name", "")))
        ]

    def estimate_gas(self, transaction: Dict[str, Any]) -> int:
        """Estimate gas only for a caller-supplied read-only candidate transaction."""
        recipient = transaction.get("to", "")
        if not is_contract_address(recipient):
            raise ValueError("Gas estimate requires a valid contract address")
        raw_value = transaction.get("value", "0x0")
        value = int(raw_value, 16) if isinstance(raw_value, str) and raw_value.startswith("0x") else int(raw_value)
        if value != 0:
            raise ValueError("NFT discovery gas estimates reject native value transfers")
        result = self.rpc.call("eth_estimateGas", [transaction])
        return int(result, 16)

    def _mint_logs(self, start: int, end: int) -> List[Dict[str, Any]]:
        logs: List[Dict[str, Any]] = []
        for topic in (TRANSFER_721, TRANSFER_SINGLE_1155, TRANSFER_BATCH_1155):
            try:
                topic_logs = self.rpc.call("eth_getLogs", [{
                    "fromBlock": hex(start), "toBlock": hex(end), "topics": [topic],
                }])
            except RpcError as exc:
                if str(self.spec.chain_id) == "56":
                    code_match = re.search(r"JSON-RPC error code (-?\d+)", str(exc))
                    if code_match and code_match.group(1) == "-32601":
                        message = "BNB RPC explicitly reports eth_getLogs as unsupported; choose a log-capable BNB RPC"
                    elif code_match:
                        message = (
                            "BNB RPC rejected eth_getLogs (JSON-RPC error code %s); check provider query limits, "
                            "rate limits, and requested block range" % code_match.group(1)
                        )
                    else:
                        http_match = re.search(r"failed after retries \(HTTP (\d{3})\)", str(exc))
                        if http_match and http_match.group(1) == "429":
                            message = "BNB RPC rate-limited eth_getLogs (HTTP 429); reduce request frequency or scan range"
                        elif http_match and http_match.group(1) in {"401", "403"}:
                            message = "BNB RPC denied eth_getLogs access (HTTP %s); check endpoint credentials and permissions" % http_match.group(1)
                        elif http_match:
                            message = "BNB RPC returned HTTP %s for eth_getLogs" % http_match.group(1)
                        elif "timeout" in str(exc).lower():
                            message = "BNB eth_getLogs timed out after retries; reduce scan range or check provider load"
                        elif "network error" in str(exc).lower():
                            message = "BNB eth_getLogs could not reach the RPC after retries; check connectivity"
                        else:
                            message = "BNB eth_getLogs transport failed after retries; check connectivity and provider limits"
                    raise RpcError(message) from None
                if not self.logs_api_key:
                    raise RpcError(
                        "eth_getLogs request failed; configure ETHERSCAN_API_KEY for the optional Ethereum/Base "
                        "indexed-logs fallback or check the RPC error and provider limits"
                    ) from None
                topic_logs = self._indexed_logs(start, end, topic)
            logs.extend(topic_logs)
        return logs

    def _indexed_logs(self, start: int, end: int, topic: str) -> List[Dict[str, Any]]:
        """Fetch a bounded topic query from the optional Etherscan V2 API.

        The API key is sent only in the HTTPS query and is never included in
        exceptions or logs. This fallback is available for Etherscan V2 chain
        IDs supported by this project: BNB, Ethereum, and Base.
        """
        chain_id = str(self.spec.chain_id)
        if chain_id not in {"1", "8453"}:
            raise RpcError("indexed event-log fallback is not enabled for this chain")

        payload = self._etherscan_v2_request({
            "module": "logs",
            "action": "getLogs",
            "fromBlock": str(start),
            "toBlock": str(end),
            "topic0": topic,
        })
        result = payload.get("result")
        if payload.get("status") == "1" and isinstance(result, list):
            return result
        if payload.get("status") == "0" and result == []:
            return []
        if payload.get("status") == "0" and isinstance(result, str) and result.lower() in {
            "no records found", "no logs found",
        }:
            return []
        # Do not include provider response text: it may echo submitted values.
        raise RpcError("indexed event-log provider rejected the request")

    @staticmethod
    def _is_mint_log(log: Dict[str, Any]) -> bool:
        topics = [str(topic).lower() for topic in log.get("topics", [])]
        signature = topics[0] if topics else ""
        if signature == TRANSFER_721:
            return len(topics) > 1 and topics[1] == ZERO_TOPIC
        if signature in (TRANSFER_SINGLE_1155, TRANSFER_BATCH_1155):
            return len(topics) > 2 and topics[2] == ZERO_TOPIC
        return False

    def scan(self, from_block: Optional[int] = None) -> List[ProjectObservation]:
        actual_chain = int(self.rpc.call("eth_chainId", []), 16)
        validate_chain_id(int(self.spec.chain_id), actual_chain)
        head = int(self.rpc.call("eth_blockNumber", []), 16)
        if from_block is None:
            start = max(0, head - self.lookback_blocks + 1)
        else:
            start = max(0, from_block - self.overlap_blocks + 1)
        results: List[ProjectObservation] = []
        seen_this_scan: set[Tuple[int, str]] = set()
        for chunk_start in range(start, head + 1, self.block_chunk):
            chunk_end = min(head, chunk_start + self.block_chunk - 1)
            try:
                logs = self._mint_logs(chunk_start, chunk_end)
            except RpcError:
                logger.exception("Mint-log scan failed on %s", self.spec.name)
                raise
            for log in logs:
                address = str(log.get("address", ""))
                if not self._is_mint_log(log) or not is_contract_address(address):
                    continue
                key = (int(self.spec.chain_id), address.lower())
                if key in seen_this_scan:
                    continue
                seen_this_scan.add(key)
                try:
                    metadata = self.inspect_contract(address)
                except RpcError:
                    logger.exception("Contract inspection failed for %s on %s", address, self.spec.name)
                    metadata = {"nft_standard": "unknown", "verification_status": "unknown", "risk_assessment": "high"}
                results.append(ProjectObservation(
                    chain_id=int(self.spec.chain_id), contract_address=address,
                    source_url=safe_rpc_source(getattr(self.rpc, "endpoint", "")), observed_at=datetime.now(timezone.utc).isoformat(),
                    project_name=metadata.get("project_name", ""), verified=metadata.get("verification_status") == "verified",
                    nft_standard=metadata.get("nft_standard", "unknown"),
                    native_gas_token=self.spec.native_token,
                    verification_status=metadata.get("verification_status", "unknown"),
                    risk_assessment=metadata.get("risk_assessment", "high"),
                    details={"chain": self.spec.key, "nft_standard": metadata.get("nft_standard", "unknown"),
                             "mint_price": None, "native_gas_token": self.spec.native_token,
                             "eligibility": "unknown", "verification_status": metadata.get("verification_status", "unknown"),
                             "risk_assessment": metadata.get("risk_assessment", "high"),
                             "mint_functions": metadata.get("mint_functions", []), "metadata": metadata.get("metadata", {})},
                ))
        self.last_scanned_block = head
        return results

