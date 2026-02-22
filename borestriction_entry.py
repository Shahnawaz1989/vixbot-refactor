from datetime import datetime, time
from typing import Any, Dict, Tuple

import pandas as pd

from config import DEFAULTBOSTART
from strategy import applyborestriction, findentryidx


def apply_default_bo_start_filter(idxdf: pd.DataFrame) -> pd.DataFrame:
    """
    DEFAULTBOSTART filter:

    - DEFAULTBOSTART (time) ke baad se hi entry candles allow
    """
    if idxdf.empty:
        return idxdf

    trade_date = idxdf.index[0].date()
    bot: time = DEFAULTBOSTART
    starttime = datetime.combine(trade_date, bot)
    return idxdf[idxdf.index >= starttime].copy()


def find_entry_candles_for_both_legs(
    idxdf_buy_window: pd.DataFrame,
    idxdf_sell_window: pd.DataFrame,
    v1req: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    BUYBO / SELLBO dono legs ke liye:

    - applyborestriction -> restricted idxdf
    - findentryidx -> entry candle row

    Returns:
        (buyentrycandle, sellentrycandle) as dict-like (or None)
    """
    # BUY side
    idxdfbuy = applyborestriction(
        idxdf_buy_window, v1req, legside="BUYBO"
    )
    buyentrycandle = findentryidx(idxdfbuy, v1req.buy.level)

    # SELL side
    idxdfsell = applyborestriction(
        idxdf_sell_window, v1req, legside="SELLBO"
    )
    sellentrycandle = findentryidx(idxdfsell, v1req.sell.level)

    return buyentrycandle, sellentrycandle
