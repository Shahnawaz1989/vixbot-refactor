from datetime import datetime
from typing import Any, Dict

import pandas as pd


def check_breakout(
    idx_df: pd.DataFrame,
    prev_high: float,
    prev_low: float,
) -> Dict[str, Any]:
    """
    Prev day high/low breakout on given timeframe (1-min ya 15-min).

    Returns:
        {
          "breakout": True/False,
          "breakouttype": "UP" / "DOWN" / None,
          "breakouttime": datetime | None,
          "breakoutprice": float | None,
        }
    """
    if idx_df.empty:
        return {
            "breakout": False,
            "breakouttype": None,
            "breakouttime": None,
            "breakoutprice": None,
        }

    df = idx_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"])
            df = df.set_index("time")

    bo_flag = False
    bo_type = None
    bo_time = None
    bo_price = None

    # Close-based breakout
    for ts, row in df.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        # UP breakout
        if close > prev_high:
            bo_flag = True
            bo_type = "UP"
            bo_time = ts
            bo_price = high
            break

        # DOWN breakout
        if close < prev_low:
            bo_flag = True
            bo_type = "DOWN"
            bo_time = ts
            bo_price = low
            break

    return {
        "breakout": bo_flag,
        "breakouttype": bo_type,
        "breakouttime": bo_time,
        "breakoutprice": bo_price,
    }


def get_prev_day_hl_breakout_till_1330(
    full_idxdf: pd.DataFrame,
    prev_high: float,
    prev_low: float,
) -> Dict[str, Any]:
    """
    Prev day H/L breakout till 13:30 on 1-min data.

    - 9:15 se 13:30 tak ka subset lo
    - check_breakout use karo
    """
    if full_idxdf.empty:
        return {
            "prev_break_flag_1330": False,
            "prev_break_till_1330": None,
        }

    df = full_idxdf.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "time" in df.columns:
            df["time"] = pd.to_datetime(df["time"])
            df = df.set_index("time")

    trade_date = df.index[0].date()
    cutoff_1330 = datetime.combine(
        trade_date, datetime.strptime("13:30", "%H:%M").time()
    )
    idx_till_1330 = df[df.index <= cutoff_1330]

    breakout_result = check_breakout(idx_till_1330, prev_high, prev_low)
    prev_break_flag_1330 = bool(breakout_result.get("breakout"))

    return {
        "prev_break_flag_1330": prev_break_flag_1330,
        "prev_break_till_1330": breakout_result,
    }
