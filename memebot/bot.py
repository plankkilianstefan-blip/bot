"""Der Callout-Bot: Coins finden → analysieren → Diagramm → Handy-Alert; Wallets tracken."""

from __future__ import annotations

import logging
import time

from .charts import render_analysis_chart
from .config import Settings
from .http import HttpClient
from .notify import Notifier
from .scoring import Analysis, SmartMoney, evaluate
from .sources.bubblemaps import Bubblemaps
from .sources.dexscreener import DexScreener, TokenMarket
from .sources.rugcheck import ContractChecker
from .sources.socials import SocialChecker
from .sources.solana_rpc import SolanaRPC
from .storage import Storage
from .utils import format_usd
from .wallets import Trade, WalletTracker

log = logging.getLogger(__name__)


class CalloutBot:
    def __init__(self, settings: Settings, http: HttpClient | None = None, storage: Storage | None = None):
        self.s = settings
        self.http = http or HttpClient()
        self.storage = storage or Storage(settings.db_path)
        self.rpc = SolanaRPC(self.http, settings.solana_rpc_url)
        self.dex = DexScreener(self.http)
        self.bubblemaps = Bubblemaps(self.http, self.rpc, settings.bubblemaps_api_key)
        self.contracts = ContractChecker(self.http, self.rpc)
        self.socials = SocialChecker(self.http, settings.x_bearer_token)
        self.tracker = WalletTracker(self.rpc, self.storage, settings.wallets_file)
        self.notifier = Notifier(settings, self.http)

    # --- Analyse -----------------------------------------------------------
    def smart_money(self, mint: str) -> SmartMoney:
        buyers = self.storage.buyers_of(mint, hours=24)
        return SmartMoney(
            gross_buyers=sum(1 for b in buyers if b["category"] == "gross"),
            klein_buyers=sum(1 for b in buyers if b["category"] == "klein"),
            labels=[b["label"] for b in buyers],
        )

    def analyze(self, mint: str, market: TokenMarket | None = None) -> Analysis | None:
        if market is None:
            market = self.dex.get_markets([mint]).get(mint)
        if market is None:
            log.warning("Keine Marktdaten für %s", mint)
            return None
        return evaluate(
            market,
            self.bubblemaps.analyze(mint),
            self.contracts.check(mint),
            self.socials.check(market.socials, market.websites),
            self.smart_money(mint),
            min_liquidity=self.s.min_liquidity_usd,
            max_risk=self.s.max_risk,
            min_score=self.s.min_score,
        )

    def candidates(self) -> list[str]:
        mints = dict.fromkeys(row["mint"] for row in self.storage.hot_tokens(hours=6, min_wallets=2))
        mints.update(dict.fromkeys(self.dex.discover_solana_tokens()))
        return [
            m for m in mints
            if not self.storage.recently_alerted(m, self.s.alert_cooldown_hours)
            and not self.storage.recently_analyzed(m, self.s.reanalyze_after_min)
        ]

    def scan_once(self) -> list[Analysis]:
        mints = self.candidates()
        markets = self.dex.get_markets(mints)
        log.info("%d Kandidaten, %d mit Marktdaten", len(mints), len(markets))
        callouts = []
        for mint, market in markets.items():
            self.storage.mark_analyzed(mint)
            if market.liquidity_usd < self.s.min_liquidity_usd:
                continue  # teure Checks (Bubblemaps, Socials) nur für handelbare Coins
            analysis = self.analyze(mint, market)
            if analysis is None:
                continue
            log.info(
                "%-10s Risiko %3.0f  Score %3.0f  %s  %s",
                market.symbol, analysis.risk, analysis.opportunity, analysis.horizon,
                "CALLOUT" if analysis.is_callout else "; ".join(analysis.reasons),
            )
            if analysis.is_callout:
                self.send_callout(analysis)
                callouts.append(analysis)
        return callouts

    # --- Benachrichtigungen ------------------------------------------------
    def callout_text(self, a: Analysis) -> tuple[str, str, str]:
        m = a.market
        title = f"🚀 Memecoin Callout: ${m.symbol} ({m.name})"
        clusters = a.clusters
        cluster_line = (
            f"{clusters.connected_wallets} verbundene Wallets in {clusters.cluster_count} Clustern "
            f"({(clusters.connected_share or 0):.0%} Supply)"
            if clusters.has_cluster_data
            else "keine Clusterdaten (BUBBLEMAPS_API_KEY setzen)"
        )
        lines = [
            f"Risiko: {a.risk:.0f}/100 ({a.risk_level}) · Score {a.opportunity:.0f}/100",
            f"Empfehlung: {a.horizon} halten (lang {a.long_score:.0f} / kurz {a.short_score:.0f})",
            f"MC {format_usd(m.market_cap)} · Liq {format_usd(m.liquidity_usd)} · "
            f"Vol24h {format_usd(m.volume_h24)} · 1h {m.price_change_h1:+.0f}%",
            f"Bubblemaps: {cluster_line}",
            f"Socials: {a.socials.summary()}",
        ]
        if a.smart_money.labels:
            lines.append(f"Getrackte Käufer: {', '.join(a.smart_money.labels[:5])}")
        lines += [f"CA: {m.address}", m.url, a.clusters.map_url]
        sms = (
            f"CALLOUT ${m.symbol} Risiko {a.risk:.0f}/100 Score {a.opportunity:.0f} "
            f"{a.horizon} halten MC {format_usd(m.market_cap)} Liq {format_usd(m.liquidity_usd)} "
            f"CA {m.address} {m.url}"
        )
        return title, "\n".join(lines), sms

    def send_callout(self, a: Analysis) -> list[str]:
        chart = render_analysis_chart(a, self.s.chart_dir)
        title, text, sms = self.callout_text(a)
        delivered = self.notifier.send(title, text, sms_text=sms, image_path=chart, click_url=a.market.url)
        self.storage.record_alert(a.market.address, a.market.symbol, a.risk, a.opportunity, a.horizon)
        return delivered

    # --- Wallet-Tracking ---------------------------------------------------
    def track_wallets_once(self) -> list[Trade]:
        trades = self.tracker.poll()
        if trades:
            log.info("%d neue Trades getrackter Wallets", len(trades))
        if not self.s.wallet_alerts:
            return trades
        fresh = [t for t in trades if time.time() - t.ts < 3600 and t.sol >= self.s.wallet_alert_min_sol]
        for trade in fresh:
            if trade.category != "gross":
                continue
            emoji = "🟢" if trade.side == "BUY" else "🔴"
            action = "kauft" if trade.side == "BUY" else "verkauft"
            self.notifier.send(
                f"{emoji} Whale {trade.label} {action}",
                f"{trade.amount:,.0f} Tokens für {trade.sol:.2f} SOL\nCA: {trade.mint}\n"
                f"https://dexscreener.com/solana/{trade.mint}\nhttps://solscan.io/tx/{trade.signature}",
                sms=False,  # SMS nur für Callouts, Wallet-Alerts per Telegram/ntfy
                click_url=f"https://dexscreener.com/solana/{trade.mint}",
            )
        return trades

    # --- Hauptschleife -----------------------------------------------------
    def run_forever(self) -> None:
        log.info("Bot gestartet. Kanäle: %s", ", ".join(self.notifier.channels) or "nur Konsole")
        next_scan = next_wallets = 0.0
        while True:
            now = time.time()
            try:
                if now >= next_wallets and self.tracker.wallets:
                    self.track_wallets_once()
                    next_wallets = now + self.s.wallet_interval_sec
                if now >= next_scan:
                    self.scan_once()
                    next_scan = now + self.s.scan_interval_sec
            except Exception:  # noqa: BLE001 – der Bot soll bei Einzelfehlern weiterlaufen
                log.exception("Fehler im Durchlauf")
            time.sleep(5)
