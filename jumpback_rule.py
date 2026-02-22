# jumpback_rule.py
from typing import Dict, Optional
import pandas as pd


print("jumpback_rule loaded OK")
print("orb_rule loaded, ORB 10:00–10:15 version active")


def decide_orb_or_jumpback(
    full_idxdf: pd.DataFrame,
    prev_high: float,
    prev_low: float,
    orb_break_time,   # datetime
) -> Dict:
    df_till_orb = full_idxdf[full_idxdf.index <= orb_break_time]

    prev_break_flag_at_orb = False
    for _, row in df_till_orb.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        if high >= prev_high or low <= prev_low:
            prev_break_flag_at_orb = True
            break

    if prev_break_flag_at_orb:
        return {
            "mode": "MAIN_ORB",
            "reason": "Prev day HL already broken before ORB breakout",
            "jump_break_time": None,
            "jump_break_dir": None,
            "jump_orb_high": None,
            "jump_orb_low": None,
        }

    first_candle = full_idxdf.iloc[0]
    jump_orb_high = float(first_candle["high"])
    jump_orb_low = float(first_candle["low"])

    jump_break_time: Optional[pd.Timestamp] = None
    jump_break_dir: Optional[str] = None

    for _, row in full_idxdf.iterrows():
        ts = row.name
        close = float(row["close"])

        if close > jump_orb_high:
            jump_break_time = ts
            jump_break_dir = "UP"
            break
        if close < jump_orb_low:
            jump_break_time = ts
            jump_break_dir = "DOWN"
            break

    return {
        "mode": "JUMP_BACK",
        "reason": "Prev day HL NOT broken till ORB breakout, using 9:15 ORB",
        "jump_break_time": jump_break_time,
        "jump_break_dir": jump_break_dir,
        "jump_orb_high": jump_orb_high,
        "jump_orb_low": jump_orb_low,
    }
