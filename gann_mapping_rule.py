from typing import Any, Dict

from models import VixRequest
from gann_engine import cut_dec


def map_gann_levels_to_v1req(
    v1req: VixRequest,
    levels: Dict[str, float],
    trigger_side: str,
    rule: str,
    half_gap: Dict[str, Any],
    high_vol_orb: bool,
) -> None:
    """
    Central GANN mapping:

    - Primary entry always *_t15 (BUY → buy_t15, SELL → sell_t15)
    - Opp entry default *_entry
    - HIGH-VOL ORB: sirf opp entry t15 pe shift (10AM/MIDDAY bots ke liye)
    - SL: BUY SL = current SELL entry, SELL SL = current BUY entry
    - HALF_GAP: fixed target rules
    - ATR_NORMAL: ATR-based T4
    """

    ts = (trigger_side or "").upper()
    rl = (rule or "").upper()

    # -------- Base entries --------
    buy_primary_entry = levels.get("buy_t15", levels.get("buy_entry", 0.0))
    sell_primary_entry = levels.get("sell_t15", levels.get("sell_entry", 0.0))

    buy_opp_entry = levels.get("buy_entry", 0.0)
    sell_opp_entry = levels.get("sell_entry", 0.0)

    # -------- HIGH-VOL OPP ENTRY SHIFT (sirf opp leg) --------
    if high_vol_orb:
        if ts == "BUY":
            # Primary BUY, opp SELL from sell_t15
            sell_opp_entry = levels.get("sell_t15", sell_opp_entry)
        elif ts == "SELL":
            # Primary SELL, opp BUY from buy_t15
            buy_opp_entry = levels.get("buy_t15", buy_opp_entry)

    # -------- HALF_GAP RULE --------
    if rl == "HALF_GAP":
        if ts == "BUY":
            # Primary BUY from buy_t15
            v1req.buy.level = cut_dec(buy_primary_entry)
            v1req.buy.t4 = cut_dec(levels.get("buy_t2", 0.0))

            # Opp SELL from sell_entry / sell_opp_entry
            v1req.sell.level = cut_dec(sell_opp_entry)
            v1req.sell.t4 = cut_dec(levels.get("sell_t15", 0.0))
        else:  # SELL trigger
            # Primary SELL from sell_t15
            v1req.sell.level = cut_dec(sell_primary_entry)
            v1req.sell.t4 = cut_dec(levels.get("sell_t2", 0.0))

            # Opp BUY from buy_entry / buy_opp_entry
            v1req.buy.level = cut_dec(buy_opp_entry)
            v1req.buy.t4 = cut_dec(levels.get("buy_t15", 0.0))

        # SL: BUY SL = current SELL entry, SELL SL = current BUY entry
        v1req.buy.sl = cut_dec(v1req.sell.level or 0.0)
        v1req.sell.sl = cut_dec(v1req.buy.level or 0.0)
        return

    # -------- ATR_NORMAL MODE --------
    atr14_local = (
        half_gap.get("atr_14", 0.0)
        or half_gap.get("atr14", 0.0)
        or 0.0
    )

    def pick_buy_t4_from_atr(base_entry: float) -> float:
        if atr14_local <= 0:
            return cut_dec(levels.get("buy_t4", 0.0))
        raw_target = base_entry + 2 * atr14_local
        candidates = [
            levels.get("buy_t2", 0.0),
            levels.get("buy_t25", 0.0),
            levels.get("buy_t3", 0.0),
            levels.get("buy_t35", 0.0),
            levels.get("buy_t4", 0.0),
        ]
        below = [x for x in candidates if x <= raw_target]
        return cut_dec(max(below) if below else max(candidates))

    def pick_sell_t4_from_atr(base_entry: float) -> float:
        if atr14_local <= 0:
            return cut_dec(levels.get("sell_t4", 0.0))
        raw_target = base_entry - 2 * atr14_local
        candidates = [
            levels.get("sell_t2", 0.0),
            levels.get("sell_t25", 0.0),
            levels.get("sell_t3", 0.0),
            levels.get("sell_t35", 0.0),
            levels.get("sell_t4", 0.0),
        ]
        above = [x for x in candidates if x >= raw_target]
        return cut_dec(min(above) if above else min(candidates))

    if ts == "BUY":
        # Primary BUY from buy_t15
        v1req.buy.level = cut_dec(buy_primary_entry)
        v1req.buy.t2 = cut_dec(levels.get("buy_t2", 0.0))
        v1req.buy.t4 = pick_buy_t4_from_atr(v1req.buy.level)

        # Opp SELL from sell_opp_entry
        v1req.sell.level = cut_dec(sell_opp_entry)
        v1req.sell.t2 = cut_dec(levels.get("sell_t2", 0.0))
        v1req.sell.t4 = pick_sell_t4_from_atr(v1req.sell.level)
    else:  # SELL trigger
        # Primary SELL from sell_t15
        v1req.sell.level = cut_dec(sell_primary_entry)
        v1req.sell.t2 = cut_dec(levels.get("sell_t2", 0.0))
        v1req.sell.t4 = pick_sell_t4_from_atr(v1req.sell.level)

        # Opp BUY from buy_opp_entry
        v1req.buy.level = cut_dec(buy_opp_entry)
        v1req.buy.t2 = cut_dec(levels.get("buy_t2", 0.0))
        v1req.buy.t4 = pick_buy_t4_from_atr(v1req.buy.level)

    # SL: BUY SL = current SELL entry, SELL SL = current BUY entry
    v1req.buy.sl = cut_dec(v1req.sell.level or 0.0)
    v1req.sell.sl = cut_dec(v1req.buy.level or 0.0)
