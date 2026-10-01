"""Optional Telegram alerts. Silently does nothing without credentials."""

from __future__ import annotations

import json
import logging
import os
import urllib.request

log = logging.getLogger(__name__)


def send(text: str, env: dict | None = None, opener=urllib.request.urlopen) -> bool:
    env = os.environ if env is None else env
    token, chat_id = env.get("TELEGRAM_BOT_TOKEN", "").strip(), env.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return False
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=json.dumps({"chat_id": chat_id, "text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener(req, timeout=20) as resp:
            resp.read()
        return True
    except Exception as exc:  # noqa: BLE001 - an alert failure must not fail the run
        # Don't log the exception text: it can contain the URL with the token.
        log.warning("Telegram alert could not be sent (%s)", type(exc).__name__)
        return False
