"""
SQLite, and the schema the whole system persists into.

Two decisions worth defending, because they are where the correctness lives:

**`BEGIN IMMEDIATE`.** SQLite's default deferred transaction takes a write lock
only at the first write, which means two concurrent ceiling checks can both read
the same balance before either inserts. That is exactly the race the ceiling
check exists to prevent. `immediate_tx()` takes the write lock up front, so the
read-and-insert is genuinely one atomic step.

**Replay is a UNIQUE constraint, not a SELECT.** `nonces.nonce` is a primary
key. The check is an INSERT, and the IntegrityError *is* the replay. A
"SELECT then INSERT if absent" would be another race, and a slower one.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

DEFAULT_URL = "sqlite:///./pact.db"

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS mandates (
  mandate_id    TEXT PRIMARY KEY,
  body_json     TEXT NOT NULL,
  delegator_pub TEXT NOT NULL,
  delegate_pub  TEXT NOT NULL,
  signature     TEXT NOT NULL,
  signature_ok  INTEGER NOT NULL,
  revoked       INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL
);

-- The ledger. Budget is the SUM over this table inside the same immediate
-- transaction that inserts the new row, never a counter that is read and
-- written back.
CREATE TABLE IF NOT EXISTS reservations (
  reservation_id TEXT PRIMARY KEY,
  mandate_id     TEXT NOT NULL,
  decision_id    TEXT NOT NULL,
  amount_paise   INTEGER NOT NULL,
  state          TEXT NOT NULL CHECK (state IN ('RESERVED','COMMITTED','RELEASED')),
  created_at     TEXT NOT NULL,
  expires_at     TEXT NOT NULL,
  FOREIGN KEY (mandate_id) REFERENCES mandates(mandate_id)
);
CREATE INDEX IF NOT EXISTS ix_res_mandate ON reservations(mandate_id, state);
CREATE INDEX IF NOT EXISTS ix_res_expiry  ON reservations(state, expires_at);

-- Replay defence. The uniqueness violation IS the replay.
CREATE TABLE IF NOT EXISTS nonces (
  nonce      TEXT PRIMARY KEY,
  mandate_id TEXT NOT NULL,
  seen_at    TEXT NOT NULL
);

-- Single use settlement tokens, issued on ALLOW and redeemed once.
CREATE TABLE IF NOT EXISTS settlement_tokens (
  token       TEXT PRIMARY KEY,
  decision_id TEXT NOT NULL,
  mandate_id  TEXT NOT NULL,
  quote_id    TEXT NOT NULL,
  amount_paise INTEGER NOT NULL,
  used_at     TEXT,
  expires_at  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
  decision_id TEXT PRIMARY KEY,
  mandate_id  TEXT NOT NULL,
  verdict     TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  amount_paise INTEGER NOT NULL,
  payee_vpa   TEXT NOT NULL,
  quote_id    TEXT,
  elapsed_ms  REAL NOT NULL,
  body_json   TEXT NOT NULL,
  at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_decisions_at ON decisions(at DESC);

CREATE TABLE IF NOT EXISTS quotes (
  quote_id   TEXT PRIMARY KEY,
  mandate_id TEXT,
  total_paise INTEGER NOT NULL,
  body_json  TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
  order_id       TEXT PRIMARY KEY,
  quote_id       TEXT NOT NULL,
  mandate_id     TEXT NOT NULL,
  decision_id    TEXT,
  state          TEXT NOT NULL,
  amount_paise   INTEGER NOT NULL,
  items_summary  TEXT NOT NULL,
  rail           TEXT NOT NULL,
  rail_order_id  TEXT,
  rail_payment_id TEXT,
  recovered_from TEXT,
  alternative_json TEXT,
  at             TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_orders_at    ON orders(at DESC);
CREATE INDEX IF NOT EXISTS ix_orders_state ON orders(state, updated_at);

CREATE TABLE IF NOT EXISTS saga_steps (
  order_id    TEXT NOT NULL,
  seq         INTEGER NOT NULL,
  state       TEXT NOT NULL,
  action      TEXT NOT NULL,
  outcome     TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  ref         TEXT,
  at          TEXT NOT NULL,
  PRIMARY KEY (order_id, seq)
);

-- Our own idempotency table. API_NOTES.md explains why: Razorpay has no
-- idempotency header, `receipt` covers orders and refunds only and rejects
-- rather than replaying, and capture has no idempotency at all. So we
-- short circuit here, before the call, and every write is safe to repeat.
CREATE TABLE IF NOT EXISTS idempotency (
  idem_key    TEXT PRIMARY KEY,
  operation   TEXT NOT NULL,
  result_json TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

-- Every rail callback we have already applied, keyed so a duplicate or
-- out of order delivery is a no op.
CREATE TABLE IF NOT EXISTS rail_events (
  event_key  TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  payment_id TEXT,
  body_json  TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counters (
  name  TEXT PRIMARY KEY,
  value INTEGER NOT NULL DEFAULT 0
);
"""

#: Wiped by POST /v1/admin/reset, in dependency order.
RESETTABLE_TABLES = (
    "saga_steps",
    "orders",
    "rail_events",
    "idempotency",
    "settlement_tokens",
    "decisions",
    "nonces",
    "reservations",
    "quotes",
    "mandates",
    "counters",
)


def _path_from_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        return url[len("sqlite://") :]
    return url


class Database:
    """
    One connection per thread. SQLite connections are not thread safe, and the
    ASGI server will hand us more than one thread whether we ask for it or not.
    """

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("PACT_DB_URL", DEFAULT_URL)
        self.path = _path_from_url(self.url)
        self._local = threading.local()
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._shared: sqlite3.Connection | None = None
        if self.path == ":memory:":
            # An in-memory database is per connection, so tests would each get
            # a different empty one. Keep a single shared connection instead.
            self._shared = self._new_connection()
        self.migrate()

    def _new_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            isolation_level=None,  # we manage transactions explicitly
            timeout=5.0,
        )
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        c = getattr(self._local, "conn", None)
        if c is None:
            c = self._new_connection()
            self._local.conn = c
        return c

    def migrate(self) -> None:
        self.conn.executescript(SCHEMA)

    @contextmanager
    def immediate_tx(self) -> Iterator[sqlite3.Connection]:
        """
        A write transaction that takes the lock up front.

        This is the whole ceiling story. Under the default deferred behaviour
        two concurrent authorize calls can both SELECT the same balance before
        either INSERTs, and the mandate overspends. `BEGIN IMMEDIATE` serialises
        them, so the sum and the insert are one step. `tests/test_race.py`
        fires twenty concurrent payments and asserts the total never exceeds
        the cap, alongside the naive number for comparison.
        """
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")

    @contextmanager
    def read_tx(self) -> Iterator[sqlite3.Connection]:
        yield self.conn

    def reset(self) -> None:
        """
        Must clear everything in under a second. It gets pressed forty times.

        DELETE rather than DROP so the schema and the WAL stay put; on a
        hackathon-sized database this is single digit milliseconds.
        """
        with self.immediate_tx() as conn:
            for table in RESETTABLE_TABLES:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed tuple

    def close(self) -> None:
        if self._shared is not None:
            self._shared.close()
            self._shared = None
        c = getattr(self._local, "conn", None)
        if c is not None:
            c.close()
            self._local.conn = None
