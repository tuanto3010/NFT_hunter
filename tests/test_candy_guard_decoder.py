import struct
import unittest

from app.discovery.candy_guard_decoder import CORE_CANDY_GUARD_DISCRIMINATOR, decode_core_candy_guard


class CoreCandyGuardDecoderTests(unittest.TestCase):
    def account(self, features=0, groups=0, trailing=b""):
        prefix = bytearray(85)
        prefix[:8] = CORE_CANDY_GUARD_DISCRIMINATOR
        struct.pack_into("<Q", prefix, 73, features)
        struct.pack_into("<I", prefix, 81, groups)
        return bytes(prefix) + trailing

    def test_empty_default_guard_configuration_is_supported(self):
        result = decode_core_candy_guard(self.account())
        self.assertEqual(result["status"], "VERIFIED_EMPTY")
        self.assertEqual(result["guards"], [])

    def test_nonempty_feature_layout_is_unknown(self):
        result = decode_core_candy_guard(self.account(features=1))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "nonempty_guard_set_unsupported")

    def test_guard_groups_are_unknown(self):
        result = decode_core_candy_guard(self.account(groups=1))
        self.assertEqual(result["reason"], "guard_groups_unsupported")

    def test_unknown_discriminator_truncation_and_trailing_bytes_stay_unknown(self):
        bad = bytearray(self.account())
        bad[0] ^= 1
        self.assertEqual(decode_core_candy_guard(bytes(bad))["reason"], "unsupported_discriminator")
        self.assertEqual(decode_core_candy_guard(b"short")["reason"], "truncated_account")
        self.assertEqual(decode_core_candy_guard(self.account(trailing=b"\x00"))["reason"],
                         "unexpected_trailing_account_data")


if __name__ == "__main__":
    unittest.main()
