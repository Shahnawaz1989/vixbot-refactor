from datetime import datetime, timedelta
from typing import Any, Dict, Literal, Optional, Tuple

import pandas as pd


OrbMode = Literal["MORNING", "MIDDAY"]


def choose_orb_mode_and_info(
    full_idxdf: pd.DataFrame,
    use_midday_orb_only: bool,
    get_marking_and_trigger,
    get_midday_orb_breakout_15min,
) -> Tuple[OrbMode, Dict[str, Any]]:
    """
    Morning vs Midday ORB selection.

    - Pehle get_marking_and_trigger(full_idxdf) call
    - Agar status=="ok" aur use_midday_orb_only False:
        orb_mode = "MORNING"
    - Warna:
        get_midday_orb_breakout_15min(full_idxdf)
        agar woh bhi fail to error dict return karo (orb_mode = "MIDDAY")
    """
    orb_info = get_marking_and_trigger(full_idxdf)

    if orb_info.get("status") == "ok" and not use_midday_orb_only:
        return "MORNING", orb_info

    # MIDDAY fallback
    orb_info = get_midday_orb_breakout_15min(full_idxdf)
    if orb_info.get("status") != "ok":
        # Caller yeh error dict directly return kar sakta hai
        return "MIDDAY", orb_info

    return "MIDDAY", orb_info


def get_entry_start_time_for_orb(
    full_idxdf: pd.DataFrame,
    trigger_time: Optional[datetime],
    orb_mode: OrbMode,
) -> datetime:
    """
    ENTRY WINDOW START TIME:

    - Default (MORNING):
        trigger_time + 15 min
        agar trigger_time None -> 10:15
    - MIDDAY:
        fixed 13:30
    """
    trade_date = full_idxdf.index[0].date()

    if orb_mode == "MIDDAY":
        return datetime.combine(
            trade_date, datetime.strptime("13:30", "%H:%M").time()
        )

    # MORNING
    if trigger_time is not None:
        return trigger_time + timedelta(minutes=15)

    # Fallback 10:15
    return datetime.combine(
        trade_date, datetime.strptime("10:15", "%H:%M").time()
    )


def apply_choti_entry_windows(
    base_idxdf: pd.DataFrame,
    orb_mode: OrbMode,
    is_choti_day: bool,
    trigger_time: Optional[datetime],
    trigger_side: Optional[str],
    entry_start_time: datetime,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    CHOTI day ke liye alag buy/sell entry windows:

    - Sirf MORNING + is_choti_day + trigger_time not None par apply
    - Default: 2h dono
    - BUY trigger:
        buy_window_end  = t + 2h
        sell_window_end = t + 1h15
    - SELL trigger:
        sell_window_end = t + 2h
        buy_window_end  = t + 1h15
    - Otherwise (normal day):
        dono legs = full base_idxdf
    """
    if orb_mode != "MORNING" or not is_choti_day or trigger_time is None:
        # Normal day: full base_idxdf both sides
        return base_idxdf.copy(), base_idxdf.copy()

    timer_start = trigger_time

    # default 2h
    buy_window_end = timer_start + timedelta(hours=2)
    sell_window_end = timer_start + timedelta(hours=2)

    if trigger_side == "BUY":
        buy_window_end = timer_start + timedelta(hours=2)
        sell_window_end = timer_start + timedelta(hours=1, minutes=15)
    elif trigger_side == "SELL":
        sell_window_end = timer_start + timedelta(hours=2)
        buy_window_end = timer_start + timedelta(hours=1, minutes=15)

    idxdf_buy_window = base_idxdf[
        (base_idxdf.index >= entry_start_time)
        & (base_idxdf.index <= buy_window_end)
    ].copy()
    idxdf_sell_window = base_idxdf[
        (base_idxdf.index >= entry_start_time)
        & (base_idxdf.index <= sell_window_end)
    ].copy()

    print(
        "[CHOTI-WINDOW]",
        "trigger_side=", trigger_side,
        "start=", entry_start_time,
        "buy_end=", buy_window_end,
        "sell_end=", sell_window_end,
    )

    return idxdf_buy_window, idxdf_sell_window
