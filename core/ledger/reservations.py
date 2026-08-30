"""
Ceilings are reserved, not counted.

The correctness point that separates this from a submission that merely looks
like it works. Budget is the SUM of reservations in state RESERVED or COMMITTED,
computed **inside the same immediate transaction that inserts the new row**. Not
a counter read and then written back.

A naive implementation races. Fire twenty concurrent payments at a mandate with
room for three and it will approve more than three, because every one of them
reads the balance before any of them writes. `naive_reserve` below is that
implementation, kept deliberately so `tests/test_race.py` can show the two
numbers side by side.

Lifecycle:

    ALLOW            -> RESERVED, plus a single use settlement token
    capture confirmed -> COMMITTED
    token expiry or rollback -> RELEASED, and the headroom returns

A sweeper releases expired RESERVED rows every 10 seconds, so an agent that
abandons a checkout does not permanently sequester the buyer's budget.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta

from contracts.ids import new_id
from contracts.money import Paise
from contracts.reason_codes import ReasonCode
from contracts.schemas import Constraints, parse_rfc3339, utcnow
from core.db import Database

#: How long an ALLOW holds budget before the sweeper gives it back. Matches the
#: quote TTL, because a reservation outliving its quote is dead weight.
RESERVATION_TTL_SECONDS = 300

ACTIVE_STATES = ("RESERVED", "COMMITTED")


@dataclass(frozen=True, slots=True)
class ReservationOutcome:
    ok: bool
    reason_code: ReasonCode
    reservation_id: str | None = None
    #: Populated on failure so the decision can say what was left.
    headroom_paise: Paise = 0
    detail: str = ""


def _sum_active(conn: sqlite3.Connection, mandate_id: str) -> tuple[Paise, int]:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(amount_paise), 0) AS spent, COUNT(*) AS used
        FROM reservations
        WHERE mandate_id = ? AND state IN ('RESERVED', 'COMMITTED')
        """,
        (mandate_id,),
    ).fetchone()
    return int(row["spent"]), int(row["used"])


class Ledger:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ------------------------------------------------------------ reserve ---

    def reserve(
        self,
        *,
        mandate_id: str,
        decision_id: str,
        amount_paise: Paise,
        constraints: Constraints,
    ) -> ReservationOutcome:
        """
        Check every ceiling and take the budget, atomically.

        The SELECT and the INSERT are inside one BEGIN IMMEDIATE. That is the
        entire difference between this and the naive version, and it is the
        difference between a mandate that holds and one that overspends under
        concurrency.
        """
        if amount_paise > constraints.max_per_txn_paise:
            # Checked outside the transaction because it needs no ledger state,
            # and taking a write lock to reject it would be gratuitous.
            return ReservationOutcome(
                ok=False,
                reason_code=ReasonCode.CEILING_PER_TXN,
                detail=(
                    f"{amount_paise} paise is over the per transaction limit of "
                    f"{constraints.max_per_txn_paise}"
                ),
            )

        now = utcnow()
        expires = (
            parse_rfc3339(now) + timedelta(seconds=RESERVATION_TTL_SECONDS)
        ).isoformat().replace("+00:00", "Z")

        with self.db.immediate_tx() as conn:
            spent, used = _sum_active(conn, mandate_id)
            remaining = constraints.max_total_paise - spent

            if used + 1 > constraints.max_count:
                return ReservationOutcome(
                    ok=False,
                    reason_code=ReasonCode.CEILING_COUNT,
                    headroom_paise=max(0, remaining),
                    detail=f"{used} of {constraints.max_count} purchases already used",
                )

            if spent + amount_paise > constraints.max_total_paise:
                return ReservationOutcome(
                    ok=False,
                    reason_code=ReasonCode.CEILING_TOTAL,
                    headroom_paise=max(0, remaining),
                    detail=f"{amount_paise} paise against {max(0, remaining)} remaining",
                )

            reservation_id = new_id("rsv")
            conn.execute(
                """
                INSERT INTO reservations
                    (reservation_id, mandate_id, decision_id, amount_paise,
                     state, created_at, expires_at)
                VALUES (?, ?, ?, ?, 'RESERVED', ?, ?)
                """,
                (reservation_id, mandate_id, decision_id, amount_paise, now, expires),
            )

        return ReservationOutcome(
            ok=True,
            reason_code=ReasonCode.OK,
            reservation_id=reservation_id,
            headroom_paise=max(0, remaining - amount_paise),
        )

    # ----------------------------------------------------------- lifecycle ---

    def commit(self, decision_id: str) -> bool:
        """Capture confirmed. Idempotent: committing twice changes nothing."""
        with self.db.immediate_tx() as conn:
            cur = conn.execute(
                "UPDATE reservations SET state = 'COMMITTED' "
                "WHERE decision_id = ? AND state = 'RESERVED'",
                (decision_id,),
            )
        return cur.rowcount > 0

    def release(self, decision_id: str) -> Paise:
        """
        Rollback or expiry. The headroom returns.

        Idempotent, and it releases from COMMITTED too — a refunded capture has
        to give the budget back, and by then the reservation is COMMITTED, not
        RESERVED. Returns the amount released so the saga can say the number.
        """
        with self.db.immediate_tx() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount_paise), 0) AS amt FROM reservations "
                "WHERE decision_id = ? AND state IN ('RESERVED', 'COMMITTED')",
                (decision_id,),
            ).fetchone()
            released = int(row["amt"])
            conn.execute(
                "UPDATE reservations SET state = 'RELEASED' "
                "WHERE decision_id = ? AND state IN ('RESERVED', 'COMMITTED')",
                (decision_id,),
            )
        return released

    def sweep(self) -> int:
        """
        Release RESERVED rows past their expiry. Runs every 10 seconds.

        Only RESERVED. A COMMITTED reservation represents money that actually
        moved, and expiring that would hand the buyer back budget they have
        already spent.
        """
        with self.db.immediate_tx() as conn:
            cur = conn.execute(
                "UPDATE reservations SET state = 'RELEASED' "
                "WHERE state = 'RESERVED' AND expires_at < ?",
                (utcnow(),),
            )
        return cur.rowcount

    # ------------------------------------------------------------ headroom ---

    def spent_and_count(self, mandate_id: str) -> tuple[Paise, int]:
        with self.db.read_tx() as conn:
            return _sum_active(conn, mandate_id)

    def headroom_paise(self, mandate_id: str, constraints: Constraints) -> Paise:
        spent, _ = self.spent_and_count(mandate_id)
        return max(0, constraints.max_total_paise - spent)

    def payments_remaining(self, mandate_id: str, constraints: Constraints) -> int:
        _, used = self.spent_and_count(mandate_id)
        return max(0, constraints.max_count - used)

    # --------------------------------------------------------------- naive ---

    def naive_reserve(
        self,
        *,
        mandate_id: str,
        decision_id: str,
        amount_paise: Paise,
        constraints: Constraints,
    ) -> ReservationOutcome:
        """
        The implementation a reasonable team writes, kept so we can show the
        number it produces next to ours.

        Read the balance, decide, then write. No transaction spanning the two.
        Correct single threaded, and it overspends the moment two requests
        overlap. Never called in the request path — `tests/test_race.py` is the
        only caller.
        """
        with self.db.read_tx() as conn:
            spent, used = _sum_active(conn, mandate_id)

        if amount_paise > constraints.max_per_txn_paise:
            return ReservationOutcome(ok=False, reason_code=ReasonCode.CEILING_PER_TXN)
        if used + 1 > constraints.max_count:
            return ReservationOutcome(ok=False, reason_code=ReasonCode.CEILING_COUNT)
        if spent + amount_paise > constraints.max_total_paise:
            return ReservationOutcome(ok=False, reason_code=ReasonCode.CEILING_TOTAL)

        now = utcnow()
        expires = (
            parse_rfc3339(now) + timedelta(seconds=RESERVATION_TTL_SECONDS)
        ).isoformat().replace("+00:00", "Z")
        reservation_id = new_id("rsv")
        with self.db.immediate_tx() as conn:
            conn.execute(
                """
                INSERT INTO reservations
                    (reservation_id, mandate_id, decision_id, amount_paise,
                     state, created_at, expires_at)
                VALUES (?, ?, ?, ?, 'RESERVED', ?, ?)
                """,
                (reservation_id, mandate_id, decision_id, amount_paise, now, expires),
            )
        return ReservationOutcome(
            ok=True, reason_code=ReasonCode.OK, reservation_id=reservation_id
        )
