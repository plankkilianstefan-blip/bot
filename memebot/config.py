"""Konfiguration – alles kommt aus Umgebungsvariablen bzw. der .env Datei."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


def _str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value.strip() if value and value.strip() else None


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "ja", "on"}


@dataclass
class Settings:
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    bubblemaps_api_key: str | None = None
    x_bearer_token: str | None = None

    # Benachrichtigungen
    alert_phone_number: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    twilio_from_number: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"

    # Bot-Verhalten
    scan_interval_sec: float = 60
    wallet_interval_sec: float = 120
    min_liquidity_usd: float = 10_000
    min_score: float = 65
    max_risk: float = 50
    alert_cooldown_hours: float = 12
    reanalyze_after_min: float = 30
    wallet_alerts: bool = True
    wallet_alert_min_sol: float = 1.0
    whale_usd_threshold: float = 10_000

    # Dateien
    wallets_file: str = "wallets.json"
    db_path: str = "data/memebot.db"
    chart_dir: str = "charts"

    @classmethod
    def from_env(cls, env_file: str | None = None) -> "Settings":
        load_dotenv(env_file)
        return cls(
            solana_rpc_url=_str("SOLANA_RPC_URL", cls.solana_rpc_url),
            bubblemaps_api_key=_str("BUBBLEMAPS_API_KEY"),
            x_bearer_token=_str("X_BEARER_TOKEN"),
            alert_phone_number=_str("ALERT_PHONE_NUMBER"),
            twilio_account_sid=_str("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_str("TWILIO_AUTH_TOKEN"),
            twilio_from_number=_str("TWILIO_FROM_NUMBER"),
            telegram_bot_token=_str("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_str("TELEGRAM_CHAT_ID"),
            ntfy_topic=_str("NTFY_TOPIC"),
            ntfy_server=_str("NTFY_SERVER", cls.ntfy_server),
            scan_interval_sec=_float("SCAN_INTERVAL_SEC", cls.scan_interval_sec),
            wallet_interval_sec=_float("WALLET_INTERVAL_SEC", cls.wallet_interval_sec),
            min_liquidity_usd=_float("MIN_LIQUIDITY_USD", cls.min_liquidity_usd),
            min_score=_float("MIN_SCORE", cls.min_score),
            max_risk=_float("MAX_RISK", cls.max_risk),
            alert_cooldown_hours=_float("ALERT_COOLDOWN_HOURS", cls.alert_cooldown_hours),
            reanalyze_after_min=_float("REANALYZE_AFTER_MIN", cls.reanalyze_after_min),
            wallet_alerts=_bool("WALLET_ALERTS", cls.wallet_alerts),
            wallet_alert_min_sol=_float("WALLET_ALERT_MIN_SOL", cls.wallet_alert_min_sol),
            whale_usd_threshold=_float("WHALE_USD_THRESHOLD", cls.whale_usd_threshold),
            wallets_file=_str("WALLETS_FILE", cls.wallets_file),
            db_path=_str("DB_PATH", cls.db_path),
            chart_dir=_str("CHART_DIR", cls.chart_dir),
        )
