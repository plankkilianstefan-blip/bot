"""Bubblemaps V2: wie viele der Top-Holder-Wallets sind miteinander verbunden?

Nutzt die Bubblemaps Data API (``api.bubblemaps.io``, API-Key nötig). Ohne Key
fällt der Bot auf eine reine Holder-Konzentration per Solana-RPC zurück –
verbundene Wallets lassen sich dann nicht ermitteln.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..http import HttpClient
from .solana_rpc import SYSTEM_PROGRAM, SolanaRPC

log = logging.getLogger(__name__)

API = "https://api.bubblemaps.io"
MAP_URL = "https://v2.bubblemaps.io/map?address={address}&chain=solana"
EXCLUDED_FLAGS = ("is_cex", "is_dex", "is_contract")


def _share(value) -> float:
    """Bubblemaps liefert Anteile als 0..1 – zur Sicherheit auch Prozentwerte abfangen."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return v / 100 if v > 1 else v


@dataclass
class HolderInfo:
    address: str
    share: float
    is_excluded: bool = False


@dataclass
class ClusterReport:
    source: str  # "bubblemaps", "rpc" oder "none"
    map_url: str
    decentralization_score: float | None = None
    connected_wallets: int | None = None
    cluster_count: int | None = None
    largest_cluster_share: float | None = None
    connected_share: float | None = None
    top10_share: float | None = None
    holders: list[HolderInfo] = field(default_factory=list)

    @property
    def has_cluster_data(self) -> bool:
        return self.source == "bubblemaps" and self.connected_wallets is not None


def _components(edges: list[tuple[str, str]]) -> list[set[str]]:
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[str, set[str]] = {}
    for node in parent:
        groups.setdefault(find(node), set()).add(node)
    return [g for g in groups.values() if len(g) > 1]


def parse_map(mint: str, data: dict) -> ClusterReport:
    nodes = data.get("nodes") or {}
    raw_holders = nodes.get("top_holders") if isinstance(nodes, dict) else nodes
    holders: list[HolderInfo] = []
    for node in raw_holders or []:
        details = node.get("address_details") or {}
        holder_data = node.get("holder_data") or {}
        holders.append(
            HolderInfo(
                address=node.get("address", ""),
                share=_share(holder_data.get("share", node.get("share"))),
                is_excluded=any(details.get(flag) for flag in EXCLUDED_FLAGS),
            )
        )
    real = sorted((h for h in holders if not h.is_excluded), key=lambda h: h.share, reverse=True)
    share_of = {h.address: h.share for h in real}
    excluded = {h.address for h in holders if h.is_excluded}

    clusters = []
    for cluster in data.get("clusters") or []:
        members = [a for a in cluster.get("holders") or [] if a not in excluded]
        count = int(cluster.get("holder_count") or len(members))
        if count < 2:
            continue
        share = _share(cluster.get("share")) if cluster.get("share") is not None else sum(
            share_of.get(a, 0.0) for a in members
        )
        clusters.append((count, share))

    if not clusters and data.get("relationships"):
        edges = []
        for rel in data["relationships"]:
            a = rel.get("from_address") or rel.get("source")
            b = rel.get("to_address") or rel.get("target")
            if a and b and a not in excluded and b not in excluded:
                edges.append((a, b))
        clusters = [(len(c), sum(share_of.get(a, 0.0) for a in c)) for c in _components(edges)]

    score = data.get("decentralization_score")
    return ClusterReport(
        source="bubblemaps",
        map_url=MAP_URL.format(address=mint),
        decentralization_score=float(score) if score is not None else None,
        connected_wallets=sum(c for c, _ in clusters),
        cluster_count=len(clusters),
        largest_cluster_share=max((s for _, s in clusters), default=0.0),
        connected_share=sum(s for _, s in clusters),
        top10_share=sum(h.share for h in real[:10]),
        holders=real,
    )


class Bubblemaps:
    def __init__(self, http: HttpClient, rpc: SolanaRPC, api_key: str | None):
        self.http = http
        self.rpc = rpc
        self.api_key = api_key

    def analyze(self, mint: str) -> ClusterReport:
        if self.api_key:
            data = self.http.get_json(
                f"{API}/maps/solana/{mint}",
                params={
                    "return_nodes": "true",
                    "return_relationships": "true",
                    "return_clusters": "true",
                    "return_decentralization_score": "true",
                },
                headers={"X-ApiKey": self.api_key},
            )
            if isinstance(data, dict):
                return parse_map(mint, data)
            log.info("Keine Bubblemaps-Daten für %s – nutze RPC-Fallback", mint)
        return self._rpc_fallback(mint)

    def _rpc_fallback(self, mint: str) -> ClusterReport:
        report = ClusterReport(source="none", map_url=MAP_URL.format(address=mint))
        supply = self.rpc.get_token_supply(mint)
        accounts = self.rpc.get_token_largest_accounts(mint)
        if not supply or not accounts:
            return report
        # Token-Accounts → Besitzer auflösen und Pools/Programme (nicht System-Program) aussortieren
        infos = self.rpc.get_multiple_accounts([a["address"] for a in accounts])
        owners = []
        for acc, info in zip(accounts, infos):
            try:
                owners.append(info["data"]["parsed"]["info"]["owner"])
            except (TypeError, KeyError):
                owners.append(None)
        owner_infos = self.rpc.get_multiple_accounts([o for o in owners if o])
        owner_program = {o: (i or {}).get("owner") for o, i in zip([o for o in owners if o], owner_infos)}
        holders = []
        for acc, owner in zip(accounts, owners):
            amount = float(acc.get("uiAmount") or 0)
            is_wallet = owner is not None and owner_program.get(owner) == SYSTEM_PROGRAM
            holders.append(HolderInfo(address=owner or acc["address"], share=amount / supply, is_excluded=not is_wallet))
        real = [h for h in holders if not h.is_excluded]
        report.source = "rpc"
        report.top10_share = sum(h.share for h in real[:10])
        report.holders = real
        return report
