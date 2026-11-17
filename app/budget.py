"""Fail-closed shared USD budget accounting for future approved mints.

This module reserves accounting entries only. It cannot sign, submit, or spend.
Unknown transaction outcomes remain liabilities until independently reconciled.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit

from app.database import Database


class BudgetError(ValueError):
    """A reservation or ledger transition violates budget policy."""


class BudgetLedger:
    def __init__(self, database: Database, cap_usd: Decimal = Decimal("5.00"),
                 quote_max_age: timedelta = timedelta(minutes=5)) -> None:
        self.database = database
        self.cap_usd = Decimal(cap_usd)
        self.quote_max_age = quote_max_age
        if not self.cap_usd.is_finite() or self.cap_usd <= 0:
            raise ValueError("Budget cap must be a positive finite amount")

    @staticmethod
    def _amount(value: Any, field: str) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise BudgetError(f"{field} must be a valid decimal amount") from None
        if not amount.is_finite() or amount < 0:
            raise BudgetError(f"{field} must be non-negative and finite")
        return amount

    @staticmethod
    def _timestamp(value: str) -> datetime:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise BudgetError("Timestamp must be ISO 8601 with timezone") from None
        if result.tzinfo is None:
            raise BudgetError("Timestamp must include a timezone")
        return result.astimezone(timezone.utc)

    @staticmethod
    def _https_url(value: str | None, field: str) -> str:
        value = str(value or "").strip()
        try:
            parts = urlsplit(value)
        except ValueError:
            parts = None
        if (parts is None or parts.scheme != "https" or not parts.hostname
                or parts.username or parts.password or parts.query or parts.fragment):
            raise BudgetError(f"{field} must be a credential-free HTTPS URL")
        return value[:1000]

    def _snapshot(self, connection: Any, now: datetime) -> dict[str, Any]:
        rows = connection.execute(
            "SELECT status, max_cost_usd, actual_spend_usd FROM budget_ledger_entries"
        ).fetchall()
        confirmed_spent = Decimal("0")
        reserved = Decimal("0")
        unknown_entries = 0
        for row in rows:
            if row["status"] == "settled":
                confirmed_spent += self._amount(row["actual_spend_usd"] or "0", "actual_spend_usd")
            elif row["status"] == "reserved":
                reserved += self._amount(row["max_cost_usd"] or "0", "max_cost_usd")
            elif row["status"] == "unknown":
                unknown_entries += 1

        # Legacy lifecycle rows have no trusted USD quote. Never assume they cost zero.
        unresolved = connection.execute(
            """SELECT preview_id FROM core_asset_transactions
               WHERE signature IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM budget_ledger_entries b
                   WHERE b.transaction_ref=core_asset_transactions.preview_id
                 )"""
        ).fetchall()
        unknown_count = unknown_entries + len(unresolved)
        liability = confirmed_spent + reserved
        available = max(Decimal("0"), self.cap_usd - liability) if unknown_count == 0 else None
        state = "BLOCKED_UNKNOWN_LIABILITY" if unknown_count else (
            "BUDGET_EXHAUSTED" if liability >= self.cap_usd else "AVAILABLE"
        )
        return {
            "cap_usd": str(self.cap_usd),
            "confirmed_spent_usd": str(confirmed_spent),
            "reserved_usd": str(reserved),
            "unknown_liability_count": unknown_count,
            "available_usd": None if available is None else str(available),
            "state": state,
            "updated_at": now.isoformat(),
            "note": ("Unknown transaction outcomes block new reservations until reconciled."
                     if unknown_count else "Accounting view only; not authorization to spend."),
        }

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise BudgetError("Current time must include a timezone")
        now = now.astimezone(timezone.utc)
        connection = self.database.connect()
        try:
            return self._snapshot(connection, now)
        finally:
            connection.close()

    def reserve(self, *, transaction_ref: str, provider_id: str, chain_identifier: str,
                wallet_address: str, max_cost_usd: Any, native_amount: Any,
                native_currency: str, quote_source_url: str, quote_observed_at: str,
                now: datetime | None = None) -> dict[str, Any]:
        """Atomically reserve a recent evidenced quote; no transaction is submitted."""
        transaction_ref = str(transaction_ref or "").strip()
        provider_id = str(provider_id or "").strip()
        chain_identifier = str(chain_identifier or "").strip()
        wallet_address = str(wallet_address or "").strip()
        native_currency = str(native_currency or "").strip().upper()
        if not all((transaction_ref, provider_id, chain_identifier, wallet_address, native_currency)):
            raise BudgetError("Reservation identity fields are required")
        max_cost = self._amount(max_cost_usd, "max_cost_usd")
        native = self._amount(native_amount, "native_amount")
        quote_url = self._https_url(quote_source_url, "quote_source_url")
        observed = self._timestamp(quote_observed_at)
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise BudgetError("Current time must include a timezone")
        now = now.astimezone(timezone.utc)
        age = now - observed
        if age < timedelta(0) or age > self.quote_max_age:
            raise BudgetError("Cost quote is stale or future-dated")
        if max_cost > self.cap_usd:
            raise BudgetError("Reservation exceeds the shared USD budget cap")

        connection = self.database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status FROM budget_ledger_entries WHERE transaction_ref=?", (transaction_ref,)
            ).fetchone()
            if existing:
                raise BudgetError("Transaction reference already exists; duplicate reservation rejected")
            summary = self._snapshot(connection, now)
            if summary["unknown_liability_count"]:
                raise BudgetError("Unknown transaction liabilities block new budget reservations")
            committed = Decimal(summary["confirmed_spent_usd"]) + Decimal(summary["reserved_usd"])
            if committed + max_cost > self.cap_usd:
                raise BudgetError("Reservation would exceed the shared USD budget cap")
            connection.execute(
                """INSERT INTO budget_ledger_entries
                   (transaction_ref,provider_id,chain_identifier,wallet_address,status,max_cost_usd,
                    native_amount,native_currency,quote_source_url,quote_observed_at,reason,created_at,updated_at)
                   VALUES (?,?,?,?, 'reserved',?,?,?,?,?,'',?,?)""",
                (transaction_ref[:128], provider_id[:64], chain_identifier[:64], wallet_address[:128],
                 str(max_cost), str(native), native_currency[:16], quote_url,
                 observed.isoformat(), now.isoformat(), now.isoformat()),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return self.snapshot(now)

    def mark_unknown(self, transaction_ref: str, reason: str) -> None:
        self._transition(transaction_ref, ("reserved",), "unknown", reason=reason)

    def settle(self, transaction_ref: str, actual_spend_usd: Any, settlement_source_url: str) -> None:
        actual = self._amount(actual_spend_usd, "actual_spend_usd")
        source = self._https_url(settlement_source_url, "settlement_source_url")
        self._transition(transaction_ref, ("reserved", "unknown"), "settled", actual=actual,
                         settlement_source_url=source)

    def release(self, transaction_ref: str, reason: str, *, confirmed_unsubmitted: bool) -> None:
        if confirmed_unsubmitted is not True:
            raise BudgetError("Release requires explicit confirmation the transaction was not submitted")
        if not str(reason or "").strip():
            raise BudgetError("Release reason is required")
        self._transition(transaction_ref, ("reserved", "unknown"), "released", reason=reason,
                         require_unsubmitted=True)

    def _transition(self, transaction_ref: str, expected: tuple[str, ...], target: str, *, reason: str = "",
                    actual: Decimal | None = None, settlement_source_url: str | None = None,
                    require_unsubmitted: bool = False) -> None:
        now = datetime.now(timezone.utc).isoformat()
        connection = self.database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if require_unsubmitted:
                submitted = connection.execute(
                    "SELECT 1 FROM core_asset_transactions WHERE preview_id=? AND signature IS NOT NULL",
                    (str(transaction_ref),),
                ).fetchone()
                if submitted:
                    raise BudgetError("A saved transaction signature prevents releasing this budget reservation")
            placeholders = ",".join("?" for _ in expected)
            cursor = connection.execute(
                """UPDATE budget_ledger_entries SET status=?, actual_spend_usd=?,
                   settlement_source_url=?,reason=?,updated_at=?
                   WHERE transaction_ref=? AND status IN (""" + placeholders + ")",
                (target, None if actual is None else str(actual), settlement_source_url,
                 str(reason)[:500], now, str(transaction_ref), *expected),
            )
            connection.commit()
            if cursor.rowcount != 1:
                raise BudgetError("Ledger transition rejected: reference missing or status changed")
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
