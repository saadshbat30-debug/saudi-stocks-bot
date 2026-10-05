"""Equal-weight portfolio kept in a local JSON file."""
import json
import math
import os
from datetime import date

PORTFOLIO_FILE = os.environ.get("PORTFOLIO_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio.json"))

# 24 large, liquid companies across 11 sectors (the universe used in the research)
SUGGESTED = [
    "1120", "1180", "1010", "1150", "1060",
    "2222", "2010", "2020", "1211", "2350", "2290", "2310", "3030",
    "7010", "7020", "7030",
    "4190", "4001", "2280", "2050", "4280", "4013",
    "8010", "2082",
]
REBALANCE_MONTH = 4  # once a year, after annual results are out
DRIFT_LIMIT = 1 / 3  # flag a holding whose weight is a third above/below target


def load():
    try:
        with open(PORTFOLIO_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return [h for h in data if h.get("symbol") and h.get("shares", 0) > 0]
    except (OSError, ValueError):
        return []


def save(holdings):
    with open(PORTFOLIO_FILE, "w", encoding="utf-8") as f:
        json.dump(holdings, f, ensure_ascii=False, indent=1)


def build_suggested(amount, prices):
    """Split amount equally across SUGGESTED at current prices (whole shares)."""
    available = [s for s in SUGGESTED if prices.get(s)]
    if not available:
        return []
    each = amount / len(available)
    holdings = []
    for s in available:
        shares = int(each // prices[s])
        if shares > 0:
            holdings.append({"symbol": s, "shares": shares, "cost": round(prices[s], 2)})
    return holdings


def next_rebalance(today=None):
    today = today or date.today()
    this_year = date(today.year, REBALANCE_MONTH, 1)
    return this_year if today <= this_year else date(today.year + 1, REBALANCE_MONTH, 1)


def evaluate(holdings, prices, dividends_per_share, names=None, today=None):
    names = names or {}
    rows = []
    for h in holdings:
        price = prices.get(h["symbol"])
        value = h["shares"] * price if price else None
        cost_total = h["shares"] * h.get("cost", 0)
        rows.append({
            "symbol": h["symbol"],
            "name": names.get(h["symbol"], ""),
            "shares": h["shares"],
            "cost": h.get("cost", 0),
            "price": price,
            "value": value,
            "cost_total": cost_total,
            "pnl": value - cost_total if value is not None else None,
            "pnl_pct": (value / cost_total - 1) * 100 if value is not None and cost_total else None,
            "dividend": h["shares"] * dividends_per_share[h["symbol"]] if dividends_per_share.get(h["symbol"]) else 0.0,
        })

    priced = [r for r in rows if r["value"] is not None]
    total_value = sum(r["value"] for r in priced)
    total_cost = sum(r["cost_total"] for r in rows)
    target = 1 / len(priced) if priced else 0
    needs_rebalance = False
    for r in rows:
        if r["value"] is None or not total_value:
            r["weight"] = r["drift"] = r["trade"] = None
            continue
        r["weight"] = r["value"] / total_value
        r["drift"] = r["weight"] / target - 1
        gap = total_value * target - r["value"]
        # ignore small gaps; trading them costs more in commission than it fixes
        r["trade"] = round(gap / r["price"]) if abs(gap) >= 0.1 * total_value * target else 0
        if abs(r["drift"]) > DRIFT_LIMIT:
            needs_rebalance = True

    nxt = next_rebalance(today)
    return {
        "rows": rows,
        "total_value": total_value,
        "total_cost": total_cost,
        "pnl": total_value - total_cost if priced else None,
        "pnl_pct": (total_value / total_cost - 1) * 100 if total_cost and priced else None,
        "dividends": sum(r["dividend"] for r in rows),
        "target_weight": target,
        "needs_rebalance": needs_rebalance,
        "next_rebalance": nxt,
        "rebalance_due": (today or date.today()).month == REBALANCE_MONTH,
        "missing_prices": [r["symbol"] for r in rows if r["price"] is None],
    }


def parse_form(symbols, shares, costs):
    holdings = {}
    for s, n, c in zip(symbols, shares, costs):
        s = s.strip().upper().removesuffix(".SR")
        try:
            n = int(float(n))
            c = float(c) if c not in ("", None) else 0.0
        except ValueError:
            continue
        if s and n > 0 and not math.isnan(c):
            holdings[s] = {"symbol": s, "shares": n, "cost": round(c, 4)}
    return list(holdings.values())
