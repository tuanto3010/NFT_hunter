"""Read-only scanner orchestration with address-based deduplication."""

import logging
from typing import List, Set, Tuple

from app.discovery.nft_detector import is_contract_address
from app.discovery.project_sources import ProjectObservation, ProjectSource

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(self, sources: List[ProjectSource], chain_id: int = 56) -> None:
        self.sources = sources
        self.chain_id = chain_id
        self._seen: Set[Tuple[int, str]] = set()

    def scan(self) -> List[ProjectObservation]:
        """Collect valid BSC observations; source failures are logged and isolated."""
        results: List[ProjectObservation] = []
        for source in self.sources:
            try:
                observations = source.fetch()
            except Exception:
                logger.exception("Discovery source failed: %s", source.name)
                continue
            for item in observations:
                if item.chain_id != self.chain_id or not is_contract_address(item.contract_address):
                    continue
                key = (item.chain_id, item.contract_address.lower())
                if key in self._seen:
                    continue
                self._seen.add(key)
                results.append(item)
        return results

