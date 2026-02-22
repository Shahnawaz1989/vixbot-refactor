# bot3_high_vol_rule.py

from typing import Dict, Any
import pandas as pd
from datetime import datetime, time


def run_bot3_method_a(
    idx15: pd.DataFrame,
    bo_ctx: Dict[str, Any],
    gann_map: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Method A – GHB style, 10:30 ke baad tight entry.
    - Sirf 10:30 ke baad entry allow.
    - Primary leg = H8 (BUY) / N8 (SELL).
    - Hedge leg = M8 (BUY hedge for SELL) / J8 (SELL hedge for BUY).
    """
    tradedate = bo_ctx["trade_date"]
    boside = bo_ctx["boside"]          # "BUY" / "SELL"
    bo_time = bo_ctx["bo_time"]        # datetime
    atr15_915 = bo_ctx["atr15_915"]    # float

    # 10:30 ke pehle Method A entry mat lo
    cutoff = time(10, 30)
    if bo_time.time() < cutoff:
        print("[BOT3-METHOD-A] BO before 10:30, skip Method A, use Method B.")
        return {"status": "SKIP_METHOD_A", "reason": "BO_BEFORE_1030"}

    # 10:30 ke baad, BO candle ke close ke aas-paas ek simple band bana sakte ho
    # filhaal tight band logic simple rakhenge: +/- 0.5 * ATR15_915
    # (baad me tune kar sakte ho)
    band = 0.5 * atr15_915

    if boside == "BUY":
        primary_key = "H8"
        hedge_key = "J8"   # OPP side hedge
    else:
        primary_key = "N8"
        hedge_key = "M8"

    primary_entry = gann_map.get(primary_key)
    hedge_entry = gann_map.get(hedge_key)

    print(
        f"[BOT3-METHOD-A] {tradedate} boside={boside} "
        f"primary={primary_key}={primary_entry} hedge={hedge_key}={hedge_entry} "
        f"band={band:.2f}"
    )

    if primary_entry is None or hedge_entry is None:
        return {"status": "SKIP_METHOD_A", "reason": "MISSING_GANN_LEVELS"}

    # Abhi ke liye, sirf mapping return kar rahe hain, actual trade execution
    # outer engine karega (jaise existing bots me hota hai)
    return {
        "status": "OK_METHOD_A",
        "method": "A",
        "boside": boside,
        "primary_key": primary_key,
        "primary_entry": float(primary_entry),
        "hedge_key": hedge_key,
        "hedge_entry": float(hedge_entry),
        "band": float(band),
    }


def run_bot3_method_b(
    idx15: pd.DataFrame,
    bo_ctx: Dict[str, Any],
    gann_map: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Method B – normal ORB-style mapping.
    - BO candle ke side ke hisaab se primary leg H8/N8.
    - Hedge leg J8/M8 simple.
    """
    tradedate = bo_ctx["trade_date"]
    boside = bo_ctx["boside"]          # "BUY" / "SELL"
    atr15_915 = bo_ctx["atr15_915"]    # float

    if boside == "BUY":
        primary_key = "H8"
        hedge_key = "J8"
    else:
        primary_key = "N8"
        hedge_key = "M8"

    primary_entry = gann_map.get(primary_key)
    hedge_entry = gann_map.get(hedge_key)

    print(
        f"[BOT3-METHOD-B] {tradedate} boside={boside} "
        f"primary={primary_key}={primary_entry} hedge={hedge_key}={hedge_entry} "
        f"atr15_915={atr15_915:.2f}"
    )

    if primary_entry is None or hedge_entry is None:
        return {"status": "SKIP_METHOD_B", "reason": "MISSING_GANN_LEVELS"}

    return {
        "status": "OK_METHOD_B",
        "method": "B",
        "boside": boside,
        "primary_key": primary_key,
        "primary_entry": float(primary_entry),
        "hedge_key": hedge_key,
        "hedge_entry": float(hedge_entry),
        "atr15_915": float(atr15_915),
    }


def detect_bot3_method(
    idx1m: pd.DataFrame,
    atr15_at_915: float,
) -> Dict[str, Any]:
    """
    Decide Method A vs Method B for Bot-3 based on 9:15 candle range vs ATR.

    - idx1m: full 1-min index DF for trade_date (DatetimeIndex)
    - atr15_at_915: 15-min ATR(14) value at 9:15 candle (externally computed)

    Method A: range_915 / atr15_at_915 > 2.0
    Method B: otherwise
    """
    if idx1m.empty or atr15_at_915 <= 0:
        return {
            "method": "B",
            "range_915": 0.0,
            "ratio_915_atr": 0.0,
        }

    tradedate = idx1m.index[0].date()

    start_915 = datetime.combine(
        tradedate, datetime.strptime("09:15", "%H:%M").time())
    end_930 = datetime.combine(
        tradedate, datetime.strptime("09:30", "%H:%M").time())

    win_915 = idx1m[(idx1m.index >= start_915) & (idx1m.index < end_930)]
    if win_915.empty:
        return {
            "method": "B",
            "range_915": 0.0,
            "ratio_915_atr": 0.0,
        }

    high_915 = float(win_915["high"].max())
    low_915 = float(win_915["low"].min())
    range_915 = high_915 - low_915
    ratio = range_915 / atr15_at_915 if atr15_at_915 > 0 else 0.0

    method = "A" if ratio > 2.0 else "B"

    print(
        f"[BOT3-METHOD] range_915={range_915:.2f} atr15_915={atr15_at_915:.2f} ratio={ratio:.2f} method={method}")

    return {
        "method": method,
        "range_915": range_915,
        "ratio_915_atr": ratio,
    }


def build_bot3_breakout_context(
    idx1m: pd.DataFrame,
) -> Dict[str, Any]:
    """
    9:15 ORB breakout context for Bot-3.

    - Mark 9:15-9:30 high/low.
    - Find 15-min close-based BO (9:30 ke baad) till 12:15.
    """
    if idx1m.empty:
        return {
            "status": "ERROR",
            "bo_side": None,
            "bo_time": None,
            "bo_close": None,
            "marked_high": None,
            "marked_low": None,
        }

    tradedate = idx1m.index[0].date()

    start_915 = datetime.combine(
        tradedate, datetime.strptime("09:15", "%H:%M").time())
    end_930 = datetime.combine(
        tradedate, datetime.strptime("09:30", "%H:%M").time())
    end_bo = datetime.combine(
        tradedate, datetime.strptime("12:15", "%H:%M").time())

    # 9:15-9:30 ORB window
    orb915 = idx1m[(idx1m.index >= start_915) & (idx1m.index < end_930)]
    if orb915.empty:
        return {
            "status": "ERROR",
            "bo_side": None,
            "bo_time": None,
            "bo_close": None,
            "marked_high": None,
            "marked_low": None,
        }

    marked_high = float(orb915["high"].max())
    marked_low = float(orb915["low"].min())

    # 9:30 se 12:15 tak 15-min candles
    trigger_window = idx1m[(idx1m.index >= end_930) & (idx1m.index <= end_bo)]
    if trigger_window.empty:
        return {
            "status": "NO_BO",
            "bo_side": None,
            "bo_time": None,
            "bo_close": None,
            "marked_high": marked_high,
            "marked_low": marked_low,
        }

    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    trigger15 = trigger_window.resample("15min").agg(agg).dropna()

    bo_side = None
    bo_time = None
    bo_close = None

    for ts, row in trigger15.iterrows():
        close_price = float(row["close"])

        if close_price > marked_high:
            bo_side = "BUY"
            bo_time = ts
            bo_close = close_price
            break
        if close_price < marked_low:
            bo_side = "SELL"
            bo_time = ts
            bo_close = close_price
            break

    if bo_side is None:
        return {
            "status": "NO_BO",
            "bo_side": None,
            "bo_time": None,
            "bo_close": None,
            "marked_high": marked_high,
            "marked_low": marked_low,
        }

    print(f"[BOT3-BO] time={bo_time} side={bo_side} close={bo_close}")

    return {
        "status": "OK",
        "bo_side": bo_side,
        "bo_time": bo_time,
        "bo_close": bo_close,
        "marked_high": marked_high,
        "marked_low": marked_low,
    }


def map_bot3_gann_levels(
    gann_levels: Dict[str, float],
) -> Dict[str, Any]:
    """
    Bot-3 ke liye H8/M8/N8/J8 raw levels dict.
    Yahan side-decisions nahi, sirf numbers.
    """
    h8 = float(gann_levels.get("H8", 0.0))
    m8 = float(gann_levels.get("M8", 0.0))
    n8 = float(gann_levels.get("N8", 0.0))
    j8 = float(gann_levels.get("J8", 0.0))

    print(f"[BOT3-GANN] H8={h8} M8={m8} N8={n8} J8={j8}")

    return {
        "H8": h8,
        "M8": m8,
        "N8": n8,
        "J8": j8,
    }


def run_bot3_entry_engine(
    idx1m: pd.DataFrame,
    idx15: pd.DataFrame,
    atr15_at_915: float,
    gann_levels: Dict[str, float],
) -> Dict[str, Any]:
    """
    Top-level Bot-3 engine (morning leg).
    """
    method_info = detect_bot3_method(idx1m, atr15_at_915)
    bo_ctx = build_bot3_breakout_context(idx1m)

    if bo_ctx["status"] != "OK":
        return {
            "status": f"SKIP_{bo_ctx['status']}",
            "method": method_info["method"],
            "bo_ctx": bo_ctx,
        }
    # yahan bo_ctx enrich karo
    bo_ctx["trade_date"] = idx1m.index[0].date()
    bo_ctx["boside"] = bo_ctx["bo_side"]
    bo_ctx["bo_time"] = bo_ctx["bo_time"]
    bo_ctx["atr15_915"] = atr15_at_915

    gann_map = map_bot3_gann_levels(gann_levels)

    if method_info["method"] == "A":
        res = run_bot3_method_a(idx15, bo_ctx, gann_map)
    else:
        res = run_bot3_method_b(idx15, bo_ctx, gann_map)

    return {
        "status": res.get("status", "BOT3_OK"),
        "method": method_info["method"],
        "bo_ctx": bo_ctx,
        "gann_map": gann_map,
        "inner": res,
    }
