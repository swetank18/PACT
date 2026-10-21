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


# ------------------------------------------------- the 2026-10-21 rename ---


def _to_pre_rename(value, parent=None):
    """The inverse of `contracts.legacy.upgrade_keys`: JSON as the old image
    wrote it."""
    if isinstance(value, dict):
        out = {}
        for key, inner in value.items():
            old = {"payee": "payee_vpa", "merchant_payee": "merchant_vpa"}.get(key, key)
            if key.endswith("_minor"):
                old = key[: -len("_minor")] + "_paise"
            if key == "account" and parent == "delegator":
                old = "vpa"
            if key == "currency" and parent == "constraints":
                continue  # the old image signed no currency into a mandate
            out[old] = _to_pre_rename(inner, key)
        return out
    if isinstance(value, list):
        return [_to_pre_rename(v, parent) for v in value]
    return value


def test_a_database_written_before_the_rename_still_reads(tmp_path, keys):
    """
    The contract renamed `*_paise` to `*_minor` and `payee_vpa` to `payee`.
    A volume written by the image before that has the old column names and the
    old keys inside every stored body. Built here from current code and then
    turned back into that shape, so the test cannot drift from what the old
    image actually wrote in the columns it renames.
    """
    import json
    from datetime import datetime, timedelta, timezone

    from contracts.crypto import sign
    from contracts.ids import new_id
    from contracts.schemas import Mandate, Order, QuoteItemRequest
    from core.db import RENAME_COLUMNS
    from core.mandate.store import MandateStore
    from merchant.quote import QuoteEngine
    from merchant.saga import OrderStore

    priv, pub = keys
    path = tmp_path / "pre-rename.db"
    db = Database(f"sqlite:///{path}")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    mandate = Mandate(
        mandate_id=new_id("mnd"),
        delegator={"account": "swetank@okaxis", "pubkey": pub},
        delegate={"agent_id": "buyer_agent_v1", "pubkey": pub},
        intent="restock office supplies",
        constraints={
            "currency": "INR",
            "max_per_txn_minor": 500_000, "max_total_minor": 1_500_000, "max_count": 5,
            "merchant_allowlist": ["deskkit@razorpay"], "category_allowlist": ["stationery"],
            "valid_from": (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
            "valid_until": (now + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        },
        issued_at=now.isoformat().replace("+00:00", "Z"),
    )
    mandate.signature = sign(mandate.model_dump(), priv)
    MandateStore(db).register(mandate)
    quote = QuoteEngine(db).build([QuoteItemRequest(sku="STA-NB-A5")], mandate_id=mandate.mandate_id)
    order = Order(
        order_id=new_id("ord"), quote_id=quote.quote_id, mandate_id=mandate.mandate_id,
        state="FULFILLED", amount_minor=quote.total_minor, currency=quote.currency,
        items_summary="1x notebooks",
        rail="mock_upi", at=quote.expires_at,
    )
    OrderStore(db).put(order)

    # Now make it the file the old image would have left behind.
    with db.immediate_tx() as conn:
        for table, old, new in RENAME_COLUMNS:
            conn.execute(f"ALTER TABLE {table} RENAME COLUMN {new} TO {old}")
        for table in ("mandates", "quotes"):
            for row in conn.execute(f"SELECT rowid, body_json FROM {table}").fetchall():
                old_body = json.dumps(_to_pre_rename(json.loads(row["body_json"])))
                conn.execute(f"UPDATE {table} SET body_json = ? WHERE rowid = ?",
                             (old_body, row["rowid"]))
        assert "max_total_paise" in conn.execute("SELECT body_json FROM mandates").fetchone()[0]

    upgraded = Database(f"sqlite:///{path}")

    for table, old, new in RENAME_COLUMNS:
        columns = {r["name"] for r in upgraded.conn.execute(f"PRAGMA table_info({table})")}
        assert new in columns and old not in columns, f"{table}.{old} was not renamed"

    stored = MandateStore(upgraded).get(mandate.mandate_id)
    assert stored is not None and stored.signature_ok
    assert stored.mandate.constraints.max_total_minor == 1_500_000
    assert stored.mandate.delegator.account == "swetank@okaxis"
    assert stored.mandate.constraints.currency == "INR", "every mandate before today was rupees"
    assert QuoteEngine(upgraded).get(quote.quote_id).total_minor == quote.total_minor
    assert OrderStore(upgraded).get(order.order_id).amount_minor == quote.total_minor
