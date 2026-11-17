"""Binance wallet adapter tests fake the documented ``baw`` CLI boundary."""

import json
import subprocess
import unittest

from app.wallet.binance_wallet import BinanceAgenticWalletAdapter, BinanceWalletError


def completed(data, returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["baw"], returncode=returncode,
        stdout=json.dumps({"success": True, "data": data}) if returncode == 0 else "",
        stderr=stderr,
    )


class FakeBawRunner:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, command, timeout):
        self.calls.append((tuple(command), timeout))
        key = tuple(command[1:])
        result = self.responses.get(key)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(command, timeout)
        if result is None:
            raise AssertionError("unexpected baw command: %r" % (key,))
        return result


class BinanceWalletAdapterTests(unittest.TestCase):
    def test_reads_claimed_agent_id_without_exposing_credentials(self):
        runner = FakeBawRunner({
            ("wallet", "agent-id", "--json"): completed({"claimed": True, "handle": "SampleAgent.ai"}),
        })
        wallet = BinanceAgenticWalletAdapter(runner=runner)
        self.assertEqual(wallet.get_agent_id_handle(), "sampleagent")
        self.assertEqual(runner.calls[0][0][1:], ("wallet", "agent-id", "--json"))

    def test_unclaimed_agent_id_returns_none(self):
        runner = FakeBawRunner({
            ("wallet", "agent-id", "--json"): completed({"claimed": False}),
        })
        self.assertIsNone(BinanceAgenticWalletAdapter(runner=runner).get_agent_id_handle())

    def test_cli_version_is_checked_against_official_skill_requirement(self):
        runner = FakeBawRunner({
            ("cli-check", "--required-version", "1.12.0", "--json"):
                completed({"currentCliVersion": "1.12.0", "needUpdateCli": False}),
        })
        self.assertEqual(BinanceAgenticWalletAdapter(runner=runner).verify_cli_version(), "1.12.0")

        old_runner = FakeBawRunner({
            ("cli-check", "--required-version", "1.12.0", "--json"):
                completed({"currentCliVersion": "1.0.0", "needUpdateCli": True}),
        })
        with self.assertRaises(BinanceWalletError):
            BinanceAgenticWalletAdapter(runner=old_runner).verify_cli_version()

    def test_reads_status_networks_addresses_and_balances_from_documented_commands(self):
        runner = FakeBawRunner({
            ("wallet", "status", "--json"): completed({"status": "CONNECTED"}),
            ("wallet", "chains", "--json"): completed([
                {"binanceChainId": "56", "name": "BNB Smart Chain", "simpleName": "BSC"},
                {"binanceChainId": "1", "name": "Ethereum", "simpleName": "ETH"},
                {"binanceChainId": "8453", "name": "Base", "simpleName": "Base"},
                {"binanceChainId": "CT_501", "name": "Solana", "simpleName": "Solana"},
                {"binanceChainId": "4663", "name": "Robinhood", "simpleName": "Robinhood"},
            ]),
            ("wallet", "address", "--json"): completed({"addresses": [
                {"binanceChainId": "56", "chainName": "BSC", "address": "0x" + "a" * 40},
                {"binanceChainId": "CT_501", "chainName": "Solana", "address": "SimulatedTestAvoid"},
            ]}),
            ("wallet", "balance", "--hideLowValue", "false", "--json"): completed([
                {"symbol": "BNB", "address": "", "binanceChainId": "56",
                 "balance": "1.25", "price": "500", "value": "625"},
            ]),
            ("wallet", "chains", "--json"): completed([
                {"binanceChainId": "56", "name": "BNB Smart Chain", "simpleName": "BSC"},
                {"binanceChainId": "1", "name": "Ethereum", "simpleName": "ETH"},
                {"binanceChainId": "8453", "name": "Base", "simpleName": "Base"},
                {"binanceChainId": "CT_501", "name": "Solana", "simpleName": "Solana"},
                {"binanceChainId": "4663", "name": "Robinhood", "simpleName": "Robinhood"},
            ]),
            ("wallet", "balance", "--binanceChainId", "4663", "--hideLowValue", "false", "--json"):
                completed([{"symbol": "ETH", "binanceChainId": "4663", "balance": "0.5"}]),
        })
        wallet = BinanceAgenticWalletAdapter(runner=runner)
        self.assertTrue(wallet.get_status().connected)
        networks = wallet.get_supported_networks()
        self.assertEqual([network.network_id for network in networks], ["56", "1", "8453", "CT_501", "4663"])
        self.assertEqual(networks[3].family, "solana")
        self.assertEqual(networks[3].native_token, "SOL")
        self.assertEqual(wallet.get_addresses()[0].address, "0x" + "a" * 40)
        self.assertEqual(wallet.get_balances()[0].value_usd, "625")
        rh_balances = wallet.get_balances("4663")
        self.assertEqual(rh_balances[0].network_id, "4663")
        self.assertEqual(rh_balances[0].symbol, "ETH")
        self.assertTrue(all("--json" in call[0] for call in runner.calls))

    def test_qr_signin_and_verification_use_exact_documented_commands(self):
        runner = FakeBawRunner({
            ("auth", "signin", "--json"): completed({
                "qrCodeId": "11111111-1111-4111-8111-111111111111", "pairingCode": "492031",
                "urlForWeb": "https://app.binance.com/uni-qr/private-pairing", "expireAt": "123456",
            }),
            ("auth", "verify", "--qrCodeId", "11111111-1111-4111-8111-111111111111", "--json"): completed({"status": "SUCCESS"}),
            ("wallet", "status", "--json"): completed({"status": "CONNECTED"}),
        })
        wallet = BinanceAgenticWalletAdapter(runner=runner)
        challenge = wallet.begin_connection()
        self.assertEqual(challenge.challenge_id, "11111111-1111-4111-8111-111111111111")
        self.assertNotIn("492031", repr(challenge))
        self.assertNotIn("private-pairing", repr(challenge))
        self.assertNotIn(challenge.challenge_id, repr(challenge))
        self.assertTrue(wallet.complete_connection(challenge.challenge_id).connected)
        verify_command, timeout = runner.calls[1]
        self.assertEqual(verify_command[1:3], ("auth", "verify"))
        self.assertGreaterEqual(timeout, 300)

    def test_disconnect_and_already_connected_use_supported_operations(self):
        runner = FakeBawRunner({
            ("auth", "signin", "--json"): completed({"status": "ALREADY_CONNECTED"}),
            ("auth", "signout", "--json"): completed({"status": "LOGGED_OUT"}),
        })
        wallet = BinanceAgenticWalletAdapter(runner=runner)
        self.assertIsNone(wallet.begin_connection())
        wallet.disconnect()
        self.assertEqual([call[0][1:3] for call in runner.calls], [("auth", "signin"), ("auth", "signout")])

    def test_signin_refuses_non_official_or_non_https_url(self):
        runner = FakeBawRunner({
            ("auth", "signin", "--json"): completed({
                "qrCodeId": "11111111-1111-4111-8111-111111111111",
                "pairingCode": "492031",
                "urlForWeb": "https://attacker.invalid/pair",
            }),
        })
        with self.assertRaises(BinanceWalletError):
            BinanceAgenticWalletAdapter(runner=runner).begin_connection()

    def test_rejected_qr_authentication_does_not_claim_connection(self):
        runner = FakeBawRunner({
            ("auth", "verify", "--qrCodeId", "qr-id", "--json"):
                completed({"status": "AUTH_REJECTED"}),
        })
        with self.assertRaises(BinanceWalletError):
            BinanceAgenticWalletAdapter(runner=runner).complete_connection("qr-id")
        self.assertEqual(len(runner.calls), 1)

    def test_unknown_network_is_not_sent_to_balance_command(self):
        runner = FakeBawRunner({
            ("wallet", "chains", "--json"): completed([]),
        })
        with self.assertRaises(ValueError):
            BinanceAgenticWalletAdapter(runner=runner).get_balances("4663")
        self.assertEqual(len(runner.calls), 1)

    def test_errors_are_sanitized_and_never_echo_raw_cli_output(self):
        runner = FakeBawRunner({
            ("wallet", "status", "--json"): completed({}, returncode=1,
                                                     stderr="secret-session-token"),
        })
        with self.assertRaises(BinanceWalletError) as caught:
            BinanceAgenticWalletAdapter(runner=runner).get_status()
        self.assertNotIn("secret-session-token", str(caught.exception))

    def test_adapter_has_no_transaction_signing_or_broadcast_methods(self):
        wallet = BinanceAgenticWalletAdapter(runner=FakeBawRunner({}))
        for method in ("sign", "sign_transaction", "contract_call", "execute",
                       "send_transaction", "broadcast", "broadcast_transaction"):
            self.assertFalse(hasattr(wallet, method), method)

    def test_missing_cli_is_reported_without_credentials_or_traceback_details(self):
        runner = FakeBawRunner({
            ("wallet", "status", "--json"): FileNotFoundError("private path and session token"),
        })
        with self.assertRaises(BinanceWalletError) as caught:
            BinanceAgenticWalletAdapter(runner=runner).get_status()
        self.assertNotIn("session token", str(caught.exception))
        self.assertIn("not installed", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
