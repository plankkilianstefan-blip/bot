"""SQLite-Speicher für Alerts, analysierte Coins und Wallet-Trades."""

from __future__ import annotations

import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    mint TEXT PRIMARY KEY, symbol TEXT, ts REAL, risk REAL, score REAL, horizon TEXT
);
CREATE TABLE IF NOT EXISTS analyzed (mint TEXT PRIMARY KEY, ts REAL);
CREATE TABLE IF NOT EXISTS wallet_cursor (address TEXT PRIMARY KEY, signature TEXT);
CREATE TABLE IF NOT EXISTS trades (
    signature TEXT, wallet TEXT, label TEXT, category TEXT, mint TEXT,
    side TEXT, amount REAL, sol REAL, ts REAL,
    PRIMARY KEY (signature, wallet, mint)
);
CREATE INDEX IF NOT EXISTS trades_mint_ts ON trades (mint, ts);
"""


class Storage:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    # --- Alerts / Analysen -------------------------------------------------
    def recently_alerted(self, mint: str, cooldown_hours: float) -> bool:
        row = self.db.execute("SELECT ts FROM alerts WHERE mint = ?", (mint,)).fetchone()
        return bool(row) and time.time() - row["ts"] < cooldown_hours * 3600

    def record_alert(self, mint: str, symbol: str, risk: float, score: float, horizon: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO alerts VALUES (?, ?, ?, ?, ?, ?)",
            (mint, symbol, time.time(), risk, score, horizon),
        )
        self.db.commit()

    def recently_analyzed(self, mint: str, minutes: float) -> bool:
        row = self.db.execute("SELECT ts FROM analyzed WHERE mint = ?", (mint,)).fetchone()
        return bool(row) and time.time() - row["ts"] < minutes * 60

    def mark_analyzed(self, mint: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO analyzed VALUES (?, ?)", (mint, time.time()))
        self.db.commit()

    # --- Wallet-Tracking ---------------------------------------------------
    def get_cursor(self, address: str) -> str | None:
        row = self.db.execute("SELECT signature FROM wallet_cursor WHERE address = ?", (address,)).fetchone()
        return row["signature"] if row else None

    def set_cursor(self, address: str, signature: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO wallet_cursor VALUES (?, ?)", (address, signature))
        self.db.commit()

    def add_trade(self, trade) -> bool:
        cur = self.db.execute(
            "INSERT OR IGNORE INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trade.signature, trade.wallet, trade.label, trade.category, trade.mint,
                trade.side, trade.amount, trade.sol, trade.ts,
            ),
        )
        self.db.commit()
        return cur.rowcount > 0

    def buyers_of(self, mint: str, hours: float = 24) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT wallet, label, category, SUM(sol) AS sol FROM trades "
            "WHERE mint = ? AND side = 'BUY' AND ts >= ? GROUP BY wallet",
            (mint, time.time() - hours * 3600),
        ).fetchall()

    def hot_tokens(self, hours: float = 6, min_wallets: int = 2) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT mint, COUNT(DISTINCT wallet) AS wallets, "
            "SUM(category = 'gross') AS gross, SUM(category = 'klein') AS klein "
            "FROM trades WHERE side = 'BUY' AND ts >= ? GROUP BY mint "
            "HAVING COUNT(DISTINCT wallet) >= ? ORDER BY wallets DESC",
            (time.time() - hours * 3600, min_wallets),
        ).fetchall()

    def recent_trades(self, hours: float = 24, limit: int = 50) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM trades WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
            (time.time() - hours * 3600, limit),
        ).fetchall()
