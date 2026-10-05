"""Per-chat watchlists and price alerts stored in a JSON file."""
import json
import os
import threading
import uuid

from . import config

_lock = threading.Lock()
_path = os.path.join(config.DATA_DIR, "store.json")


def _load():
    try:
        with open(_path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"chats": {}}


def _save(data):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    tmp = _path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path)


def _chat(data, chat_id):
    return data["chats"].setdefault(str(chat_id), {"watchlist": [], "alerts": []})


def register_chat(chat_id):
    with _lock:
        data = _load()
        if str(chat_id) not in data["chats"]:
            _chat(data, chat_id)
            _save(data)


def all_chat_ids():
    with _lock:
        return list(_load()["chats"].keys())


def get_watchlist(chat_id):
    with _lock:
        return list(_chat(_load(), chat_id)["watchlist"])


def add_to_watchlist(chat_id, symbols):
    with _lock:
        data = _load()
        wl = _chat(data, chat_id)["watchlist"]
        for s in symbols:
            if s not in wl:
                wl.append(s)
        del wl[50:]
        _save(data)
        return list(wl)


def remove_from_watchlist(chat_id, symbols):
    with _lock:
        data = _load()
        chat = _chat(data, chat_id)
        chat["watchlist"] = [s for s in chat["watchlist"] if s not in symbols]
        _save(data)
        return list(chat["watchlist"])


def add_alert(chat_id, symbol, op, target):
    with _lock:
        data = _load()
        alert = {"id": uuid.uuid4().hex[:6], "symbol": symbol, "op": op, "target": target}
        _chat(data, chat_id)["alerts"].append(alert)
        _save(data)
        return alert


def get_alerts(chat_id):
    with _lock:
        return list(_chat(_load(), chat_id)["alerts"])


def remove_alert(chat_id, alert_id):
    with _lock:
        data = _load()
        chat = _chat(data, chat_id)
        before = len(chat["alerts"])
        chat["alerts"] = [a for a in chat["alerts"] if a["id"] != alert_id]
        _save(data)
        return len(chat["alerts"]) < before


def all_alerts():
    """Return [(chat_id, alert), ...] for every chat."""
    with _lock:
        data = _load()
        return [(cid, a) for cid, chat in data["chats"].items() for a in chat["alerts"]]
