"""Contract-Checks: Mint-/Freeze-Authority per RPC plus RugCheck.xyz Report."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..http import HttpClient
from .solana_rpc import SolanaRPC

log = logging.getLogger(__name__)

RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"


@dataclass
class ContractReport:
    mint_authority: bool | None = None
    freeze_authority: bool | None = None
    rugcheck_score: float | None = None  # 0..100, höher = riskanter
    lp_locked_pct: float | None = None
    dangers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.mint_authority is not None or self.rugcheck_score is not None


def parse_rugcheck(data: dict, report: ContractReport) -> ContractReport:
    for risk in data.get("risks") or []:
        name = risk.get("name", "")
        level = (risk.get("level") or "").lower()
        lowered = name.lower()
        if "mint authority" in lowered and report.mint_authority is None:
            report.mint_authority = True
        if "freeze authority" in lowered and report.freeze_authority is None:
            report.freeze_authority = True
        if level == "danger":
            report.dangers.append(name)
        elif level == "warn":
            report.warnings.append(name)
    score = data.get("score_normalised")
    if score is not None:
        report.rugcheck_score = max(0.0, min(100.0, float(score)))
    if data.get("lpLockedPct") is not None:
        report.lp_locked_pct = float(data["lpLockedPct"])
    return report


class ContractChecker:
    def __init__(self, http: HttpClient, rpc: SolanaRPC):
        self.http = http
        self.rpc = rpc

    def check(self, mint: str) -> ContractReport:
        report = ContractReport()
        info = self.rpc.get_mint_info(mint)
        if info is not None:
            report.mint_authority = bool(info.get("mintAuthority"))
            report.freeze_authority = bool(info.get("freezeAuthority"))
        data = self.http.get_json(RUGCHECK.format(mint=mint))
        if isinstance(data, dict):
            parse_rugcheck(data, report)
        return report
