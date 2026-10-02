"""Gemeinsamer HTTP-Client mit Timeouts, Retries und Backoff bei Rate-Limits."""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)


class HttpClient:
    def __init__(self, timeout: float = 15, retries: int = 2, backoff: float = 1.5):
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "memebot/1.0 (+solana callout bot)"

    def _request(self, method: str, url: str, **kwargs) -> requests.Response | None:
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                log.warning("%s %s fehlgeschlagen: %s", method, url, exc)
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    log.warning("%s %s -> HTTP %s", method, url, resp.status_code)
                else:
                    return resp
            if attempt < self.retries:
                time.sleep(self.backoff * (2**attempt))
        return None

    def get_json(self, url: str, params: dict | None = None, headers: dict | None = None) -> Any:
        resp = self._request("GET", url, params=params, headers=headers)
        if resp is None or not resp.ok:
            if resp is not None and resp.status_code != 404:
                log.warning("GET %s -> HTTP %s", url, resp.status_code)
            return None
        try:
            return resp.json()
        except ValueError:
            log.warning("GET %s lieferte kein JSON", url)
            return None

    def post_json(self, url: str, payload: Any, headers: dict | None = None) -> Any:
        resp = self._request("POST", url, json=payload, headers=headers)
        if resp is None or not resp.ok:
            if resp is not None:
                log.warning("POST %s -> HTTP %s", url, resp.status_code)
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    def get_text(self, url: str) -> str | None:
        resp = self._request("GET", url)
        if resp is None or not resp.ok:
            return None
        return resp.text

    def is_reachable(self, url: str) -> bool:
        resp = self._request("GET", url, allow_redirects=True, stream=True)
        if resp is None:
            return False
        resp.close()
        return resp.status_code < 400
