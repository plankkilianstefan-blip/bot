"""Handy-Benachrichtigungen: SMS (Twilio), Telegram (mit Diagramm) und ntfy Push."""

from __future__ import annotations

import base64
import logging
import os

from .config import Settings
from .http import HttpClient

log = logging.getLogger(__name__)


def _header(value: str) -> str:
    """HTTP-Header müssen ASCII sein – Umlaute/Emojis per RFC 2047 kodieren (ntfy versteht das)."""
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode("utf-8")).decode() + "?="


class Notifier:
    def __init__(self, settings: Settings, http: HttpClient):
        self.s = settings
        self.session = http.session

    @property
    def channels(self) -> list[str]:
        channels = []
        if self.s.twilio_account_sid and self.s.twilio_auth_token and self.s.twilio_from_number and self.s.alert_phone_number:
            channels.append("sms")
        if self.s.telegram_bot_token and self.s.telegram_chat_id:
            channels.append("telegram")
        if self.s.ntfy_topic:
            channels.append("ntfy")
        return channels

    def send(self, title: str, text: str, *, sms_text: str | None = None, image_path: str | None = None,
             click_url: str | None = None, sms: bool = True) -> list[str]:
        """Schickt an alle konfigurierten Kanäle; gibt die erfolgreichen zurück."""
        log.info("ALERT: %s\n%s", title, text)
        delivered = []
        if sms and "sms" in self.channels and self._sms(sms_text or f"{title}\n{text}"):
            delivered.append("sms")
        if "telegram" in self.channels and self._telegram(f"{title}\n\n{text}", image_path):
            delivered.append("telegram")
        if "ntfy" in self.channels and self._ntfy(title, text, image_path, click_url):
            delivered.append("ntfy")
        if not self.channels:
            log.warning("Kein Benachrichtigungskanal konfiguriert – siehe .env.example")
        return delivered

    def _sms(self, body: str) -> bool:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.s.twilio_account_sid}/Messages.json"
        try:
            resp = self.session.post(
                url,
                data={"To": self.s.alert_phone_number, "From": self.s.twilio_from_number, "Body": body[:1500]},
                auth=(self.s.twilio_account_sid, self.s.twilio_auth_token),
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001 – Benachrichtigung darf den Bot nie stoppen
            log.error("SMS fehlgeschlagen: %s", exc)
            return False
        if not resp.ok:
            log.error("SMS fehlgeschlagen: HTTP %s %s", resp.status_code, resp.text[:200])
        return resp.ok

    def _telegram(self, text: str, image_path: str | None) -> bool:
        base = f"https://api.telegram.org/bot{self.s.telegram_bot_token}"
        try:
            if image_path and os.path.exists(image_path):
                with open(image_path, "rb") as fh:
                    resp = self.session.post(
                        f"{base}/sendPhoto",
                        data={"chat_id": self.s.telegram_chat_id, "caption": text[:1024]},
                        files={"photo": fh},
                        timeout=30,
                    )
            else:
                resp = self.session.post(
                    f"{base}/sendMessage",
                    data={"chat_id": self.s.telegram_chat_id, "text": text[:4096], "disable_web_page_preview": True},
                    timeout=20,
                )
        except Exception as exc:  # noqa: BLE001
            log.error("Telegram fehlgeschlagen: %s", exc)
            return False
        if not resp.ok:
            log.error("Telegram fehlgeschlagen: HTTP %s %s", resp.status_code, resp.text[:200])
        return resp.ok

    def _ntfy(self, title: str, text: str, image_path: str | None, click_url: str | None) -> bool:
        url = f"{self.s.ntfy_server.rstrip('/')}/{self.s.ntfy_topic}"
        headers = {"Title": _header(title), "Tags": "rocket", "Priority": "high"}
        if click_url:
            headers["Click"] = click_url
        try:
            if image_path and os.path.exists(image_path):
                headers["Message"] = _header(text.replace("\n", "\\n"))
                headers["Filename"] = os.path.basename(image_path)
                with open(image_path, "rb") as fh:
                    resp = self.session.put(url, data=fh, headers=headers, timeout=30)
            else:
                resp = self.session.post(url, data=text.encode("utf-8"), headers=headers, timeout=20)
        except Exception as exc:  # noqa: BLE001
            log.error("ntfy fehlgeschlagen: %s", exc)
            return False
        if not resp.ok:
            log.error("ntfy fehlgeschlagen: HTTP %s %s", resp.status_code, resp.text[:200])
        return resp.ok
