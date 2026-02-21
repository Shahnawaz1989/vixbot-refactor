# strategy.py

from datetime import datetime, timedelta
from typing import Dict, Any, Optional

import pandas as pd

from smartapi_helpers import getoptioncloseat
from models import VixRequest


# ------------ BO RESTRICTION (NEW) ------------

def applyborestriction(
    idxdf: pd.DataFrame,
    req: VixRequest,
    legside: str = "BUYBO",
) -> pd.DataFrame:
    """
    BO restriction:
    - req.borestrictside == legside ho
    - borestrictuntil time set ho (HHMM)
    To us side ke liye borestrictuntil se pehle ke candles skip kar do.
    """
    side = (req.borestrictside or "").upper()
    until = req.borestrictuntil

    if not side or side != legside.upper() or not until:
        return idxdf

    try:
        t = datetime.strptime(until.strip(), "%H%M").time()
    except ValueError:
        return idxdf

    date0 = idxdf.index[0].date()
    cut = datetime.combine(date0, t)
    return idxdf[idxdf.index >= cut]


# ------------ COMMON HELPERS ------------

def findentryidx(df: pd.DataFrame, level: float) -> Optional[pd.Series]:
    for _, row in df.iterrows():
        if row["low"] <= level <= row["high"]:
            return row
    return None


def calc_pnl(entryopt: float, exitopt: float, direction: str, lots: int = 1) -> float:
    """
    Hum sirf BUY trades le rahe (CE/PE dono).
    NIFTY 1 lot = 65 quantity.
    P&L = (exit - entry) * 65 * lots
    direction ignore kar rahe, sirf sign BUY jaisa rakhenge.
    """
    if lots <= 0:
        lots = 1
    qty = 65 * lots
    return (exitopt - entryopt) * qty


# ------------ SINGLE EXIT ENGINE (OPTIONS) ------------

def processnormal(
    idxdf: pd.DataFrame,
    optdf: pd.DataFrame,
    entrytime: datetime,
    level: float,
    t4: float,
    sl: float,
    direction: str,
    lots: int = 1,
    is_half_gap: bool = False,
    half_gap_type: Optional[str] = None,
    rule: str = "ATR_NORMAL",  # "HALF_GAP" / "ORB_LATE" / "ATR_NORMAL"
) -> Dict[str, Any]:
    """
    Single unified exit engine:
    - HALF_GAP  : fixed scalp to t4 (M8/N8 etc.)
    - ORB_LATE  : MORNING ORB late entry, fixed T2 (J8/M8) as t4
    - ATR_NORMAL: plain Gann T4 (ya baad me ATR-based adjust)
    """
    entryopt = getoptioncloseat(optdf, entrytime)
    if not entryopt:
        return {"status": "NO_OPT_ENTRY"}

    print(
        f"[DEBUG] PROCESSNORMAL ENTRY dir={direction} entrytime={entrytime} "
        f"level={level} t4={t4} sl={sl} lots={lots} entryopt={entryopt} "
        f"is_half_gap={is_half_gap} half_gap_type={half_gap_type} rule={rule}"
    )

    eodexit = datetime.combine(
        entrytime.date(), datetime.strptime("15:00", "%H:%M").time()
    )
    afterentry = idxdf[idxdf.index >= entrytime]

    for _, row in afterentry.iterrows():
        ts = row.name

        if direction == "BUY":
            # BUY side: abhi sab rules normal T4/SL hi use karte hain
            if sl > 0 and row["low"] <= sl:
                exitopt = getoptioncloseat(optdf, ts)
                pnl = calc_pnl(entryopt, exitopt, "BUY", lots) if exitopt else 0.0
                return {
                    "status": "SL",
                    "entry": entryopt,
                    "exit": exitopt,
                    "exittime": ts,
                    "exitindex": row["close"],
                    "pnl": pnl,
                }
            if t4 > 0 and row["high"] >= t4:
                print(
                    f"[DEBUG] T4 HIT BUY ts={ts} high={row['high']} "
                    f"t4={t4} close={row['close']}"
                )
                exitopt = getoptioncloseat(optdf, ts)
                pnl = calc_pnl(entryopt, exitopt, "BUY", lots) if exitopt else 0.0
                label = "T4"
                if rule == "ORB_LATE":
                    label = "ORB_LATE_T"
                elif rule == "HALF_GAP":
                    label = "HALF_GAP_T"
                elif rule == "ATR_NORMAL":
                    label = "ATR_T4"
                return {
                    "status": label,
                    "entry": entryopt,
                    "exit": exitopt,
                    "exittime": ts,
                    "exitindex": row["close"],
                    "pnl": pnl,
                }

        else:
            # SELL side: rule-specific handling
            # HALF-GAP scalp: fixed target exit at t4 (N8/M8 level)
            if rule == "HALF_GAP" and t4 > 0 and row["low"] <= t4:
                exitopt = getoptioncloseat(optdf, ts)
                pnl = calc_pnl(entryopt, exitopt, "SELL", lots) if exitopt else 0.0
                return {
                    "status": "HALF_GAP_T",
                    "entry": entryopt,
                    "exit": exitopt,
                    "exittime": ts,
                    "exitindex": row["close"],
                    "pnl": pnl,
                }

            # MORNING ORB LATE: fixed T2 (J8/M8) as t4
            if rule == "ORB_LATE" and t4 > 0 and row["low"] <= t4:
                exitopt = getoptioncloseat(optdf, ts)
                pnl = calc_pnl(entryopt, exitopt, "SELL", lots) if exitopt else 0.0
                return {
                    "status": "ORB_LATE_T",
                    "entry": entryopt,
                    "exit": exitopt,
                    "exittime": ts,
                    "exitindex": row["close"],
                    "pnl": pnl,
                }

            # ATR_NORMAL or fallback: normal SL + T4
            if sl > 0 and row["high"] >= sl:
                exitopt = getoptioncloseat(optdf, ts)
                pnl = calc_pnl(entryopt, exitopt, "SELL", lots) if exitopt else 0.0
                return {
                    "status": "SL",
                    "entry": entryopt,
                    "exit": exitopt,
                    "exittime": ts,
                    "exitindex": row["close"],
                    "pnl": pnl,
                }
            if t4 > 0 and row["low"] <= t4:
                print(
                    f"[DEBUG] T4 HIT SELL ts={ts} low={row['low']} "
                    f"t4={t4} close={row['close']}"
                )
                exitopt = getoptioncloseat(optdf, ts)
                pnl = calc_pnl(entryopt, exitopt, "SELL", lots) if exitopt else 0.0
                label = "T4"
                if rule == "ATR_NORMAL":
                    label = "ATR_T4"
                return {
                    "status": label,
                    "entry": entryopt,
                    "exit": exitopt,
                    "exittime": ts,
                    "exitindex": row["close"],
                    "pnl": pnl,
                }

        # EOD exit
        if ts >= eodexit:
            exitopt = getoptioncloseat(optdf, ts)
            pnl = calc_pnl(entryopt, exitopt, direction, lots) if exitopt else 0.0
            return {
                "status": "EOD_1500",
                "entry": entryopt,
                "exit": exitopt,
                "exittime": ts,
                "exitindex": row["close"],
                "pnl": pnl,
            }

    return {"status": "OPEN", "entry": entryopt, "exit": None, "pnl": 0.0}
