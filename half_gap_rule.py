# half_gap_rule.py
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Dict, Any

from config import NIFTYINDEXTOKEN
from smartapi_helpers import getindex1min


def atr_tradingview_style(df: pd.DataFrame, length: int = 14) -> float:
    df = df.copy()
    df["prev_close"] = df["c"].shift()
    tr1 = df["h"] - df["l"]
    tr2 = (df["h"] - df["prev_close"]).abs()
    tr3 = (df["l"] - df["prev_close"]).abs()
    df["tr"] = np.maximum(tr1, np.maximum(tr2, tr3))

    if df["tr"].count() < length:
        return 0.0

    atr = pd.Series(index=df.index, dtype=float)
    atr.iloc[length - 1] = df["tr"].iloc[:length].mean()
    for i in range(length, len(df)):
        atr.iloc[i] = (atr.iloc[i - 1] * (length - 1) +
                       df["tr"].iloc[i]) / length

    last_atr = atr.iloc[-1]
    return float(round(float(last_atr), 2)) if pd.notna(last_atr) else 0.0


def get_angel_atr_14(api, symbol_token: str, trade_date: str) -> float:
    try:
        start_dt = pd.to_datetime(trade_date) - pd.Timedelta(days=7)
        from_date = start_dt.strftime("%Y-%m-%d") + " 09:15"
        to_date = f"{trade_date} 09:30"

        hist = api.getCandleData({
            "exchange":    "NSE",
            "symboltoken": symbol_token,
            "interval":    "FIFTEEN_MINUTE",
            "fromdate":    from_date,
            "todate":      to_date,
        })

        if hist.get("status") != True or not hist.get("data"):
            print("[ATR-15M-HIST-DEBUG] status=", hist.get("status"),
                  "rows=", len(hist.get("data") or []))
            return 0.0

        df = pd.DataFrame(hist["data"], columns=[
                          "ts", "o", "h", "l", "c", "v"])
        df["ts"] = pd.to_datetime(df["ts"])
        df[["o", "h", "l", "c"]] = df[["o", "h", "l", "c"]].astype(float)
        df = df.sort_values("ts")

        cutoff_naive = pd.to_datetime(f"{trade_date} 09:30")
        cutoff = (cutoff_naive.tz_localize(df["ts"].dt.tz)
                  if df["ts"].dt.tz is not None else cutoff_naive)
        df = df[df["ts"] <= cutoff]

        print("[ATR-15M-ROWS] rows=", len(df))
        if len(df) < 14:
            return 0.0

        atr_14 = atr_tradingview_style(df[["h", "l", "c"]], length=14)
        print("[ATR-15M-VALUE] trade_date=", trade_date, "ATR14_15m=", atr_14)
        return atr_14

    except Exception as e:
        print("[ATR-15M-ERROR]", e)
        return 0.0


def detect_half_gap(api, nifty_idxdf: pd.DataFrame, trade_date: str) -> Dict[str, Any]:
    base = {
        "half_gap_type": "NO_HALF_GAP",
        "daily_open":    0.0,
        "prev_close":    0.0,
        "atr_14":        0.0,
        "gap_diff":      0.0,
        "gap_atr":       0.0,
        "gap_pct":       0.0,
        "use_t2_only":   False,
    }

    if nifty_idxdf.empty:
        base["half_gap_type"] = "NO_DATA"
        return base

    trade_date_obj = nifty_idxdf.index[0].date()
    first_start = datetime.combine(
        trade_date_obj, datetime.strptime("09:15", "%H:%M").time())
    first_end = datetime.combine(
        trade_date_obj, datetime.strptime("09:16", "%H:%M").time())
    first_candle = nifty_idxdf[
        (nifty_idxdf.index >= first_start) & (nifty_idxdf.index < first_end)
    ]

    if first_candle.empty:
        base["half_gap_type"] = "NO_OPEN"
        return base

    daily_open = float(first_candle["open"].iloc[0])
    base["daily_open"] = daily_open

    atr_14 = get_angel_atr_14(api, NIFTYINDEXTOKEN, trade_date)
    print("[ATR-15M-DEBUG] trade_date=", trade_date, "ATR14_15m=", atr_14)
    base["atr_14"] = atr_14

    if atr_14 == 0:
        base["half_gap_type"] = "NO_ATR"
        return base

    prev_date = (pd.to_datetime(trade_date) -
                 timedelta(days=1)).strftime("%Y-%m-%d")
    prev_df = getindex1min(api, prev_date, NIFTYINDEXTOKEN)
    prev_close = float(prev_df["close"].iloc[-1]
                       ) if not prev_df.empty else daily_open
    base["prev_close"] = prev_close

    gap_diff = daily_open - prev_close
    gap_atr = gap_diff / atr_14 if atr_14 > 0 else 0.0
    gap_pct = abs(gap_atr)

    half_type = "NO_HALF_GAP"
    if gap_atr > 2.10:
        half_type = "HALF_GAP_UP"
    elif gap_atr < -2.10:
        half_type = "HALF_GAP_DOWN"

    base.update({
        "half_gap_type": half_type,
        "gap_diff":      gap_diff,
        "gap_atr":       round(gap_atr, 2),
        "gap_pct":       round(gap_pct, 2),
        "use_t2_only":   gap_pct > 2.10,
        "is_half_gap":   half_type in ("HALF_GAP_UP", "HALF_GAP_DOWN"),
    })
    return base


def detect_hook_930_exact(api, trade_date: str) -> Dict[str, Any]:
    """
    9:30 Hook Detection (cross/touch logic):
    - Gap DOWN: today 9:15-9:30 HIGH >= prev last candle LOW  → HOOKED
    - Gap UP:   today 9:15-9:30 LOW  <= prev last candle HIGH → HOOKED
    - 10-day lookback for prev day (weekend/holiday handle)
    """

    # ---------- Prev day last candle (10-day lookback) ----------
    trade_dt = datetime.strptime(trade_date, "%Y-%m-%d")
    prev_day = trade_dt - timedelta(days=1)
    max_lookback = 10

    prev_last = None
    prev_found_date = None

    for _ in range(max_lookback):
        fromdt = prev_day.replace(hour=9, minute=15)
        todt = prev_day.replace(hour=15, minute=30)

        payload = {
            "exchange": "NSE",
            "symboltoken": NIFTYINDEXTOKEN,
            "interval": "ONE_MINUTE",
            "fromdate": fromdt.strftime("%Y-%m-%d %H:%M"),
            "todate":   prev_day.replace(hour=15, minute=31).strftime("%Y-%m-%d %H:%M"),
        }

        hist = api.getCandleData(payload)
        if hist.get("status") and hist.get("data"):
            df = pd.DataFrame(
                hist["data"],
                columns=["time", "open", "high", "low", "close", "volume"],
            )
            df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
            df = df.set_index("time")

            # Last 15-min candle (15:15–15:30)
            df15 = df.resample("15min").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last"}
            ).dropna()

            prev_last = df15.iloc[-1]
            prev_found_date = prev_day.date()
            print(
                f"[HOOK] Prev day found: {prev_found_date} last 15min candle time={prev_last.name}")
            break

    prev_close = float(prev_last["close"])
    prev_high = float(prev_last["high"])
    prev_low = float(prev_last["low"])

    # ---------- Today 9:15–9:30 ----------
    today_df = getindex1min(api, trade_date, NIFTYINDEXTOKEN)
    if today_df.empty:
        print("[HOOK] NO_TODAY_DATA – treating as HOOKED")
        return {
            "hook_status": "NO_TODAY_DATA",
            "is_hooked": True,
            "breakout_level": prev_close,
        }

    t_915 = pd.Timestamp(f"{trade_date} 09:15")
    t_930 = pd.Timestamp(f"{trade_date} 09:30")
    today_15m = today_df[(today_df.index >= t_915) & (today_df.index < t_930)]

    if today_15m.empty:
        print("[HOOK] NO_915_930 – treating as HOOKED")
        return {
            "hook_status": "NO_915_930",
            "is_hooked": True,
            "breakout_level": prev_close,
        }

    today_open = float(today_15m["open"].iloc[0])
    today_high = float(today_15m["high"].max())
    today_low = float(today_15m["low"].min())

    # ---------- Gap direction ----------
    if today_open > prev_close:
        gap_direction = "GAP_UP"
    else:
        gap_direction = "GAP_DOWN"

    # ---------- Hook cross/touch logic ----------
    if gap_direction == "GAP_DOWN":
        # today HIGH ne prev LOW ko touch/cross kiya?
        ref_prev = prev_low
        ref_today = today_high
        is_hooked = today_high >= prev_low

    else:  # GAP_UP
        # today LOW ne prev HIGH ko touch/cross kiya?
        ref_prev = prev_high
        ref_today = today_low
        is_hooked = today_low <= prev_high

    distance = abs(ref_prev - ref_today)
    hook_status = "HOOKED" if is_hooked else "UNHOOKED"

    print(
        f"[HOOK] 9:30 CHECK gap_dir={gap_direction} "
        f"prev_ref={ref_prev} today_ref={ref_today} "
        f"dist={distance:.2f} status={hook_status}"
    )

    return {
        "hook_status": hook_status,
        "gap_direction": gap_direction,
        "prev_ref": ref_prev,
        "today_ref": ref_today,
        "distance_rs": round(distance, 2),
        "is_hooked": is_hooked,
        "breakout_level": ref_prev,
        "prev_close": prev_close,
        "prev_high": prev_high,
        "prev_low": prev_low,
        "today_open": today_open,
    }
