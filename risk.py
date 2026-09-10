"""
risk.py - position sizing, portfolio limits and exit rules.

This is the part that decides whether you survive. The scanner only
decides what to look at; this file decides how much and when to leave.
"""

import time


def position_size_usd(capital, cfg, available_cash):
    """
    Risk-based sizing:
        size = (capital * risk_per_trade_pct) / |stop_loss_pct|
    So if the stop is hit, you lose exactly risk_per_trade_pct of capital
    (plus fees and slippage, which are NOT included here on purpose -
    they make the real loss worse, and you should see that in the report).
    """
    r = cfg["risk"]
    stop = abs(float(cfg["exits"]["stop_loss_pct"])) / 100.0
    raw = capital * (r["risk_per_trade_pct"] / 100.0) / stop
    size = min(raw, r["max_position_usd"], available_cash)
    return round(max(size, 0.0), 2)


def can_open(cfg, open_positions, deployed_usd, capital, today_pnl):
    """Portfolio level gates. Returns (ok, reason)."""
    r = cfg["risk"]
    if len(open_positions) >= r["max_open_positions"]:
        return False, "max open positions reached"
    if deployed_usd >= capital * (r["max_deployed_pct"] / 100.0):
        return False, "max deployed capital reached"
    if today_pnl <= -capital * (r["daily_loss_limit_pct"] / 100.0):
        return False, "daily loss limit hit - trading halted for today"
    return True, "ok"


def check_exits(pos, price, cfg, now=None):
    """
    Returns a list of (fraction_to_sell, reason).
    Later rules win: if the hard stop triggers, everything goes.
    """
    now = now or time.time()
    ex = cfg["exits"]
    out = []
    if price <= 0 or pos["entry_price"] <= 0:
        return out

    pnl_pct = (price / pos["entry_price"] - 1.0) * 100.0
    held_min = (now - pos["opened_at"]) / 60.0
    done = set(pos.get("tp_done") or [])

    # 1) hard stop - always full exit
    if pnl_pct <= float(ex["stop_loss_pct"]):
        return [(1.0, "stop_loss")]

    # 2) time stop - memecoins decay fast, do not marry them
    if held_min >= ex["max_hold_minutes"]:
        return [(1.0, "time_stop")]

    # 3) stale - nothing happened, free up the slot
    if held_min >= ex["stale_minutes"] and abs(pnl_pct) < ex["stale_band_pct"]:
        return [(1.0, "stale")]

    # 4) trailing stop - protects profit after a run
    peak = max(pos.get("peak_price") or pos["entry_price"], price)
    if pos["entry_price"] > 0:
        peak_gain = (peak / pos["entry_price"] - 1.0) * 100.0
        if peak_gain >= ex["trailing_activation_pct"]:
            drawdown = (price / peak - 1.0) * 100.0
            if drawdown <= -abs(ex["trailing_stop_pct"]):
                return [(1.0, "trailing_stop")]

    # 5) take profit ladder - scale out, never all at once
    for rung in ex["take_profit_ladder"]:
        key = str(rung["gain_pct"])
        if pnl_pct >= rung["gain_pct"] and key not in done:
            out.append((rung["sell_pct"] / 100.0, "tp_" + key))

    return out


def apply_costs(qty_usd, cfg, side):
    """
    Realistic round-trip cost model. Most bots that look profitable in
    a backtest die here: fees + priority fees + slippage.
    """
    c = cfg["costs"]
    fee = qty_usd * (c["fee_bps_per_side"] / 10000.0)
    slippage_pct = c["assumed_entry_slippage_pct"] if side == "buy" else c["assumed_exit_slippage_pct"]
    slip = qty_usd * (slippage_pct / 100.0)
    return fee + slip + c["priority_fee_usd"]
