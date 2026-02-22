# orb_rule.py
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from price_rounding import round_index_price_for_side


def detect_orb_atr_ratio(nifty_idxdf: pd.DataFrame, atr_14: float) -> Dict[str, Any]:
    """10:00–10:15 ORB candle high-low / ATR14 ratio."""
    result = {
        "orb_high":    0.0,
        "orb_low":     0.0,
        "orb_range":   0.0,
        "orb_atr":     atr_14,
        "orb_ratio":   0.0,
        "is_high_vol": False,
    }

    if nifty_idxdf.empty or atr_14 <= 0:
        return result

    if not isinstance(nifty_idxdf.index, pd.DatetimeIndex):
        if "time" in nifty_idxdf.columns:
            df = nifty_idxdf.copy()
            df["time"] = pd.to_datetime(df["time"])
            df = df.set_index("time")
        else:
            return result
    else:
        df = nifty_idxdf

    trade_date = df.index[0].date()
    orb_start = datetime.combine(
        trade_date, datetime.strptime("10:00", "%H:%M").time())
    orb_end = datetime.combine(
        trade_date, datetime.strptime("10:15", "%H:%M").time())
    orb_candle = df[(df.index >= orb_start) & (df.index < orb_end)]

    if orb_candle.empty:
        return result

    orb_high = float(orb_candle["high"].max())
    orb_low = float(orb_candle["low"].min())
    orb_range = orb_high - orb_low
    orb_ratio = orb_range / atr_14 if atr_14 > 0 else 0.0
    is_high_vol = orb_ratio >= 1.8

    print("[ORB-ATR-DEBUG]", "orb_high=", orb_high, "orb_low=", orb_low,
          "orb_range=", orb_range, "atr_14=", atr_14,
          "ratio=", round(orb_ratio, 2), "high_vol=", is_high_vol)

    result.update({
        "orb_high":    orb_high,
        "orb_low":     orb_low,
        "orb_range":   orb_range,
        "orb_atr":     atr_14,
        "orb_ratio":   round(orb_ratio, 2),
        "is_high_vol": is_high_vol,
    })
    return result


def detectbreakout15matrratio(
    idx15: pd.DataFrame,
    trigger_time: datetime,
    atr_14: float,
) -> Dict[str, Any]:
    """ORB breakout ke 15-min candle ka high-low / ATR14 ratio."""
    result = {
        "bo15_high":   0.0,
        "bo15_low":    0.0,
        "bo15_range":  0.0,
        "bo15_atr":    atr_14,
        "bo15_ratio":  0.0,
        "is_high_vol": False,
    }

    if idx15.empty or atr_14 <= 0 or trigger_time is None:
        return result

    bucket_start = trigger_time.replace(
        minute=(trigger_time.minute // 15) * 15,
        second=0, microsecond=0,
    )

    print("DEBUG-BO15-trigger_time:", trigger_time)
    print("DEBUG-BO15-bucket_start:", bucket_start)
    print("DEBUG-BO15-bucket_start-in-index?", bucket_start in idx15.index)

    if bucket_start not in idx15.index:
        return result

    row = idx15.loc[bucket_start]
    bo_high = float(row["high"])
    bo_low = float(row["low"])
    bo_range = bo_high - bo_low
    bo_ratio = bo_range / atr_14 if atr_14 > 0 else 0.0
    is_high_vol = bo_ratio >= 1.8

    print("[ORB-15M-BO-ATR-DEBUG]", "bucket_start=", bucket_start,
          "bo15_high=", bo_high, "bo15_low=", bo_low,
          "bo15_range=", bo_range, "atr_14=", atr_14,
          "ratio=", round(bo_ratio, 2), "high_vol=", is_high_vol)

    result.update({
        "bo15_high":   bo_high,
        "bo15_low":    bo_low,
        "bo15_range":  bo_range,
        "bo15_atr":    atr_14,
        "bo15_ratio":  round(bo_ratio, 2),
        "is_high_vol": is_high_vol,
        "bucket_start": bucket_start,
    })
    return result


# ensure import hai


def get_marking_and_trigger(idxdf: pd.DataFrame) -> Dict[str, Any]:
    """
    10:00–10:15 candle HIGH/LOW mark karo.
    10:15–12:30 window me 15-min close-based breakout detect karo.
    BUY BO  -> trigger_price = rounded buy trigger (ceil to next int)
    SELL BO -> trigger_price = rounded sell trigger (floor to int)
    """
    if idxdf.empty:
        return {"status": "error", "message": "Empty index DF"}

    if not isinstance(idxdf.index, pd.DatetimeIndex):
        if "time" in idxdf.columns:
            idxdf = idxdf.copy()
            idxdf["time"] = pd.to_datetime(idxdf["time"])
            idxdf = idxdf.set_index("time")
        else:
            return {"status": "error", "message": "No datetime index/time column"}

    trade_date = idxdf.index[0].date()
    mark_start = datetime.combine(
        trade_date, datetime.strptime("10:00", "%H:%M").time()
    )
    mark_end = datetime.combine(
        trade_date, datetime.strptime("10:15", "%H:%M").time()
    )
    orb_window_end = datetime.combine(
        trade_date, datetime.strptime("12:30", "%H:%M").time()
    )

    marking = idxdf[(idxdf.index >= mark_start) & (idxdf.index < mark_end)]
    if marking.empty:
        return {"status": "error", "message": "No 10:00–10:15 data"}

    marked_high = float(marking["high"].max())
    marked_low = float(marking["low"].min())

    # NIFTY index ke rounded trigger levels
    rounded_buy_trigger = round_index_price_for_side(marked_high, "BUY")
    rounded_sell_trigger = round_index_price_for_side(marked_low, "SELL")

    trigger_df = idxdf[(idxdf.index >= mark_end) &
                       (idxdf.index <= orb_window_end)]
    if trigger_df.empty:
        return {
            "status":      "no_orb_window",
            "message":     "No candles in ORB window",
            "marked_high": marked_high,
            "marked_low":  marked_low,
        }

    agg_dict = {"open": "first", "high": "max", "low": "min", "close": "last"}
    trigger_df15m = trigger_df.resample("15min").agg(agg_dict).dropna()

    for ts, row in trigger_df15m.iterrows():
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        print(
            f"ORB-15M-CHECK {ts} close {close_price} high {high_price} low {low_price}"
        )

        # BUY breakout: close must reach rounded_buy_trigger
        if close_price >= rounded_buy_trigger:
            # trigger_price = breakout candle ka CLOSE
            rounded_trigger = round_index_price_for_side(close_price, "BUY")
            return {
                "status": "ok",
                "trigger_side": "BUY",
                "trigger_time": ts,
                "trigger_price": rounded_trigger,
                "marked_high": marked_high,
                "marked_low": marked_low,
            }

        # SELL breakout: close must reach rounded_sell_trigger
        if close_price <= rounded_sell_trigger:
            # trigger_price = breakout candle ka CLOSE
            rounded_trigger = round_index_price_for_side(close_price, "SELL")
            return {
                "status": "ok",
                "trigger_side": "SELL",
                "trigger_time": ts,
                "trigger_price": rounded_trigger,
                "marked_high": marked_high,
                "marked_low": marked_low,
            }

    return {
        "status":      "no_orb_breakout",
        "message":     "No ORB breakout till 12:30",
        "marked_high": marked_high,
        "marked_low":  marked_low,
    }


def get_midday_entry_start(trade_date) -> datetime:
    """MIDDAY ORB ke liye fixed entry start: 13:30."""
    return datetime.combine(trade_date, datetime.strptime("13:30", "%H:%M").time())
