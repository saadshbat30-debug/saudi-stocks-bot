"""Minimal Telegram Bot API client (no extra dependencies)."""
import logging

import requests

from . import config

log = logging.getLogger(__name__)
_session = requests.Session()


def _call(method, timeout=15, **payload):
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/{method}"
    resp = _session.post(url, json=payload, timeout=timeout)
    data = resp.json()
    if not data.get("ok"):
        log.warning("Telegram %s failed: %s", method, data.get("description"))
    return data


def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _call("sendMessage", **payload)


def answer_callback(callback_id):
    return _call("answerCallbackQuery", callback_query_id=callback_id)


def set_webhook(url, secret):
    return _call("setWebhook", url=url, secret_token=secret, allowed_updates=["message", "callback_query"])


def delete_webhook():
    return _call("deleteWebhook")


def get_updates(offset=None, timeout=50):
    payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
    if offset is not None:
        payload["offset"] = offset
    return _call("getUpdates", timeout=timeout + 10, **payload)


def set_commands(commands):
    return _call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])
