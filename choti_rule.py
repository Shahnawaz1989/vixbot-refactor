from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

import pandas as pd


def apply_choti_rule(
    full_idxdf: pd.DataFrame,
    orb_mode: str,
    trigger_side: Optional[str],
    trigger_time: Optional[datetime],
    trigger_price: Optional[float],
    marked_high: float,
    marked_low: float,
    bo15_atr_info: Dict[str, Any],
) -> Tuple[
    bool,          # is_choti_day
    str,           # new_orb_mode ("MORNING" / "MIDDAY")
    Optional[str],  # new_trigger_side
    Optional[datetime],  # new_trigger_time
    Optional[float],     # new_trigger_price
    float,        # new_marked_high
    float,        # new_marked_low
    Optional[bool],  # boside_up_override (None = no override)
]:
    """
    CHOTI rule logic extracted from vix_server.run_v2_orb_gann_backtest_logic.

    - Sirf MORNING ORB par apply hota hai (orb_mode == "MORNING" and trigger_time not None)
    - orb_range = marked_high - marked_low
      bo_range = breakout 15-min candle ka high-low
      choti_ratio = bo_range / orb_range
      choti_ratio > 1.99 => is_choti_day = True
    - CHOTI day:
        * NEW ORB = bo15 candle ka high/low
        * 12:00 tak 15-min close-based breakout search
        * Agar breakout nahi mila -> suggest MIDDAY shift (caller MIDDAY ORB call kare)
        * Agar breakout mila -> naya trigger_side/time/price return
        * Boside flip:
            - BUY trigger -> SELLBO (boside_up=False)
            - SELL trigger -> BUYBO (boside_up=True)
    """
    is_choti_day = False
    boside_up_override: Optional[bool] = None

    if orb_mode != "MORNING" or trigger_time is None:
        # CHOTI only for valid MORNING ORB trigger
        return (
            False,
            orb_mode,
            trigger_side,
            trigger_time,
            trigger_price,
            float(marked_high),
            float(marked_low),
            None,
        )

    # Safe floats
    marked_high = float(marked_high)
    marked_low = float(marked_low)

    orb_range = marked_high - marked_low

    bo_high = bo15_atr_info.get(
        "bo15_high", bo15_atr_info.get("bo15high", 0.0)
    )
    bo_low = bo15_atr_info.get(
        "bo15_low", bo15_atr_info.get("bo15low", 0.0)
    )
    bo_range = bo15_atr_info.get("bo15_range", bo_high - bo_low)

    print(
        "CHOTI-PRE-CHECK",
        "mode", orb_mode,
        "trigger_time", trigger_time,
        "orb_high", marked_high,
        "orb_low", marked_low,
        "orb_range", orb_range,
        "bo_high", bo_high,
        "bo_low", bo_low,
        "bo_range", bo_range,
    )

    if orb_range <= 0 or bo_range <= 0:
        # No meaningful ratio possible
        return (
            False,
            orb_mode,
            trigger_side,
            trigger_time,
            trigger_price,
            marked_high,
            marked_low,
            None,
        )

    choti_ratio = bo_range / orb_range
    is_choti_day = choti_ratio > 1.99

    print(
        "CHOTI-DEBUG",
        "orb_high", marked_high,
        "orb_low", marked_low,
        "orb_range", orb_range,
        "bo_high", bo_high,
        "bo_low", bo_low,
        "bo_range", bo_range,
        "ratio", round(choti_ratio, 2),
        "is_choti", is_choti_day,
    )

    if not is_choti_day:
        # CHOTI not triggered; keep original ORB
        return (
            False,
            orb_mode,
            trigger_side,
            trigger_time,
            trigger_price,
            marked_high,
            marked_low,
            None,
        )

    # CHOTI active: NEW ORB = breakout 15-min candle high/low
    marked_high = float(bo_high)
    marked_low = float(bo_low)

    # NEW BREAKOUT SEARCH: bo15 bucket ke baad se 12:00 tak (15-min close)
    bo15_bucket_start: Optional[datetime] = bo15_atr_info.get("bucket_start")
    trade_date = full_idxdf.index[0].date()

    if bo15_bucket_start is None and trigger_time is not None:
        bo15_bucket_start = trigger_time.replace(
            minute=(trigger_time.minute // 15) * 15,
            second=0,
            microsecond=0,
        )

    new_mark_start = bo15_bucket_start + timedelta(minutes=15)
    new_orb_end = datetime.combine(
        trade_date, datetime.strptime("12:00", "%H:%M").time()
    )

    # 1-min window
    new_trigger_df = full_idxdf[
        (full_idxdf.index > new_mark_start)
        & (full_idxdf.index <= new_orb_end)
    ]

    # 15-min resample for breakout decision
    new_trigger_15m = (
        new_trigger_df.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    new_trigger_time: Optional[datetime] = None
    new_trigger_price: Optional[float] = None
    new_trigger_side: Optional[str] = None

    print(
        "CHOTI-NEW-ORB-WINDOW",
        "start", new_mark_start,
        "end", new_orb_end,
        "marked_high", marked_high,
        "marked_low", marked_low,
    )

    # 15-min CLOSE-based breakout
    for ts, row in new_trigger_15m.iterrows():
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])

        print("CHOTI-NEW-ORB-CHECK-15M", ts, "close", close_price)

        if new_trigger_side is None:
            # BUY BO: close breakout, CMP = 15-min HIGH
            if close_price > marked_high:
                new_trigger_side = "BUY"
                new_trigger_time = ts
                new_trigger_price = high_price  # BUY = HIGH
            # SELL BO: close breakout, CMP = 15-min LOW
            elif close_price < marked_low:
                new_trigger_side = "SELL"
                new_trigger_time = ts
                new_trigger_price = low_price   # SELL = LOW

    if new_trigger_time is None:
        print("CHOTI-NEW-ORB NO BREAKOUT TILL 12:00, SHIFT TO MIDDAY")
        # Caller ko MIDDAY ORB call karna hoga
        return (
            True,
            "MIDDAY",
            None,
            None,
            None,
            marked_high,
            marked_low,
            None,
        )

    print(
        "CHOTI-NEW-ORB SUCCESS",
        "new_high", marked_high,
        "new_low", marked_low,
        "side", new_trigger_side,
        "time", new_trigger_time,
        "price", new_trigger_price,
    )

    # Boside flip rule: BUY trigger -> SELLBO, SELL trigger -> BUYBO
    if new_trigger_side == "BUY":
        boside_up_override = False
    elif new_trigger_side == "SELL":
        boside_up_override = True

    return (
        True,
        orb_mode,
        new_trigger_side,
        new_trigger_time,
        new_trigger_price,
        marked_high,
        marked_low,
        boside_up_override,
    )
