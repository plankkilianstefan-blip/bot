"""Bewertung: Risikofaktoren (0 = sicher … 100 = extrem riskant) und Halte-Empfehlung."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .sources.bubblemaps import ClusterReport
from .sources.dexscreener import TokenMarket
from .sources.rugcheck import ContractReport
from .sources.socials import SocialReport

RISK_WEIGHTS = {
    "Wallet-Cluster": 0.25,
    "Contract": 0.20,
    "Holder-Konzentration": 0.15,
    "Liquidität": 0.15,
    "Alter & Volatilität": 0.15,
    "Socials": 0.10,
}


def clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def scale(value: float, zero_at: float, full_at: float) -> float:
    """Linear 0..100 zwischen zero_at und full_at (beide Richtungen möglich)."""
    if full_at == zero_at:
        return 0.0
    return clamp((value - zero_at) / (full_at - zero_at) * 100)


def risk_level(risk: float) -> str:
    if risk < 35:
        return "niedrig"
    if risk < 55:
        return "mittel"
    if risk < 75:
        return "hoch"
    return "sehr hoch"


@dataclass
class SmartMoney:
    gross_buyers: int = 0
    klein_buyers: int = 0
    labels: list[str] = field(default_factory=list)

    @property
    def score(self) -> float:
        return clamp(self.gross_buyers * 35 + self.klein_buyers * 15)


@dataclass
class RiskFactor:
    name: str
    value: float
    detail: str


@dataclass
class Analysis:
    market: TokenMarket
    clusters: ClusterReport
    contract: ContractReport
    socials: SocialReport
    smart_money: SmartMoney
    risk_factors: list[RiskFactor]
    risk: float
    long_score: float
    short_score: float
    opportunity: float
    is_callout: bool
    reasons: list[str]

    @property
    def horizon(self) -> str:
        return "LANG" if self.long_score >= self.short_score else "KURZ"

    @property
    def risk_level(self) -> str:
        return risk_level(self.risk)


# --- einzelne Risikofaktoren -------------------------------------------------

def cluster_risk(c: ClusterReport) -> RiskFactor:
    if not c.has_cluster_data:
        return RiskFactor("Wallet-Cluster", 60, "keine Bubblemaps-Clusterdaten")
    connected = scale(c.connected_share or 0, 0.0, 0.40)
    biggest = scale(c.largest_cluster_share or 0, 0.0, 0.25)
    value = max(connected, biggest)
    if c.decentralization_score is not None:
        value = 0.6 * value + 0.4 * (100 - clamp(c.decentralization_score))
    detail = (
        f"{c.connected_wallets} verbundene Wallets in {c.cluster_count} Clustern, "
        f"{(c.connected_share or 0):.0%} des Supplies"
    )
    return RiskFactor("Wallet-Cluster", value, detail)


def concentration_risk(c: ClusterReport) -> RiskFactor:
    if c.top10_share is None:
        return RiskFactor("Holder-Konzentration", 60, "Holder unbekannt")
    return RiskFactor(
        "Holder-Konzentration", scale(c.top10_share, 0.15, 0.60), f"Top 10 halten {c.top10_share:.0%}"
    )


def liquidity_risk(m: TokenMarket) -> RiskFactor:
    if m.liquidity_usd <= 0:
        return RiskFactor("Liquidität", 100, "keine Liquidität")
    absolute = scale(math.log10(m.liquidity_usd), 5.3, 3.7)  # 200k$ → 0, 5k$ → 100
    ratio = m.liquidity_usd / m.market_cap if m.market_cap else 0
    ratio_risk = scale(ratio, 0.15, 0.02)
    return RiskFactor(
        "Liquidität", 0.6 * absolute + 0.4 * ratio_risk, f"${m.liquidity_usd:,.0f} ({ratio:.0%} vom MC)"
    )


def contract_risk(c: ContractReport) -> RiskFactor:
    if not c.available:
        return RiskFactor("Contract", 50, "Contract nicht prüfbar")
    value = 0.0
    notes = []
    if c.mint_authority:
        value += 45
        notes.append("Mint-Authority aktiv")
    if c.freeze_authority:
        value += 35
        notes.append("Freeze-Authority aktiv")
    value += 25 * len(c.dangers) + 8 * len(c.warnings)
    if c.lp_locked_pct is not None and c.lp_locked_pct < 50:
        value += 15
        notes.append(f"LP nur {c.lp_locked_pct:.0f}% gelockt")
    if c.rugcheck_score is not None:
        value = max(value, c.rugcheck_score)
        notes.append(f"RugCheck {c.rugcheck_score:.0f}")
    if c.dangers:
        notes.append(f"{len(c.dangers)} Gefahren")
    return RiskFactor("Contract", clamp(value), ", ".join(notes) or "unauffällig")


def social_risk(s: SocialReport) -> RiskFactor:
    return RiskFactor("Socials", 100 - social_strength(s), s.summary())


def social_strength(s: SocialReport) -> float:
    points = 0.0
    if s.twitter_handle:
        points += 30
        if s.twitter_followers is not None:
            points += scale(math.log10(max(s.twitter_followers, 1)), 2, 4.5) * 0.25  # 100 … ~30k
        if s.twitter_age_days is not None and s.twitter_age_days < 3:
            points -= 10  # frisch erstellter Account
    if s.telegram_handle:
        points += 20
        if s.telegram_members is not None:
            points += scale(math.log10(max(s.telegram_members, 1)), 2, 4) * 0.15  # 100 … 10k
    if s.website_ok:
        points += 15
    return clamp(points)


def age_volatility_risk(m: TokenMarket) -> RiskFactor:
    age = m.age_hours
    if age is None:
        value = 50.0
    elif age < 1:
        value = 80.0
    elif age < 6:
        value = 60.0
    elif age < 24:
        value = 45.0
    elif age < 72:
        value = 30.0
    else:
        value = 15.0
    if abs(m.price_change_h1) > 50:
        value += 20
    if m.sells_h1 > 1.5 * max(m.buys_h1, 1):
        value += 15
    age_txt = f"{age:.1f}h alt" if age is not None else "Alter unbekannt"
    return RiskFactor("Alter & Volatilität", clamp(value), f"{age_txt}, 1h {m.price_change_h1:+.0f}%")


# --- Gesamtbewertung ---------------------------------------------------------

def evaluate(
    market: TokenMarket,
    clusters: ClusterReport,
    contract: ContractReport,
    socials: SocialReport,
    smart_money: SmartMoney,
    *,
    min_liquidity: float,
    max_risk: float,
    min_score: float,
) -> Analysis:
    factors = [
        cluster_risk(clusters),
        contract_risk(contract),
        concentration_risk(clusters),
        liquidity_risk(market),
        age_volatility_risk(market),
        social_risk(socials),
    ]
    risk = sum(RISK_WEIGHTS[f.name] * f.value for f in factors)
    by_name = {f.name: f.value for f in factors}

    age = market.age_hours if market.age_hours is not None else 0.0
    maturity = scale(age, 0, 72)
    momentum = scale(market.price_change_h1, 0, 50)
    turnover = scale(market.volume_h24 / market.liquidity_usd if market.liquidity_usd else 0, 0, 5)
    txns = market.buys_h1 + market.sells_h1
    buy_pressure = scale(market.buys_h1 / txns if txns else 0.5, 0.5, 0.75)
    strength = social_strength(socials)

    long_score = clamp(
        0.35 * (100 - risk)
        + 0.15 * strength
        + 0.15 * maturity
        + 0.15 * (100 - by_name["Wallet-Cluster"])
        + 0.10 * (100 - by_name["Liquidität"])
        + 0.10 * clamp(smart_money.gross_buyers * 35)
    )
    short_score = clamp(
        0.30 * momentum
        + 0.25 * turnover
        + 0.20 * (100 - maturity)
        + 0.15 * buy_pressure
        + 0.10 * clamp(smart_money.klein_buyers * 25)
    )
    opportunity = clamp(
        0.45 * (100 - risk)
        + 0.20 * max(long_score, short_score)
        + 0.15 * momentum
        + 0.10 * strength
        + 0.10 * smart_money.score
    )

    reasons = []
    if contract.mint_authority:
        reasons.append("Mint-Authority aktiv (Dev kann neue Tokens drucken)")
    if contract.freeze_authority:
        reasons.append("Freeze-Authority aktiv (Honeypot-Gefahr)")
    if market.liquidity_usd < min_liquidity:
        reasons.append(f"Liquidität unter ${min_liquidity:,.0f}")
    if risk > max_risk:
        reasons.append(f"Risiko {risk:.0f} > {max_risk:.0f}")
    if opportunity < min_score:
        reasons.append(f"Score {opportunity:.0f} < {min_score:.0f}")

    return Analysis(
        market=market,
        clusters=clusters,
        contract=contract,
        socials=socials,
        smart_money=smart_money,
        risk_factors=factors,
        risk=risk,
        long_score=long_score,
        short_score=short_score,
        opportunity=opportunity,
        is_callout=not reasons,
        reasons=reasons,
    )
