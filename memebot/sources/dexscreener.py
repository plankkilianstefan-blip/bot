"""DexScreener: neue Solana-Memecoins finden sowie Markt- und Social-Daten laden."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..http import HttpClient

log = logging.getLogger(__name__)

BASE = "https://api.dexscreener.com"
DISCOVERY_PATHS = ("/token-profiles/latest/v1", "/token-boosts/latest/v1", "/token-boosts/top/v1")


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class TokenMarket:
    address: str
    name: str
    symbol: str
    pair_address: str
    dex_id: str
    url: str
    price_usd: float
    liquidity_usd: float
    market_cap: float
    volume_h24: float
    volume_h1: float
    price_change_m5: float
    price_change_h1: float
    price_change_h6: float
    price_change_h24: float
    buys_h1: int
    sells_h1: int
    buys_h24: int
    sells_h24: int
    created_at: datetime | None
    websites: list[str] = field(default_factory=list)
    socials: list[dict] = field(default_factory=list)

    @property
    def age_hours(self) -> float | None:
        if not self.created_at:
            return None
        return (datetime.now(timezone.utc) - self.created_at).total_seconds() / 3600


def parse_pair(pair: dict) -> TokenMarket:
    base = pair.get("baseToken") or {}
    volume = pair.get("volume") or {}
    change = pair.get("priceChange") or {}
    txns = pair.get("txns") or {}
    info = pair.get("info") or {}
    created_ms = pair.get("pairCreatedAt")
    created = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc) if created_ms else None
    return TokenMarket(
        address=base.get("address", ""),
        name=base.get("name", "?"),
        symbol=base.get("symbol", "?"),
        pair_address=pair.get("pairAddress", ""),
        dex_id=pair.get("dexId", ""),
        url=pair.get("url", ""),
        price_usd=_num(pair.get("priceUsd")),
        liquidity_usd=_num((pair.get("liquidity") or {}).get("usd")),
        market_cap=_num(pair.get("marketCap") or pair.get("fdv")),
        volume_h24=_num(volume.get("h24")),
        volume_h1=_num(volume.get("h1")),
        price_change_m5=_num(change.get("m5")),
        price_change_h1=_num(change.get("h1")),
        price_change_h6=_num(change.get("h6")),
        price_change_h24=_num(change.get("h24")),
        buys_h1=int(_num((txns.get("h1") or {}).get("buys"))),
        sells_h1=int(_num((txns.get("h1") or {}).get("sells"))),
        buys_h24=int(_num((txns.get("h24") or {}).get("buys"))),
        sells_h24=int(_num((txns.get("h24") or {}).get("sells"))),
        created_at=created,
        websites=[w["url"] for w in info.get("websites") or [] if w.get("url")],
        socials=[s for s in info.get("socials") or [] if s.get("url")],
    )


class DexScreener:
    def __init__(self, http: HttpClient):
        self.http = http

    def discover_solana_tokens(self) -> list[str]:
        """Neueste Token-Profile und Boosts (= Projekte, die gerade Marketing machen)."""
        seen: dict[str, None] = {}
        for path in DISCOVERY_PATHS:
            data = self.http.get_json(BASE + path)
            for item in data if isinstance(data, list) else []:
                if item.get("chainId") == "solana" and item.get("tokenAddress"):
                    seen.setdefault(item["tokenAddress"], None)
        return list(seen)

    def get_markets(self, addresses: list[str]) -> dict[str, TokenMarket]:
        """Bester (liquidester) Pool je Token. DexScreener erlaubt 30 Adressen pro Request."""
        wanted = set(addresses)
        best: dict[str, TokenMarket] = {}
        for i in range(0, len(addresses), 30):
            chunk = addresses[i : i + 30]
            data = self.http.get_json(f"{BASE}/tokens/v1/solana/{','.join(chunk)}")
            for pair in data if isinstance(data, list) else []:
                market = parse_pair(pair)
                if market.address not in wanted:
                    continue
                current = best.get(market.address)
                if current is None or market.liquidity_usd > current.liquidity_usd:
                    best[market.address] = market
        return best
