"""
scanner.py - finds and scores candidate memecoins.

Data source: DexScreener public API (no API key required).
Everything here is READ-ONLY: it never touches a wallet.
"""

import json
import time
import urllib.error
import urllib.request

API = "https://api.dexscreener.com"
UA = {"User-Agent": "memebot/0.1", "Accept": "application/json"}


def _get(path, timeout=15):
    req = urllib.request.Request(API + path, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def latest_profiles():
    """Newest token profiles across all chains."""
    try:
        data = _get("/token-profiles/latest/v1")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print("[scanner] profiles fetch failed: %s" % exc)
        return []
    return data if isinstance(data, list) else []


def latest_boosts():
    """Tokens whose owners paid for promotion (attention signal, noisy)."""
    try:
        data = _get("/token-boosts/latest/v1")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print("[scanner] boosts fetch failed: %s" % exc)
        return []
    return data if isinstance(data, list) else []


def token_pairs(addresses):
    """Full pair data for up to 30 addresses per call."""
    pairs = []
    addrs = list(addresses)
    for i in range(0, len(addrs), 30):
        chunk = addrs[i:i + 30]
        try:
            data = _get("/latest/dex/tokens/" + ",".join(chunk))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            print("[scanner] token fetch failed: %s" % exc)
            continue
        for p in (data.get("pairs") or []):
            pairs.append(p)
        time.sleep(0.2)
    return pairs


def best_pair_per_token(pairs, chain):
    """Keep only the deepest pair for each token."""
    best = {}
    for p in pairs:
        if p.get("chainId") != chain:
            continue
        addr = (p.get("baseToken") or {}).get("address")
        if not addr:
            continue
        liq = float(((p.get("liquidity") or {}).get("usd")) or 0)
        cur = best.get(addr)
        if cur is None or liq > cur[0]:
            best[addr] = (liq, p)
    return {a: v[1] for a, v in best.items()}


def _num(d, *path, default=0.0):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    try:
        return float(cur)
    except (TypeError, ValueError):
        return default


def metrics(pair, now_ms=None):
    """Flatten a DexScreener pair into the numbers we care about."""
    now_ms = now_ms or int(time.time() * 1000)
    created = pair.get("pairCreatedAt") or 0
    age_min = (now_ms - created) / 60000.0 if created else 1e9

    txns = pair.get("txns") or {}
    m5 = txns.get("m5") or {}
    buys = float(m5.get("buys") or 0)
    sells = float(m5.get("sells") or 0)

    return {
        "chain": pair.get("chainId"),
        "dex": pair.get("dexId"),
        "pair_address": pair.get("pairAddress"),
        "address": (pair.get("baseToken") or {}).get("address"),
        "symbol": (pair.get("baseToken") or {}).get("symbol") or "?",
        "name": (pair.get("baseToken") or {}).get("name") or "?",
        "price": _num(pair, "priceUsd"),
        "liquidity": _num(pair, "liquidity", "usd"),
        "fdv": _num(pair, "fdv"),
        "market_cap": _num(pair, "marketCap"),
        "vol_h1": _num(pair, "volume", "h1"),
        "vol_h24": _num(pair, "volume", "h24"),
        "chg_m5": _num(pair, "priceChange", "m5"),
        "chg_h1": _num(pair, "priceChange", "h1"),
        "buys_m5": buys,
        "sells_m5": sells,
        "txns_m5": buys + sells,
        "buy_ratio": (buys / (buys + sells)) if (buys + sells) > 0 else 0.0,
        "age_min": age_min,
        "url": pair.get("url"),
    }


def apply_filters(m, f):
    """Hard gates. Returns (passed, list_of_rejection_reasons)."""
    why = []
    if m["symbol"].upper() in [s.upper() for s in f.get("blocked_symbols", [])]:
        why.append("blocked symbol")
    if m["liquidity"] < f["min_liquidity_usd"]:
        why.append("liquidity too low (%.0f)" % m["liquidity"])
    if m["liquidity"] > f["max_liquidity_usd"]:
        why.append("liquidity too high (already crowded)")
    if m["vol_h1"] < f["min_volume_h1_usd"]:
        why.append("h1 volume too low (%.0f)" % m["vol_h1"])
    if m["txns_m5"] < f["min_txns_m5"]:
        why.append("too few trades in m5 (%.0f)" % m["txns_m5"])
    if m["buy_ratio"] < f["min_buy_ratio_m5"]:
        why.append("sellers dominating (%.2f)" % m["buy_ratio"])
    if m["age_min"] < f["min_age_minutes"]:
        why.append("too new - sniper zone (%.1f min)" % m["age_min"])
    if m["age_min"] > f["max_age_minutes"]:
        why.append("too old (%.0f min)" % m["age_min"])
    if m["fdv"] and m["fdv"] < f["min_fdv_usd"]:
        why.append("fdv too small")
    if m["fdv"] and m["fdv"] > f["max_fdv_usd"]:
        why.append("fdv too large - no room to run")
    if m["chg_m5"] > f["max_change_m5_pct"]:
        why.append("already pumped %.0f%% in 5m" % m["chg_m5"])
    if m["chg_m5"] < f["min_change_m5_pct"]:
        why.append("in freefall %.0f%%" % m["chg_m5"])
    return (len(why) == 0), why


def score(m, weights):
    """0-100 heuristic score. Tune this only after you have paper results."""
    s = 0.0
    # buy pressure: 0.9 -> 0 points, 2.0+ -> full points
    s += weights["buy_ratio_m5"] * _clamp01((m["buy_ratio"] - 0.9) / 1.1)
    # volume: 3k -> 0, 150k -> full
    s += weights["volume_h1"] * _clamp01((m["vol_h1"] - 3000) / 147000)
    # liquidity sweet spot: 8k -> 0, 120k -> full, then fades
    s += weights["liquidity"] * _clamp01((m["liquidity"] - 8000) / 112000)
    # trade count
    s += weights["txns_m5"] * _clamp01((m["txns_m5"] - 10) / 90)
    # momentum: prefer 5-60% in 5m, punish overextension
    chg = m["chg_m5"]
    if 5 <= chg <= 60:
        s += weights["momentum_m5"]
    elif 0 <= chg < 5:
        s += weights["momentum_m5"] * 0.5
    elif chg > 60:
        s += weights["momentum_m5"] * 0.3
    # age sweet spot: 5-120 min
    if 5 <= m["age_min"] <= 120:
        s += weights["age_sweetspot"]
    elif 120 < m["age_min"] <= 480:
        s += weights["age_sweetspot"] * 0.5
    return round(min(s, 100.0), 1)


def _clamp01(x):
    return max(0.0, min(1.0, x))
