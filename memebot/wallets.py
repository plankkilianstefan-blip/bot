"""Wallet-Tracker für große ("gross") und kleine ("klein") Memecoin-Trader.

Neue Transaktionen jeder Wallet werden per RPC geladen; Käufe/Verkäufe ergeben
sich aus den Token-Salden vor und nach der Transaktion (funktioniert für
Jupiter, Raydium, Pump.fun, Meteora … ohne DEX-spezifisches Parsing).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass

from .sources.bubblemaps import HolderInfo
from .sources.solana_rpc import SolanaRPC
from .storage import Storage

log = logging.getLogger(__name__)

CATEGORIES = ("gross", "klein")
BASE58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
# SOL, USDC, USDT – Gegenwährungen, keine Memecoins
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
}


@dataclass
class TrackedWallet:
    address: str
    label: str
    category: str  # "gross" oder "klein"


@dataclass
class Trade:
    signature: str
    wallet: str
    label: str
    category: str
    mint: str
    side: str  # "BUY" oder "SELL"
    amount: float
    sol: float  # bewegte SOL (positiv = ausgegeben bei Kauf / erhalten bei Verkauf)
    ts: float


def is_valid_address(address: str) -> bool:
    return bool(BASE58.match(address or ""))


def load_wallets(path: str) -> list[TrackedWallet]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    wallets = []
    for entry in raw:
        address = entry.get("address", "")
        category = entry.get("category", "klein")
        if not is_valid_address(address):
            log.warning("Ungültige Wallet-Adresse übersprungen: %r", address)
            continue
        if category not in CATEGORIES:
            category = "klein"
        wallets.append(TrackedWallet(address, entry.get("label") or address[:6], category))
    return wallets


def save_wallets(path: str, wallets: list[TrackedWallet]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([asdict(w) for w in wallets], fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def _owner_balances(entries: list[dict], owner: str) -> dict[str, float]:
    totals: dict[str, float] = {}
    for entry in entries or []:
        if entry.get("owner") != owner:
            continue
        amount = (entry.get("uiTokenAmount") or {}).get("uiAmount") or 0.0
        totals[entry["mint"]] = totals.get(entry["mint"], 0.0) + float(amount)
    return totals


def _sol_change(tx: dict, wallet: str) -> float:
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    meta = tx.get("meta") or {}
    for i, key in enumerate(keys):
        pubkey = key.get("pubkey") if isinstance(key, dict) else key
        if pubkey == wallet:
            try:
                return (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
            except (KeyError, IndexError, TypeError):
                return 0.0
    return 0.0


def extract_trades(tx: dict, wallet: TrackedWallet, signature: str) -> list[Trade]:
    meta = (tx or {}).get("meta")
    if not meta or meta.get("err"):
        return []
    pre = _owner_balances(meta.get("preTokenBalances"), wallet.address)
    post = _owner_balances(meta.get("postTokenBalances"), wallet.address)
    sol = abs(_sol_change(tx, wallet.address))
    ts = float(tx.get("blockTime") or time.time())
    trades = []
    for mint in set(pre) | set(post):
        if mint in QUOTE_MINTS:
            continue
        delta = post.get(mint, 0.0) - pre.get(mint, 0.0)
        if abs(delta) < 1e-9:
            continue
        trades.append(
            Trade(
                signature=signature,
                wallet=wallet.address,
                label=wallet.label,
                category=wallet.category,
                mint=mint,
                side="BUY" if delta > 0 else "SELL",
                amount=abs(delta),
                sol=sol,
                ts=ts,
            )
        )
    return trades


class WalletTracker:
    def __init__(self, rpc: SolanaRPC, storage: Storage, wallets_file: str):
        self.rpc = rpc
        self.storage = storage
        self.wallets_file = wallets_file

    @property
    def wallets(self) -> list[TrackedWallet]:
        return load_wallets(self.wallets_file)

    def poll(self, max_tx_per_wallet: int = 15) -> list[Trade]:
        """Neue Trades aller getrackten Wallets laden und speichern."""
        new_trades: list[Trade] = []
        for wallet in self.wallets:
            cursor = self.storage.get_cursor(wallet.address)
            signatures = self.rpc.get_signatures(wallet.address, limit=max_tx_per_wallet, until=cursor)
            if not signatures:
                continue
            for sig in reversed(signatures):  # älteste zuerst
                if sig.get("err"):
                    continue
                tx = self.rpc.get_transaction(sig["signature"])
                for trade in extract_trades(tx, wallet, sig["signature"]):
                    if self.storage.add_trade(trade):
                        new_trades.append(trade)
            self.storage.set_cursor(wallet.address, signatures[0]["signature"])
        return new_trades

    def add(self, address: str, label: str, category: str) -> TrackedWallet:
        if not is_valid_address(address):
            raise ValueError(f"Ungültige Solana-Adresse: {address}")
        if category not in CATEGORIES:
            raise ValueError(f"Kategorie muss eine von {CATEGORIES} sein")
        wallets = [w for w in self.wallets if w.address != address]
        wallet = TrackedWallet(address, label or address[:6], category)
        wallets.append(wallet)
        save_wallets(self.wallets_file, wallets)
        return wallet

    def discover_from_holders(
        self, holders: list[HolderInfo], market_cap: float, symbol: str, whale_usd: float, limit: int = 20
    ) -> list[TrackedWallet]:
        """Top-Holder eines Coins als Trader übernehmen: Wert >= whale_usd → "gross", sonst "klein"."""
        known = {w.address for w in self.wallets}
        added = []
        for holder in holders[:limit]:
            if holder.is_excluded or holder.address in known or not is_valid_address(holder.address):
                continue
            value = holder.share * market_cap
            category = "gross" if value >= whale_usd else "klein"
            added.append(self.add(holder.address, f"{symbol}-holder-{holder.address[:4]}", category))
        return added
