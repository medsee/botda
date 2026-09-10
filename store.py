"""
store.py - SQLite ledger for paper trades.

Nothing is deleted, ever. You need the full history to know whether
your rules actually make money or just feel good.
"""

import json
import os
import sqlite3
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memebot.db")


def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    _init(con)
    return con


def _init(con):
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY,
            ts REAL, address TEXT, symbol TEXT, score REAL,
            passed INTEGER, reason TEXT, metrics TEXT
        );
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY,
            address TEXT, symbol TEXT, opened_at REAL, closed_at REAL,
            entry_price REAL, qty REAL, size_usd REAL, remaining_qty REAL,
            peak_price REAL, tp_done TEXT, status TEXT
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            position_id INTEGER, ts REAL, side TEXT, price REAL,
            qty REAL, usd REAL, cost_usd REAL, reason TEXT
        );
        CREATE TABLE IF NOT EXISTS equity (
            id INTEGER PRIMARY KEY, ts REAL, equity_usd REAL, note TEXT
        );
        """
    )
    con.commit()


def log_candidate(con, m, score, passed, reason):
    con.execute(
        "INSERT INTO candidates (ts, address, symbol, score, passed, reason, metrics)"
        " VALUES (?,?,?,?,?,?,?)",
        (time.time(), m["address"], m["symbol"], score, 1 if passed else 0,
         reason, json.dumps(m)),
    )
    con.commit()


def open_position(con, m, price, size_usd, qty):
    cur = con.execute(
        "INSERT INTO positions (address, symbol, opened_at, entry_price, qty,"
        " size_usd, remaining_qty, peak_price, tp_done, status)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (m["address"], m["symbol"], time.time(), price, qty, size_usd, qty,
         price, json.dumps([]), "open"),
    )
    con.commit()
    return cur.lastrowid


def add_trade(con, position_id, side, price, qty, usd, cost_usd, reason):
    con.execute(
        "INSERT INTO trades (position_id, ts, side, price, qty, usd, cost_usd, reason)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (position_id, time.time(), side, price, qty, usd, cost_usd, reason),
    )
    con.commit()


def update_position(con, pid, remaining_qty, peak_price, tp_done, status="open"):
    con.execute(
        "UPDATE positions SET remaining_qty=?, peak_price=?, tp_done=?, status=?"
        " WHERE id=?",
        (remaining_qty, peak_price, json.dumps(tp_done), status, pid),
    )
    con.commit()


def close_position(con, pid):
    con.execute("UPDATE positions SET closed_at=?, status=? WHERE id=?",
                (time.time(), "closed", pid))
    con.commit()


def open_positions(con):
    return [dict(r) for r in con.execute(
        "SELECT * FROM positions WHERE status='open' ORDER BY opened_at")]


def log_equity(con, equity, note=""):
    con.execute("INSERT INTO equity (ts, equity_usd, note) VALUES (?,?,?)",
                (time.time(), equity, note))
    con.commit()


def report(con):
    """Win rate, expectancy and where the exits actually happened."""
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM positions WHERE status='closed'")]
    if not rows:
        return {"closed": 0}

    wins, losses, gross, costs = 0, 0, 0.0, 0.0
    by_reason = {}
    for p in rows:
        trades = [dict(t) for t in con.execute(
            "SELECT * FROM trades WHERE position_id=? ORDER BY ts", (p["id"],))]
        buys = sum(t["usd"] for t in trades if t["side"] == "buy")
        sells = sum(t["usd"] for t in trades if t["side"] == "sell")
        cost = sum(t["cost_usd"] for t in trades)
        leftover = p["remaining_qty"] * p["entry_price"]
        pnl = sells + leftover - buys - cost
        gross += pnl
        costs += cost
        if pnl > 0:
            wins += 1
        else:
            losses += 1
        last = trades[-1]["reason"] if trades else "?"
        by_reason[last] = by_reason.get(last, 0) + 1

    n = len(rows)
    return {
        "closed": n,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(100.0 * wins / n, 1) if n else 0.0,
        "net_pnl_usd": round(gross, 2),
        "total_costs_usd": round(costs, 2),
        "avg_pnl_per_trade_usd": round(gross / n, 2) if n else 0.0,
        "exit_reasons": by_reason,
    }
