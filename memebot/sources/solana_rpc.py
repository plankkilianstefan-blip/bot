"""Minimaler Solana JSON-RPC Client (funktioniert mit jedem RPC, z.B. Helius)."""

from __future__ import annotations

import itertools
import logging
from typing import Any

from ..http import HttpClient

log = logging.getLogger(__name__)

SYSTEM_PROGRAM = "11111111111111111111111111111111"


class SolanaRPC:
    def __init__(self, http: HttpClient, url: str):
        self.http = http
        self.url = url
        self._ids = itertools.count(1)

    def call(self, method: str, params: list | None = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params or []}
        data = self.http.post_json(self.url, payload)
        if not isinstance(data, dict):
            return None
        if "error" in data:
            log.warning("RPC %s Fehler: %s", method, data["error"])
            return None
        return data.get("result")

    def get_token_largest_accounts(self, mint: str) -> list[dict]:
        result = self.call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        return (result or {}).get("value") or []

    def get_token_supply(self, mint: str) -> float | None:
        result = self.call("getTokenSupply", [mint, {"commitment": "confirmed"}])
        value = (result or {}).get("value") or {}
        amount = value.get("uiAmount")
        return float(amount) if amount is not None else None

    def get_mint_info(self, mint: str) -> dict | None:
        """Liefert u.a. mintAuthority und freezeAuthority eines SPL-Tokens."""
        result = self.call("getAccountInfo", [mint, {"encoding": "jsonParsed"}])
        try:
            return result["value"]["data"]["parsed"]["info"]
        except (TypeError, KeyError):
            return None

    def get_multiple_accounts(self, addresses: list[str]) -> list[dict | None]:
        accounts: list[dict | None] = []
        for i in range(0, len(addresses), 100):
            chunk = addresses[i : i + 100]
            result = self.call("getMultipleAccounts", [chunk, {"encoding": "jsonParsed"}])
            values = (result or {}).get("value")
            accounts.extend(values if isinstance(values, list) else [None] * len(chunk))
        return accounts

    def get_signatures(self, address: str, limit: int = 20, until: str | None = None) -> list[dict]:
        opts: dict[str, Any] = {"limit": limit, "commitment": "confirmed"}
        if until:
            opts["until"] = until
        return self.call("getSignaturesForAddress", [address, opts]) or []

    def get_transaction(self, signature: str) -> dict | None:
        return self.call(
            "getTransaction",
            [
                signature,
                {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0, "commitment": "confirmed"},
            ],
        )
