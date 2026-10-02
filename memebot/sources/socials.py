"""Social-Media-Check: X/Twitter, Telegram und Website eines Coins prüfen."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from ..http import HttpClient

log = logging.getLogger(__name__)

TG_MEMBERS = re.compile(r"([\d][\d\s.,']*)\s*(members|subscribers|Mitglieder|Abonnenten)", re.I)


@dataclass
class SocialReport:
    twitter_handle: str | None = None
    twitter_followers: int | None = None
    twitter_age_days: float | None = None
    telegram_handle: str | None = None
    telegram_members: int | None = None
    website: str | None = None
    website_ok: bool = False

    @property
    def channel_count(self) -> int:
        return sum([bool(self.twitter_handle), bool(self.telegram_handle), self.website_ok])

    def summary(self) -> str:
        parts = []
        if self.twitter_handle:
            followers = f" ({self.twitter_followers:,} Follower)" if self.twitter_followers is not None else ""
            parts.append(f"X @{self.twitter_handle}{followers}")
        if self.telegram_handle:
            members = f" ({self.telegram_members:,} Mitglieder)" if self.telegram_members is not None else ""
            parts.append(f"TG {self.telegram_handle}{members}")
        if self.website:
            parts.append(f"Web {'ok' if self.website_ok else 'offline'}")
        return ", ".join(parts) or "keine Socials"


def _handle(url: str) -> str | None:
    path = urlparse(url if "://" in url else f"https://{url}").path.strip("/")
    first = path.split("/")[0] if path else ""
    if not first or first.lower() in {"i", "intent", "share", "home", "search", "joinchat"}:
        return None
    return first.lstrip("@")


def parse_telegram_members(html: str) -> int | None:
    match = TG_MEMBERS.search(html or "")
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


class SocialChecker:
    def __init__(self, http: HttpClient, x_bearer_token: str | None = None):
        self.http = http
        self.x_bearer_token = x_bearer_token

    def check(self, socials: list[dict], websites: list[str]) -> SocialReport:
        report = SocialReport()
        for social in socials:
            kind = (social.get("type") or "").lower()
            url = social.get("url") or ""
            host = urlparse(url).netloc.lower()
            if (kind in {"twitter", "x"} or host.endswith(("twitter.com", "x.com"))) and not report.twitter_handle:
                report.twitter_handle = _handle(url)
            elif (kind == "telegram" or host.endswith(("t.me", "telegram.me"))) and not report.telegram_handle:
                report.telegram_handle = _handle(url)
        if websites:
            report.website = websites[0]
            report.website_ok = self.http.is_reachable(websites[0])
        if report.telegram_handle:
            html = self.http.get_text(f"https://t.me/{report.telegram_handle}")
            report.telegram_members = parse_telegram_members(html) if html else None
        if report.twitter_handle and self.x_bearer_token:
            self._load_twitter(report)
        return report

    def _load_twitter(self, report: SocialReport) -> None:
        data = self.http.get_json(
            f"https://api.x.com/2/users/by/username/{report.twitter_handle}",
            params={"user.fields": "public_metrics,created_at"},
            headers={"Authorization": f"Bearer {self.x_bearer_token}"},
        )
        user = (data or {}).get("data") if isinstance(data, dict) else None
        if not user:
            return
        report.twitter_followers = (user.get("public_metrics") or {}).get("followers_count")
        if user.get("created_at"):
            created = datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
            report.twitter_age_days = (datetime.now(timezone.utc) - created).total_seconds() / 86400
