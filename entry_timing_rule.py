from datetime import datetime, timedelta
from typing import Literal, Optional

import pandas as pd


OrbMode = Literal["MORNING", "MIDDAY"]


def get_entry_start_time(
    full_idxdf: pd.DataFrame,
    trigger_time: Optional[datetime],
    orb_mode: OrbMode,
) -> datetime:
    """
    ENTRY WINDOW START TIME

    Default (MORNING):
        - trigger_time + 15 min
        - Agar trigger_time None -> 10:15
    MIDDAY:
        - fixed 13:30
    """
    if full_idxdf.empty:
        # Fallback: today 10:15, but ideally caller ka df non-empty hoga
        trade_date = datetime.now().date()
    else:
        trade_date = full_idxdf.index[0].date()

    if orb_mode == "MIDDAY":
        return datetime.combine(
            trade_date, datetime.strptime("13:30", "%H:%M").time()
        )

    if trigger_time is not None:
        return trigger_time + timedelta(minutes=15)

    # Fallback 10:15 if no trigger_time
    return datetime.combine(
        trade_date, datetime.strptime("10:15", "%H:%M").time()
    )
