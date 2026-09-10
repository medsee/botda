"""
engine.py - main loop.

PAPER TRADING ONLY in v0.1. It reads live market data and simulates
orders against it, with realistic fees and slippage. It never signs
a transaction and never touches a wallet.

Usage:
    python engine.py --once          # one scan + manage open positions
    python engine.py                 # loop forever (poll_interval_sec)
    python engine.py --report        # show results so far
    python engine.py --scan-only     # show candidates, open nothing
"""

import argparse
import json
import os
import sys
import time

import risk
import scanner
import store

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")


def load_config():
    with open(CONFIG, "r", encoding="utf-8") as f:
        return json.load(f)


def compute_cash(con, capital):
    cash = float(capital)
    for t in con.execute("SELECT side, usd, cost_usd FROM trades ORDER BY ts"):
        if t["side"] == "buy":
            cash -= (t["usd"] + t["cost_usd"])
        else:
            cash += (t["usd"] - t["cost_usd"])
    return cash


def realized_pnl_today(con):
    start = time.time() - 86400
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM positions WHERE status='closed' AND closed_at>=?", (start,))]
    total = 0.0
    for p in rows:
        trades = [dict(t) for t in con.execute(
            "SELECT * FROM trades WHERE position_id=?", (p["id"],))]
        buys = sum(t["usd"] for t in trades if t["side"] == "buy")
        sells = sum(t["usd"] for t in trades if t["side"] == "sell")
        cost = sum(t["cost_usd"] for t in trades)
        total += sells + p["remaining_qty"] * p["entry_price"] - buys - cost
    return total


def discover(cfg, limit=40):
    """Get fresh tokens on our chain, then enrich them."""
    chain = cfg["chain"]
    addrs = []
    for item in scanner.latest_profiles():
        if item.get("chainId") == chain and item.get("tokenAddress"):
            addrs.append(item["tokenAddress"])
    for item in scanner.latest_boosts():
        if item.get("chainId") == chain and item.get("tokenAddress"):
            addrs.append(item["tokenAddress"])

    seen, uniq = set(), []
    for a in addrs:
        if a not in seen:
            seen.add(a)
            uniq.append(a)
    uniq = uniq[:limit]

    if not uniq:
        return []
    pairs = scanner.token_pairs(uniq)
    return scanner.best_pair_per_token(pairs, chain)


def manage_open(con, cfg, price_map):
    """Exits first. Always."""
    for p in store.open_positions(con):
        m = price_map.get(p["address"])
        if not m or not m["price"]:
            continue
        price = m["price"]
        peak = max(p["peak_price"] or p["entry_price"], price)
        tp_done = list(json.loads(p["tp_done"] or "[]"))
        remaining = p["remaining_qty"]

        actions = risk.check_exits(p, price, cfg)
        for frac, reason in actions:
            qty = remaining if frac >= 0.999 else remaining * frac
            if qty <= 0:
                continue
            usd = qty * price
            cost = risk.apply_costs(usd, cfg, "sell")
            store.add_trade(con, p["id"], "sell", price, qty, usd, cost, reason)
            remaining -= qty
            if reason.startswith("tp_"):
                tp_done.append(reason.split("_")[1])
            print("  [SELL] %s %.0f%% @ %.8f  reason=%s" %
                  (p["symbol"], frac * 100, price, reason))

        store.update_position(con, p["id"], remaining, peak, tp_done,
                              "open" if remaining > 1e-9 else "closed")
        if remaining <= 1e-9:
            store.close_position(con, p["id"])


def try_open(con, cfg, cands, cash, capital, today_pnl):
    opens = store.open_positions(con)
    deployed = 0.0
    for p in opens:
        deployed += (p["remaining_qty"] or 0) * (p["entry_price"] or 0)

    for m, sc in cands:
        ok, why = risk.can_open(cfg, opens, deployed, capital, today_pnl)
        if not ok:
            print("  [HOLD] %s" % why)
            break
        size = risk.position_size_usd(capital, cfg, cash)
        if size < 5:
            print("  [HOLD] not enough cash for a new position")
            break

        slip = 1.0 + cfg["costs"]["assumed_entry_slippage_pct"] / 100.0
        fill = m["price"] * slip
        qty = (size - risk.apply_costs(size, cfg, "buy")) / fill
        cost = risk.apply_costs(size, cfg, "buy")

        pid = store.open_position(con, m, fill, size, qty)
        store.add_trade(con, pid, "buy", fill, qty, size, cost, "entry")
        cash -= size
        deployed += size
        opens = store.open_positions(con)
        print("  [BUY ] %s  score=%.1f  $%.2f @ %.8f" %
              (m["symbol"], sc, size, fill))


def run_pass(con, cfg, scan_only=False):
    capital = float(cfg["capital_usd"])
    cash = compute_cash(con, capital)
    today_pnl = realized_pnl_today(con)

    print("--- pass %s | cash $%.2f | today realized $%.2f ---" %
          (time.strftime("%H:%M:%S"), cash, today_pnl))

    pairs = discover(cfg, cfg["universe"]["max_tokens_per_pass"])
    price_map = {}
    cands = []
    rejects = {}
    now_ms = int(time.time() * 1000)

    for addr, pair in pairs.items():
        m = scanner.metrics(pair, now_ms)
        price_map[addr] = m
        passed, why = scanner.apply_filters(m, cfg["filters"])
        sc = scanner.score(m, cfg["scoring"]["weights"])
        store.log_candidate(con, m, sc, passed, "; ".join(why))
        for w in why:
            key = w.split(" (")[0]
            rejects[key] = rejects.get(key, 0) + 1
        if passed and sc >= cfg["scoring"]["min_score_to_trade"]:
            cands.append((m, sc))

    cands.sort(key=lambda x: -x[1])

    print("  scanned %d tokens, %d passed filters (score >= %.0f)" %
          (len(pairs), len(cands), cfg["scoring"]["min_score_to_trade"]))
    if rejects:
        top = sorted(rejects.items(), key=lambda x: -x[1])[:5]
        print("  top rejections: " + ", ".join("%s x%d" % (k, v) for k, v in top))
    for m, sc in cands[:8]:
        print("    %-12s score=%5.1f  liq=$%-9.0f vol1h=$%-9.0f buy/sell=%.2f  age=%.0fm"
              % (m["symbol"][:12], sc, m["liquidity"], m["vol_h1"],
                 m["buy_ratio"], m["age_min"]))

    if scan_only:
        return

    manage_open(con, cfg, price_map)
    try_open(con, cfg, cands, cash, capital, today_pnl)

    opens = store.open_positions(con)
    unreal = sum((p["remaining_qty"] or 0) *
                 ((price_map.get(p["address"]) or {}).get("price") or p["entry_price"])
                 for p in opens)
    equity = compute_cash(con, capital) + unreal
    store.log_equity(con, equity, "pass")
    print("  equity $%.2f | open %d" % (equity, len(opens)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--scan-only", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--interval", type=int, default=None)
    args = ap.parse_args()

    cfg = load_config()
    if cfg.get("mode") != "paper":
        print("ERROR: live mode is not implemented in v0.1.")
        print("Set mode='paper' in config.json. Do not connect real money")
        print("until this system has at least 100 closed paper trades with")
        print("positive net PnL AFTER costs.")
        sys.exit(1)

    con = store.connect()

    if args.report:
        print(json.dumps(store.report(con), indent=2))
        return

    interval = args.interval or int(cfg.get("poll_interval_sec", 60))
    if args.once or args.scan_only:
        run_pass(con, cfg, scan_only=args.scan_only)
        print(json.dumps(store.report(con), indent=2))
        return

    print("memebot paper engine running. Ctrl+C to stop.")
    while True:
        try:
            run_pass(con, cfg)
        except KeyboardInterrupt:
            print("\nstopped")
            break
        except Exception as exc:  # keep the loop alive
            print("[engine] pass failed: %s" % exc)
        time.sleep(interval)


if __name__ == "__main__":
    main()
