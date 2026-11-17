"""Conservative decoder for the documented Core Candy Guard account prefix.

Only an empty default guard set with no groups is currently supported. The
variable feature-flag serialization is deliberately left UNKNOWN until the
full guard map for the deployed program version is available in this project.
"""

from __future__ import annotations

import hashlib
import struct
from typing import Any

CORE_CANDY_GUARD_DISCRIMINATOR = hashlib.sha256(b"account:CandyGuard").digest()[:8]


def decode_core_candy_guard(data: bytes) -> dict[str, Any]:
    """Decode only a byte-exact, empty Core Candy Guard configuration."""
    if not isinstance(data, bytes) or len(data) < 85:
        return {"status": "UNKNOWN", "reason": "truncated_account"}
    if data[:8] != CORE_CANDY_GUARD_DISCRIMINATOR:
        return {"status": "UNKNOWN", "reason": "unsupported_discriminator"}
    # Published Core Candy Guard account layout: discriminator, base, bump,
    # authority, then a u64 feature mask at offset 73.
    features = struct.unpack_from("<Q", data, 73)[0]
    if features != 0:
        return {"status": "UNKNOWN", "reason": "nonempty_guard_set_unsupported",
                "feature_mask": features}
    group_count = struct.unpack_from("<I", data, 81)[0]
    if group_count != 0:
        return {"status": "UNKNOWN", "reason": "guard_groups_unsupported",
                "feature_mask": features, "group_count": group_count}
    if len(data) != 85:
        return {"status": "UNKNOWN", "reason": "unexpected_trailing_account_data",
                "feature_mask": features, "group_count": group_count}
    return {"status": "VERIFIED_EMPTY", "feature_mask": 0, "group_count": 0,
            "guards": [], "evidence": "Core Candy Guard account has no enabled guards or guard groups"}
