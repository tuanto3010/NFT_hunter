"""Tests for the local simulated wallet; no RPC calls or real accounts."""

import unittest

from app.wallet.dummy_wallet import DummyWalletAdapter


class DummyWalletTests(unittest.TestCase):
    def setUp(self):
        self.wallet = DummyWalletAdapter()

    def test_connection_address_and_fake_balance_are_clearly_simulated(self):
        self.assertFalse(self.wallet.is_connected())
        with self.assertRaises(RuntimeError):
            self.wallet.get_address()
        address = self.wallet.connect()
        self.assertTrue(self.wallet.is_connected())
        self.assertTrue(address.startswith("SIMULATED-WALLET-"))
        balance = self.wallet.get_balance()
        self.assertTrue(balance.simulated)
        self.assertEqual(balance.token, "BNB")
        self.assertEqual(balance.amount, "1.25000000")
        self.wallet.disconnect()
        self.assertFalse(self.wallet.is_connected())
        with self.assertRaises(RuntimeError):
            self.wallet.get_balance()

    def test_network_switching_is_simulated_and_validated(self):
        self.wallet.connect()
        for network_id, token in ((1, "ETH"), (8453, "ETH"), (4663, "ETH"),
                                  ("solana-mainnet-beta", "SOL"), (56, "BNB")):
            network = self.wallet.switch_network(network_id)
            self.assertEqual(network.network_id, network_id)
            self.assertEqual(network.native_token, token)
            self.assertTrue(network.simulated)
            self.assertTrue(self.wallet.get_balance().simulated)
        with self.assertRaises(ValueError):
            self.wallet.switch_network(999)

    def test_preparation_approval_and_success_outcome_are_local_simulation(self):
        self.wallet.connect()
        transaction = self.wallet.prepare_transaction(
            "not-a-real-chain-address", "mint", {"quantity": "1"}, "0",
        )
        self.assertTrue(transaction.simulated)
        self.assertEqual(transaction.decision, "pending")
        with self.assertRaises(ValueError):
            self.wallet.simulate_transaction(transaction.transaction_id)
        approved = self.wallet.approve_transaction(transaction.transaction_id)
        self.assertEqual(approved.decision, "approved")
        outcome = self.wallet.simulate_transaction(transaction.transaction_id)
        self.assertEqual(outcome.status, "simulated_success")
        self.assertTrue(outcome.simulated)
        self.assertFalse(outcome.broadcast)
        self.assertTrue(outcome.simulated_reference.startswith("SIMULATED-"))
        with self.assertRaises(ValueError):
            self.wallet.reject_transaction(transaction.transaction_id)

    def test_rejection_is_simulated_without_execution(self):
        self.wallet.connect()
        transaction = self.wallet.prepare_transaction("target", "mint", {})
        rejected = self.wallet.reject_transaction(transaction.transaction_id)
        self.assertEqual(rejected.decision, "rejected")
        outcome = self.wallet.simulate_transaction(transaction.transaction_id)
        self.assertEqual(outcome.status, "simulated_rejected")
        self.assertFalse(outcome.broadcast)

    def test_invalid_value_and_unknown_transactions_are_rejected(self):
        self.wallet.connect()
        for value in ("-1", "NaN", "Infinity", "not-a-number"):
            with self.assertRaises(ValueError):
                self.wallet.prepare_transaction("target", "mint", {}, value)
        with self.assertRaises(ValueError):
            self.wallet.approve_transaction("sim-tx-unknown")
        with self.assertRaises(ValueError):
            self.wallet.prepare_transaction("", "mint", {})

    def test_dummy_has_no_sign_or_broadcast_capability(self):
        forbidden = (
            "sign", "sign_transaction", "broadcast", "broadcast_transaction",
            "send_transaction", "submit_transaction",
        )
        for name in forbidden:
            self.assertFalse(hasattr(self.wallet, name), name)
        self.assertFalse(hasattr(self.wallet, "rpc"))
        self.assertFalse(hasattr(self.wallet, "private_key"))


if __name__ == "__main__":
    unittest.main()
