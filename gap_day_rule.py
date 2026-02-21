# gap_day_rule.py
import pandas as pd
from datetime import datetime
from typing import Dict, Any


def detect_gap_day(
    idxdf: pd.DataFrame,
    prev_high: float,
    prev_low: float,
) -> Dict[str, Any]:
    if idxdf.empty:
        return {"gap_type": "NO_DATA"}

    if not isinstance(idxdf.index, pd.DatetimeIndex):
        if "time" in idxdf.columns:
            idxdf = idxdf.copy()
            idxdf["time"] = pd.to_datetime(idxdf["time"])
            idxdf = idxdf.set_index("time")
        else:
            return {"gap_type": "NO_TIME_COLUMN"}

    trade_date = idxdf.index[0].date()
    start = datetime.combine(
        trade_date, datetime.strptime("09:15", "%H:%M").time())
    end = datetime.combine(
        trade_date, datetime.strptime("09:30", "%H:%M").time())

    first_15 = idxdf[(idxdf.index >= start) & (idxdf.index < end)]
    if first_15.empty:
        return {"gap_type": "NO_FIRST_15"}

    first_low = float(first_15["low"].min())
    first_high = float(first_15["high"].max())
    prev_high = float(prev_high)
    prev_low = float(prev_low)

    gap_type = "NO_GAP"
    if first_low > prev_high:
        gap_type = "GAP_UP"
    elif first_high < prev_low:
        gap_type = "GAP_DOWN"

    return {
        "gap_type":   gap_type,
        "first_low":  first_low,
        "first_high": first_high,
        "prev_high":  prev_high,
        "prev_low":   prev_low,
    }
