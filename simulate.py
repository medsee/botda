"""
simulate.py - Monte Carlo test of the exit rules in risk.py.

WHY THIS FILE EXISTS
--------------------
Before you risk one dollar, you need to know whether the rules make money.
This runs thousands of synthetic trades through the EXACT same exit logic
the live bot will use, then tells you the expected outcome.

"edge" = how good your filters are. 0 means you buy random tokens
(no skill). 0.5 means your filters remove half of the junk before you buy.
The point of this file is to show you how much edge you need to break even.

The price model is NOT a forecast. It is a rough calibration to published
2026 statistics: ~98.6% of launched tokens never graduate, tokens that run
do so in minutes, and rugs happen fast and completely.
"""

import argparse
import json
import math
import os
import random

import risk

HERE = os.path.dirname(os.path.abspath(__file__))

# --- model assumptions (change these, but write down why) ---------------
RUG_PROB = 0.12        # share of trades that get rugged / liquidity pulled
RUGGED_FLOOR = 0.08    # where price ends after a rug
PUMP_PROB = 0.10       # share of trades that get a real run
HOURLY_DECAY = 0.08    # baseline decay per hour for a normal memecoin
HOURLY_VOL = 0.30      # baseline volatility per hour
MAX_MIN = 240          # matches exits.max_hold_minutes


def load_config():
    with open(os.path.join(HERE, "config.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def make_path(rng, edge):
    """Return a list of price multipliers, one per minute."""
    mu = math.log(1 - HOURLY_DECAY) / 60.0
    sigma = HOURLY_VOL / math.sqrt(60.0)

    rug = rng.random() < RUG_PROB * (1 - edge)
    pump = (not rug) and (rng.random() < PUMP_PROB * (1 + edge))

    path = [1.0]
    rug_at = rng.randint(1, 40) if rug else None
    pump_at = rng.randint(3, 60) if pump else None
    pump_len = rng.randint(15, 60) if pump else 0
    pump_peak = rng.uniform(2.0, 7.0) if pump else 0.0

    for m in range(1, MAX_MIN + 1):
        prev = path[-1]
        nxt = prev * math.exp(rng.gauss(mu, sigma))
        if rug and m == rug_at:
            nxt = prev * rng.uniform(0.30, 0.60)
        if rug and m > rug_at:
            nxt = nxt * 0.90
        if pump and pump_at <= m < pump_at + pump_len:
            step = (pump_peak ** (1.0 / pump_len))
            nxt = nxt * step
        path.append(max(nxt, 0.0001))
    return path, rug, rug_at


def simulate_trade(cfg, rng, edge, size_usd):
    c = cfg["costs"]
    ex = cfg["exits"]

    entry_slip = 1.0 + c["assumed_entry_slippage_pct"] / 100.0
    entry_fee = size_usd * (c["fee_bps_per_side"] / 10000.0) + c["priority_fee_usd"]
    eff_entry = 1.0 * entry_slip
    qty = (size_usd - entry_fee) / eff_entry
    remaining = qty
    proceeds = 0.0

    path, rug, rug_at = make_path(rng, edge)
    t0 = 1_000_000.0
    pos = {"entry_price": eff_entry, "opened_at": t0,
           "peak_price": eff_entry, "tp_done": []}

    last_price = eff_entry
    for minute, mult in enumerate(path):
        price = eff_entry * mult
        last_price = price
        pos["peak_price"] = max(pos["peak_price"], price)
        now = t0 + minute * 60

        for frac, reason in risk.check_exits(pos, price, cfg, now):
            sell_qty = remaining if frac >= 0.999 else remaining * frac
            if sell_qty <= 0:
                continue
            usd = sell_qty * price * (1 - c["assumed_exit_slippage_pct"] / 100.0)
            fee = usd * (c["fee_bps_per_side"] / 10000.0) + c["priority_fee_usd"]
            proceeds += usd - fee
            remaining -= sell_qty
            if reason.startswith("tp_"):
                pos["tp_done"].append(reason.split("_")[1])

        if remaining <= 1e-12:
            break

    leftover = remaining * last_price
    return proceeds + leftover - size_usd


def run(cfg, edge, n_trades, capital):
    rng = random.Random(12345 + int(edge * 1000))
    size = risk.position_size_usd(capital, cfg, capital)
    size = max(size, 5.0)

    equity = capital
    peak = equity
    max_dd = 0.0
    wins = 0
    total = 0.0
    ruined = False
    trades_run = 0

    for _ in range(n_trades):
        pnl = simulate_trade(cfg, rng, edge, size)
        equity += pnl
        total += pnl
        trades_run += 1
        if pnl > 0:
            wins += 1
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak * 100.0)
        if equity <= capital * 0.5:
            ruined = True
        if equity <= 0:
            equity = 0.0
            break

    return {
        "edge": edge,
        "trades_run": trades_run,
        "win_rate_pct": round(100.0 * wins / trades_run, 1) if trades_run else 0.0,
        "avg_pnl_per_trade_usd": round(total / trades_run, 2) if trades_run else 0.0,
        "equity_end_usd": round(equity, 2),
        "return_pct": round((equity / capital - 1) * 100.0, 1),
        "max_drawdown_pct": round(max_dd, 1),
        "halved_capital": ruined,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", type=int, default=400)
    ap.add_argument("--capital", type=float, default=None)
    args = ap.parse_args()

    cfg = load_config()
    capital = args.capital or float(cfg["capital_usd"])
    size = max(risk.position_size_usd(capital, cfg, capital), 5.0)

    print("Monte Carlo of the exit rules in risk.py")
    print("capital $%.0f | position size $%.2f | %d trades per scenario"
          % (capital, size, args.trades))
    print("costs: %.0f bps/side + $%.2f priority + %.1f%%/%.1f%% slippage"
          % (cfg["costs"]["fee_bps_per_side"], cfg["costs"]["priority_fee_usd"],
             cfg["costs"]["assumed_entry_slippage_pct"],
             cfg["costs"]["assumed_exit_slippage_pct"]))
    print("")
    print("%-7s %-8s %-11s %-12s %-9s %-12s" %
          ("edge", "win%", "avg/trade", "equity end", "max DD%", "trades run"))

    rows = []
    for edge in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.7]:
        r = run(cfg, edge, args.trades, capital)
        rows.append(r)
        busted = " (busted)" if r["equity_end_usd"] <= 0 else ""
        print("%-7s %-8s %-11s %-12s %-9s %-12s" %
              ("%.0f%%" % (edge * 100), r["win_rate_pct"], r["avg_pnl_per_trade_usd"],
               r["equity_end_usd"], r["max_drawdown_pct"],
               str(r["trades_run"]) + busted))

    be = next((r for r in rows if r["avg_pnl_per_trade_usd"] > 0), None)
    print("")
    if be is None:
        print("RESULT: no tested level of filter quality made money.")
        print("The edge required is beyond what these rules can deliver.")
    else:
        print("RESULT: break-even needs roughly %.0f%% filter edge or better."
              % (be["edge"] * 100))
    print("These are synthetic paths, not a forecast. Use them to compare")
    print("rule sets against each other, not to predict your profit.")


if __name__ == "__main__":
    main()
