"""Replay the breakout strategy over past daily closes.

Saudi stocks can't be sold short, so this is long-only: buy on a BUY
breakout, sell at take-profit, stop-loss, a SELL breakout, or after
max_hold_days, whichever comes first. Prices are daily closes.
"""
from dataclasses import dataclass

from strategy import Params, find_signals


@dataclass
class Rules:
    take_profit_pct: float = 6.0
    stop_loss_pct: float = 3.0
    max_hold_days: int = 20


def run(close, params=Params(), rules=Rules()):
    signals = {d: side for d, side, _, _ in find_signals(close, params)}
    trades = []
    entry = None  # (index, date, price)

    for i, (day, price) in enumerate(close.items()):
        side = signals.get(day)
        if entry is None:
            if side == "BUY":
                entry = (i, day, price)
            continue

        change = (price / entry[2] - 1) * 100
        if change >= rules.take_profit_pct:
            reason = "هدف الربح"
        elif change <= -rules.stop_loss_pct:
            reason = "وقف الخسارة"
        elif side == "SELL":
            reason = "كسر هابط"
        elif i - entry[0] >= rules.max_hold_days:
            reason = "انتهاء المدة"
        else:
            continue
        trades.append(_trade(entry, day, price, reason))
        entry = None

    if entry is not None:
        trades.append(_trade(entry, close.index[-1], close.iloc[-1], "مفتوحة"))

    return {"trades": trades, **summarize(trades, close)}


def _trade(entry, exit_day, exit_price, reason):
    return {
        "entry_date": entry[1],
        "entry_price": float(entry[2]),
        "exit_date": exit_day,
        "exit_price": float(exit_price),
        "return_pct": float((exit_price / entry[2] - 1) * 100),
        "reason": reason,
    }


def summarize(trades, close):
    closed = [t for t in trades if t["reason"] != "مفتوحة"]
    wins = [t for t in closed if t["return_pct"] > 0]
    total = 1.0
    for t in trades:
        total *= 1 + t["return_pct"] / 100
    return {
        "count": len(closed),
        "wins": len(wins),
        "win_rate": len(wins) / len(closed) * 100 if closed else None,
        "avg_return": sum(t["return_pct"] for t in closed) / len(closed) if closed else None,
        "total_return": (total - 1) * 100,
        "buy_hold": float((close.iloc[-1] / close.iloc[0] - 1) * 100) if len(close) else None,
    }
