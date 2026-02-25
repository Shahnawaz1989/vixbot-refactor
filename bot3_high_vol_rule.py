from typing import Dict, Any
import math
from datetime import datetime, time

import pandas as pd

from config import BOT3_HIGH_VOL_THRESHOLD

from gann_engine import get_gann_row_from_json


# ===== Common Helpers =====

def round_index_price_for_side(price: float, side: str) -> float:
    side = (side or "").upper()
    if side == "BUY":
        # hamesha upar round
        return float(math.ceil(price))
    elif side == "SELL":
        # hamesha neeche round
        return float(math.floor(price))
    else:
        # default: normal int
        return float(int(price))


# ===== Method A (intra-day entry search) =====

def run_bot3_method_a(
    idx1m: pd.DataFrame,   # 1-min data
    bo_ctx: Dict[str, Any],
    gann_map: Dict[str, Any],
) -> Dict[str, Any]:
    tradedate = bo_ctx["trade_date"]
    boside = bo_ctx["boside"]
    bo_time = bo_ctx["bo_time"]
    atr15_915 = bo_ctx["atr15_915"]

    band = 0.5 * atr15_915

    if boside == "BUY":
        primary_key = "H8"
        hedge_key = "J8"
    else:
        primary_key = "N8"
        hedge_key = "M8"

    primary_entry = gann_map.get("primary_entry")
    hedge_entry = gann_map.get("opp_entry")

    # Gann levels rounding
    primary_entry = round_index_price_for_side(
        float(primary_entry), "BUY" if boside == "BUY" else "SELL"
    )
    hedge_entry = round_index_price_for_side(
        float(hedge_entry), "SELL" if boside == "BUY" else "BUY"
    )

    print(
        f"[BOT3-METHOD-A] {tradedate} boside={boside} "
        f"bo_time={bo_time} primary={primary_entry} hedge={hedge_entry} band={band:.2f}"
    )

    if primary_entry is None or hedge_entry is None:
        return {"status": "SKIP_METHOD_A", "reason": "MISSING_GANN_LEVELS"}

    # 1-min entry search loop (10:31+ entry)
    entry_cutoff = datetime.combine(tradedate, time(10, 31))
    late_cutoff = datetime.combine(tradedate, time(13, 30))
    eod_cutoff = datetime.combine(tradedate, time(15, 0))

    search_df = idx1m[(idx1m.index >= entry_cutoff)
                      & (idx1m.index <= eod_cutoff)]

    entry_time = None
    entry_rule = None
    for ts, row in search_df.iterrows():
        if row["low"] <= primary_entry <= row["high"]:
            entry_time = ts
            is_late = ts >= late_cutoff
            entry_rule = "ORB_LATE" if is_late else "ATR_NORMAL"
            break

    if entry_time is None:
        return {"status": "SKIP_METHOD_A", "reason": "NO_ENTRY_CANDLE"}

    print(f"[BOT3-METHOD-A] Entry found at {entry_time} rule={entry_rule}")

    return {
        "status": "OK_METHOD_A",
        "method": "A",
        "boside": boside,
        "primary_entry": float(primary_entry),
        "hedge_entry": float(hedge_entry),
        "band": float(band),
        "entry_time": entry_time,
        "entry_rule": entry_rule,
    }


# ===== Method B (pure Gann mapping) =====

def run_bot3_method_b(
    idx15: pd.DataFrame,
    bo_ctx: Dict[str, Any],
    gann_map: Dict[str, Any],
) -> Dict[str, Any]:
    tradedate = bo_ctx["trade_date"]
    boside = bo_ctx["boside"]
    atr15_915 = bo_ctx["atr15_915"]

    if boside == "BUY":
        primary_key = "H8"
        hedge_key = "J8"
    else:
        primary_key = "N8"
        hedge_key = "M8"

    primary_entry_raw = gann_map.get(primary_key)
    hedge_entry_raw = gann_map.get(hedge_key)

    # agar level hi missing hai to yahin SKIP
    if primary_entry_raw is None or hedge_entry_raw is None:
        print(
            f"[BOT3-METHOD-B] {tradedate} boside={boside} "
            f"missing gann levels: {primary_key}={primary_entry_raw} "
            f"{hedge_key}={hedge_entry_raw}"
        )
        return {"status": "SKIP_METHOD_B", "reason": "MISSING_GANN_LEVELS"}

    primary_entry = round_index_price_for_side(
        float(primary_entry_raw), "BUY" if boside == "BUY" else "SELL"
    )
    hedge_entry = round_index_price_for_side(
        float(hedge_entry_raw), "SELL" if boside == "BUY" else "BUY"
    )

    print(
        f"[BOT3-METHOD-B] {tradedate} boside={boside} "
        f"primary={primary_key}={primary_entry} hedge={hedge_key}={hedge_entry} "
        f"atr15_915={atr15_915:.2f}"
    )

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


# ===== Method selection based on 9:15 range =====

def detect_bot3_method(
    idx1m: pd.DataFrame,
    atr15_at_915: float,
) -> Dict[str, Any]:
    """
    Decide Method A vs Method B for Bot-3 based on 9:15 candle range vs ATR.

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

    start_915 = datetime.combine(tradedate, time(9, 15))
    end_930 = datetime.combine(tradedate, time(9, 30))

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
        f"[BOT3-METHOD] range_915={range_915:.2f} atr15_915={atr15_at_915:.2f} "
        f"ratio={ratio:.2f} method={method}"
    )

    return {
        "method": method,
        "range_915": range_915,
        "ratio_915_atr": ratio,
    }


# ===== 9:15 ORB breakout context =====

def build_bot3_breakout_context(
    idx1m: pd.DataFrame,
) -> Dict[str, Any]:
    """
    9:15 ORB breakout context for Bot-3.

    - Mark 9:15-9:30 high/low.  (ORB style)
    - 9:30–12:15 window me 15-min close-based breakout detect karo. (ORB pattern)
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

    if not isinstance(idx1m.index, pd.DatetimeIndex):
        if "time" in idx1m.columns:
            idx1m = idx1m.copy()
            idx1m["time"] = pd.to_datetime(idx1m["time"])
            idx1m = idx1m.set_index("time")
        else:
            return {
                "status": "ERROR",
                "bo_side": None,
                "bo_time": None,
                "bo_close": None,
                "marked_high": None,
                "marked_low": None,
            }

    tradedate = idx1m.index[0].date()

    # 9:15–9:30 marking window (ORB style, bas time alag)
    mark_start = datetime.combine(tradedate, time(9, 15))
    mark_end = datetime.combine(tradedate, time(9, 30))
    bo_window_end = datetime.combine(tradedate, time(12, 15))

    marking = idx1m[(idx1m.index >= mark_start) & (idx1m.index < mark_end)]
    if marking.empty:
        return {
            "status": "ERROR",
            "bo_side": None,
            "bo_time": None,
            "bo_close": None,
            "marked_high": None,
            "marked_low": None,
        }

    # ORB pattern: raw mark, phir rounded triggers
    marked_high = float(marking["high"].max())
    marked_low = float(marking["low"].min())

    rounded_buy_trigger = round_index_price_for_side(marked_high, "BUY")
    rounded_sell_trigger = round_index_price_for_side(marked_low, "SELL")

    # 9:30–12:15 ORB-style trigger window
    trigger_df = idx1m[
        (idx1m.index >= mark_end) &
        (idx1m.index <= bo_window_end)
    ]
    if trigger_df.empty:
        return {
            "status": "NO_BO",
            "bo_side": None,
            "bo_time": None,
            "bo_close": None,
            "marked_high": marked_high,
            "marked_low": marked_low,
        }

    # 15-min resample
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    trigger15 = trigger_df.resample("15min").agg(agg).dropna()

    bo_side = None
    bo_time = None
    bo_close = None

    for ts, row in trigger15.iterrows():
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])

        print(
            f"[BOT3-ORB-STYLE-15M] {ts} close={close_price} "
            f"high={high_price} low={low_price} "
            f"rbuy={rounded_buy_trigger} rsell={rounded_sell_trigger}"
        )

        # BUY breakout
        if close_price >= rounded_buy_trigger:
            bo_side = "BUY"
            bo_time = ts
            bo_close = round_index_price_for_side(close_price, "BUY")
            break

        # SELL breakout
        if close_price <= rounded_sell_trigger:
            bo_side = "SELL"
            bo_time = ts
            bo_close = round_index_price_for_side(close_price, "SELL")
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


# ===== Gann mapping (targets + SL) =====

def map_bot3_gann_levels(
    bo_side: str,
    gann_levels: Dict[str, float],
    atr14: float,
    method: str,  # "A" ya "B"
) -> Dict[str, Any]:
    """
    Bot-3 ka complete entry/target/sl system.

    Method A OPP entry: sell_t2 / buy_t2 (tighter)
    Method B OPP entry: sell_entry / buy_entry (normal)

    Target dono me same: base_entry +/- ATR * 1.5 closest level
    SL dono me same: BUY SL = sell_entry, SELL SL = buy_entry
    """

    def pick_buy_target(base_entry: float) -> float:
        if atr14 <= 0:
            return float(gann_levels.get("buy_t2", 0.0))
        raw_target = base_entry + atr14 * 1.5
        candidates = [
            gann_levels.get("buy_t2", 0.0),
            gann_levels.get("buy_t25", 0.0),
            gann_levels.get("buy_t3", 0.0),
            gann_levels.get("buy_t35", 0.0),
            gann_levels.get("buy_t4", 0.0),
        ]
        below = [x for x in candidates if x <= raw_target]
        result = max(below) if below else min(candidates)
        print(
            f"[BOT3-TARGET] BUY base={base_entry:.2f} raw={raw_target:.2f} target={result:.2f}"
        )
        return float(result)

    def pick_sell_target(base_entry: float) -> float:
        if atr14 <= 0:
            return float(gann_levels.get("sell_t2", 0.0))
        raw_target = base_entry - atr14 * 1.5
        candidates = [
            gann_levels.get("sell_t2", 0.0),
            gann_levels.get("sell_t25", 0.0),
            gann_levels.get("sell_t3", 0.0),
            gann_levels.get("sell_t35", 0.0),
            gann_levels.get("sell_t4", 0.0),
        ]
        above = [x for x in candidates if x >= raw_target]
        result = min(above) if above else max(candidates)
        print(
            f"[BOT3-TARGET] SELL base={base_entry:.2f} raw={raw_target:.2f} target={result:.2f}"
        )
        return float(result)

    # OPP entry method ke hisaab se
    if method == "A":
        opp_buy_entry_key = "buy_t2"
        opp_sell_entry_key = "sell_t2"
    else:  # Method B
        opp_buy_entry_key = "buy_entry"
        opp_sell_entry_key = "sell_entry"

    if bo_side == "BUY":
        primary_entry = float(gann_levels.get("buy_t15", 0.0))
        primary_sl = float(gann_levels.get("sell_entry", 0.0))
        primary_target = pick_buy_target(
            float(gann_levels.get("buy_entry", 0.0)))

        opp_entry = float(gann_levels.get(opp_sell_entry_key, 0.0))
        opp_sl = float(gann_levels.get("buy_entry", 0.0))
        opp_target = pick_sell_target(
            float(gann_levels.get("sell_entry", 0.0)))

    elif bo_side == "SELL":
        primary_entry = float(gann_levels.get("sell_t15", 0.0))
        primary_sl = float(gann_levels.get("buy_entry", 0.0))
        primary_target = pick_sell_target(
            float(gann_levels.get("sell_entry", 0.0)))

        opp_entry = float(gann_levels.get(opp_buy_entry_key, 0.0))
        opp_sl = float(gann_levels.get("sell_entry", 0.0))
        opp_target = pick_buy_target(float(gann_levels.get("buy_entry", 0.0)))

    else:
        return {}

    print(
        f"[BOT3-GANN-MAP] method={method} bo_side={bo_side}\n"
        f"  PRIMARY: entry={primary_entry} sl={primary_sl} target={primary_target}\n"
        f"  OPP:     entry={opp_entry} sl={opp_sl} target={opp_target}"
    )

    return {
        "method":         method,
        "bo_side":        bo_side,
        "primary_entry":  primary_entry,
        "primary_sl":     primary_sl,
        "primary_target": primary_target,
        "opp_entry":      opp_entry,
        "opp_sl":         opp_sl,
        "opp_target":     opp_target,
    }


# ===== Entry Engine (A/B) =====

def run_bot3_entry_engine(
    idx1m: pd.DataFrame,
    idx15: pd.DataFrame,
    atr15_at_915: float,
    gann_levels: Dict[str, float],
) -> Dict[str, Any]:

    method_info = detect_bot3_method(idx1m, atr15_at_915)
    bo_ctx = build_bot3_breakout_context(idx1m)

    # 9:15 ORB window me breakout na mile -> yahin se NO_BO tag
    if bo_ctx["status"] != "OK":
        return {
            "status": f"SKIP_{bo_ctx['status']}",  # e.g. SKIP_NO_BO
            "method": method_info["method"],
            "bo_ctx": bo_ctx,
        }

    bo_ctx["trade_date"] = idx1m.index[0].date()
    bo_ctx["boside"] = bo_ctx["bo_side"]
    bo_ctx["bo_time"] = bo_ctx["bo_time"]
    bo_ctx["atr15_915"] = atr15_at_915

    method = method_info["method"]

    gann_map = map_bot3_gann_levels(
        bo_side=bo_ctx["bo_side"],
        gann_levels=gann_levels,
        atr14=atr15_at_915,
        method=method,
    )

    if method == "A":
        res_a = run_bot3_method_a(idx1m, bo_ctx, gann_map)
        if res_a.get("status") != "OK_METHOD_A":
            return {
                "status": "NO_TRADE_METHOD_A",
                "method": "A",
                "bo_ctx": bo_ctx,
                "gann_map": gann_map,
                "inner": res_a,
            }
        return {
            "status": "BOT3_OK_METHOD_A",
            "method": "A",
            "bo_ctx": bo_ctx,
            "gann_map": gann_map,
            "inner": res_a,
        }

    else:
        res_b = run_bot3_method_b(idx15, bo_ctx, gann_map)
        return {
            "status": res_b.get("status", "BOT3_OK_METHOD_B"),
            "method": "B",
            "bo_ctx": bo_ctx,
            "gann_map": gann_map,
            "inner": res_b,
        }


# ===== HIGH VOL STRATEGY WRAPPER =====

def run_bot3_high_vol_strategy(
    idxdf_daily,
    idxdf_1min,
    trade_date,
    gann_levels: Dict[str, float],  # ignore - andar se JSON se lenge
) -> Dict[str, Any]:
    """
    HIGH_VOL (prev day HL/ATR > BOT3_HIGH_VOL_THRESHOLD) ke liye special Bot-3.
    CMP = 9:15 ORB breakout close (bo_close) se JSON ladder fetch hogi.
    """

    ratio = None
    print(
        f"[BOT3] ACTIVE for {trade_date}, prev_ratio={ratio}, "
        f"threshold={BOT3_HIGH_VOL_THRESHOLD}"
    )

    # ---------- 1) 1-min -> 15-min ----------
    idx15 = (
        idxdf_1min
        .resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    # ---------- 2) 15-min ATR(14) ----------
    high_low = idx15["high"] - idx15["low"]
    high_close = (idx15["high"] - idx15["close"].shift()).abs()
    low_close = (idx15["low"] - idx15["close"].shift()).abs()
    tr15 = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr15 = tr15.rolling(window=14).mean()

    atr_series = atr15.dropna()
    atr15_at_915 = float(atr_series.iloc[0]) if not atr_series.empty else 0.0
    print(f"[BOT3-ATR-15M] atr15_at_915={atr15_at_915:.2f}")

    # ---------- 3) 9:15 ORB breakout context ----------
    bo_ctx = build_bot3_breakout_context(idxdf_1min)
    if bo_ctx.get("status") != "OK":
        print(f"[BOT3] No breakout (status={bo_ctx.get('status')}), skipping.")
        return {
            "status": f"SKIP_{bo_ctx.get('status', 'NO_BO')}",
            "regime_ratio": ratio,
            "details": bo_ctx,
        }

    bo_side = bo_ctx["bo_side"]
    bo_close = float(bo_ctx["bo_close"])
    print(
        f"[BOT3-BO] side={bo_side}, bo_time={bo_ctx.get('bo_time')}, "
        f"bo_close(CMP)={bo_close:.2f}"
    )

    # ---------- 4) JSON se CMP-correct Gann levels ----------
    cmp_int = int(round(bo_close))
    cmp_gann_levels = get_gann_row_from_json(cmp_int, midpoint=False)
    print(
        f"[BOT3-GANN-JSON] cmp_key={cmp_int} "
        f"sell_t15={cmp_gann_levels.get('sell_t15')} "
        f"buy_t15={cmp_gann_levels.get('buy_t15')}"
    )

    # ---------- 5) Method A/B detect ----------
    method_info = detect_bot3_method(idxdf_1min, atr15_at_915)
    method = method_info["method"]
    print(
        f"[BOT3-METHOD] range_915={method_info.get('range_915', 0):.2f} "
        f"ratio={method_info.get('ratio_915_atr', 0):.2f} method={method}"
    )

    # ---------- 6) Gann mapping (JSON levels se) ----------
    gann_map = map_bot3_gann_levels(
        bo_side=bo_side,
        gann_levels=cmp_gann_levels,   # << JSON se correct levels
        atr14=atr15_at_915,
        method=method,
    )
    print(
        f"[BOT3-GANN-MAP] method={method} bo_side={bo_side}\n"
        f"  PRIMARY: entry={gann_map.get('primary_entry')} "
        f"sl={gann_map.get('primary_sl')} "
        f"target={gann_map.get('primary_target')}\n"
        f"  OPP:     entry={gann_map.get('opp_entry')} "
        f"sl={gann_map.get('opp_sl')} "
        f"target={gann_map.get('opp_target')}"
    )

    # ---------- 7) bo_ctx mein extra keys add karo A/B ke liye ----------
    bo_ctx["trade_date"] = idxdf_1min.index[0].date()
    bo_ctx["boside"] = bo_side
    bo_ctx["atr15_915"] = atr15_at_915

    # ---------- 8) Method A / B engine ----------
    if method == "A":
        inner = run_bot3_method_a(idxdf_1min, bo_ctx, gann_map)
    else:
        inner = run_bot3_method_b(idx15, bo_ctx, gann_map)

    print(f"[BOT3-INNER] status={inner.get('status')}")

    return {
        "status":       inner.get("status", "BOT3_PENDING"),
        "regime_ratio": ratio,
        "details": {
            "method":       method,
            "bo_ctx":       bo_ctx,
            "atr15_at_915": atr15_at_915,
            "gann_map":     gann_map,
            "inner":        inner,
        },
    }
