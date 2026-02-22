# choti_day_rule.py
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from smartapi_helpers import get_midday_orb_breakout_15min


def detect_choti_day(
    bo15_atr_info: Dict[str, Any],
    marked_high: float,
    marked_low: float,
    orb_mode: str,
    trigger_time: Optional[datetime],
) -> bool:
    """
    CHOTI DAY: BO 15-min candle range / ORB range > 1.99
    Sirf MORNING mode me check hoga.
    """
    if orb_mode != "MORNING" or trigger_time is None:
        return False

    orb_range = marked_high - marked_low
    bo_high = bo15_atr_info.get(
        "bo15_high", bo15_atr_info.get("bo15high", 0.0))
    bo_low = bo15_atr_info.get("bo15_low",  bo15_atr_info.get("bo15low",  0.0))
    bo_range = bo15_atr_info.get("bo15_range", bo_high - bo_low)

    if orb_range <= 0 or bo_range <= 0:
        return False

    choti_ratio = bo_range / orb_range
    is_choti = choti_ratio > 1.99

    print("CHOTI-DEBUG",
          "orb_high", marked_high, "orb_low", marked_low, "orb_range", orb_range,
          "bo_high",  bo_high,     "bo_low",  bo_low,     "bo_range",  bo_range,
          "ratio",    round(choti_ratio, 2), "is_choti", is_choti)

    return is_choti


def run_choti_new_orb(
    full_idxdf: pd.DataFrame,
    bo15_atr_info: Dict[str, Any],
    trigger_time: datetime,
    trigger_side: str,
) -> Dict[str, Any]:
    """
    CHOTI day me:
    1. New ORB = breakout 15-min candle HIGH/LOW
    2. New breakout search: bo15 bucket ke baad se 12:00 tak (15-min close)
    Returns dict with new trigger info ya fallback MIDDAY.
    """
    trade_date = full_idxdf.index[0].date()

    # New ORB high/low = breakout 15-min candle
    new_marked_high = bo15_atr_info.get("bo15_high", 0.0)
    new_marked_low = bo15_atr_info.get("bo15_low",  0.0)

    bo15_bucket_start = bo15_atr_info.get("bucket_start")
    if bo15_bucket_start is None and trigger_time is not None:
        bo15_bucket_start = trigger_time.replace(
            minute=(trigger_time.minute // 15) * 15,
            second=0, microsecond=0,
        )

    new_mark_start = bo15_bucket_start + timedelta(minutes=15)
    new_orb_end = datetime.combine(
        trade_date, datetime.strptime("12:00", "%H:%M").time())

    new_trigger_df = full_idxdf[
        (full_idxdf.index > new_mark_start) & (full_idxdf.index <= new_orb_end)
    ]
    new_trigger_15m = (
        new_trigger_df.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    print("CHOTI-NEW-ORB-WINDOW",
          "start", new_mark_start, "end", new_orb_end,
          "marked_high", new_marked_high, "marked_low", new_marked_low)

    new_trigger_time:  Optional[datetime] = None
    new_trigger_price: Optional[float] = None
    new_trigger_side:  Optional[str] = None

    for ts, row in new_trigger_15m.iterrows():
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        print("CHOTI-NEW-ORB-CHECK-15M", ts, "close", close_price)

        if new_trigger_side is None:
            if close_price > new_marked_high:
                new_trigger_side = "BUY"
                new_trigger_time = ts
                new_trigger_price = high_price
            elif close_price < new_marked_low:
                new_trigger_side = "SELL"
                new_trigger_time = ts
                new_trigger_price = low_price

        if new_trigger_time is None:
            # MIDDAY fallback
            print("CHOTI-NEW-ORB NO BREAKOUT TILL 12:00, SHIFT TO MIDDAY")
            orb_info = get_midday_orb_breakout_15min(full_idxdf)
            if orb_info.get("status") != "ok":
                return {"status": "CHOTI-MIDDAY-FAIL", "message": "No MIDDAY ORB after CHOTI"}
            return {
                "status":        "MIDDAY_FALLBACK",
                "orb_mode":      "MIDDAY",
                "trigger_side":  orb_info["trigger_side"],
                "trigger_time":  orb_info["trigger_time"],
                "trigger_price": orb_info["trigger_price"],
                "marked_high":   orb_info["marked_high"],
                "marked_low":    orb_info["marked_low"],
            }

        print(
            "CHOTI-NEW-ORB SUCCESS",
            "new_high", new_marked_high,
            "new_low", new_marked_low,
            "side", new_trigger_side,
            "time", new_trigger_time,
            "price", new_trigger_price,
        )

        return {
            "status":        "OK",
            "orb_mode":      "MORNING",
            "trigger_side":  new_trigger_side,
            "trigger_time":  new_trigger_time,
            "trigger_price": new_trigger_price,
            "marked_high":   new_marked_high,
            "marked_low":    new_marked_low,
        }
