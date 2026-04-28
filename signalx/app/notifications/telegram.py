"""Telegram notifier — best-effort, never raises into the caller.

Uses the public Bot API directly via httpx so we don't need to manage an event
loop just to post a message.
"""
from __future__ import annotations

import logging

import httpx

from app.config.settings import get_settings

log = logging.getLogger(__name__)


def send(message: str) -> bool:
    """Return True on success, False otherwise. Never raises."""
    s = get_settings()
    if not s.telegram_enabled:
        log.info("telegram disabled; skipping send")
        return False
    if not s.telegram_bot_token or not s.telegram_chat_id:
        log.warning("telegram enabled but token/chat_id missing")
        return False

    url = f"https://api.telegram.org/bot{s.telegram_bot_token}/sendMessage"
    # Plain text on purpose: signal bodies contain free-form news copy that
    # may include `<`, `>`, `&`, and other characters that would break HTML or
    # MarkdownV2 parsing. Telegram renders plain text fine.
    payload = {
        "chat_id": s.telegram_chat_id,
        "text": message,
        "disable_web_page_preview": True,
    }
    try:
        r = httpx.post(url, json=payload, timeout=5.0)
        if r.status_code != 200:
            log.warning("telegram send failed status=%s body=%s", r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:  # pragma: no cover - network
        log.warning("telegram send error: %s", exc)
        return False
