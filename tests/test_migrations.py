"""
The database survives an upgrade.

This mattered the moment the deployment grew a named volume. Before that, every
database was a throwaway demo file and a schema change cost nothing. Now a
`docker pull` puts new code in front of a file written by the old image, and
`CREATE TABLE IF NOT EXISTS` does exactly nothing to a table that already
exists — so a column added to SCHEMA would never reach it, and the first saga
step after the upgrade would fail on an instance that had already taken real
orders.
"""

from __future__ import annotations

import sqlite3

from contracts.reason_codes import ReasonCode
from core.audit.store import AuditStore
from core.db import ADD_COLUMNS, Database

#: saga_steps exactly as the image before the reason_code column created it.
LEGACY_SCHEMA = """
CREATE TABLE saga_steps (
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
"""


def _legacy_db(tmp_path) -> str:
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        "INSERT INTO saga_steps VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("ord_legacy", 1, "QUOTED", "quote", "OK", '"written by the old image"',
         None, "2026-08-30T00:00:00Z"),
    )
    conn.commit()
    conn.close()
    return str(path)


def test_an_old_database_gains_the_column_and_keeps_its_rows(tmp_path):
    path = _legacy_db(tmp_path)

    db = Database(f"sqlite:///{path}")

    columns = {r["name"] for r in db.conn.execute("PRAGMA table_info(saga_steps)")}
    assert "reason_code" in columns

    audit = AuditStore(db)
    audit.append_step(
        order_id="ord_legacy", state="ROLLED_BACK", action="rail.capture",
        outcome="FAIL", detail="a new row on an old file",
        reason_code=ReasonCode.RAIL_CAPTURE_FAILED,
    )

    steps = audit.list_steps("ord_legacy")
    assert [s["seq"] for s in steps] == [1, 2]
    # The pre-upgrade row is still there and simply has no code, which is the
    # honest answer: nothing recorded one at the time.
    assert steps[0]["reason_code"] is None
    assert steps[1]["reason_code"] == ReasonCode.RAIL_CAPTURE_FAILED.value


def test_migrating_twice_is_a_no_op(tmp_path):
    """Every boot runs this. It has to be safe to run against an already
    migrated file, or the second container start fails."""
    path = _legacy_db(tmp_path)

    db = Database(f"sqlite:///{path}")
    db.migrate()
    db.migrate()

    columns = [r["name"] for r in db.conn.execute("PRAGMA table_info(saga_steps)")]
    assert columns.count("reason_code") == 1


def test_every_added_column_is_nullable(tmp_path):
    """
    Additive and nullable only.

    `ALTER TABLE ... ADD COLUMN` with a NOT NULL and no default fails outright
    on a table that already has rows, and one *with* a default silently
    backfills every historical row with a value that was never true of it. In an
    audit trail that is a fabricated record, which is worse than a null.
    """
    for table, column, decl in ADD_COLUMNS:
        assert "NOT NULL" not in decl.upper(), f"{table}.{column} is NOT NULL"
        assert "DEFAULT" not in decl.upper(), f"{table}.{column} backfills a default"
