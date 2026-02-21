from typing import Any, Dict

from vixmodels import VixRequest  # adjust import if needed
from vixutils import cut_dec      # adjust path according to your project


def map_gann_levels_to_v1req(
    v1req: VixRequest,
    levels: Dict[str, float],
    trigger_side: str,
    rule: str,
    half_gap: Dict[str, Any],
    high_vol_orb: bool,
) -> None:
    """
    GANN MAPPING (HALF_GAP / ATR_NORMAL) + HIGH-VOL OPP ENTRY SHIFT.

    - levels: calc_gann_levels_with_excel se aya dict
    - trigger_side: "BUY" / "SELL"
    - rule: "HALF_GAP" / "ATR_NORMAL"
    - half_gap: detect_half_gap ka dict (atr_14 / atr14)
    - high_vol_orb: ORB/BO high-vol flag
    """

    # -------- HIGH-VOL OPP ENTRY SHIFT --------
    if high_vol_orb:
        if trigger_side == "BUY":
            if "sell_t15" in levels:
                levels["sell_entry"] = levels["sell_t15"]
        elif trigger_side == "SELL":
            if "buy_t15" in levels:
                levels["buy_entry"] = levels["buy_t15"]

    # -------- GANN MAPPING (HALF_GAP / ATR_NORMAL) --------
    if rule == "HALF_GAP":
        if trigger_side == "BUY":
            v1req.buy.level = cut_dec(levels["buy_entry"])
            v1req.buy.t4 = cut_dec(levels["buy_t2"])

            v1req.sell.level = cut_dec(levels["sell_entry"])
            v1req.sell.t4 = cut_dec(levels["sell_t15"])
        else:
            v1req.sell.level = cut_dec(levels["sell_entry"])
            v1req.sell.t4 = cut_dec(levels["sell_t2"])

            v1req.buy.level = cut_dec(levels["buy_entry"])
            v1req.buy.t4 = cut_dec(levels["buy_t2"])

        v1req.buy.sl = cut_dec(levels["sell_entry"])
        v1req.sell.sl = cut_dec(levels["buy_entry"])
        return

    # ATR_NORMAL mode
    atr14_local = (
        half_gap.get("atr_14", 0.0)
        or half_gap.get("atr14", 0.0)
        or 0.0
    )

    def pick_buy_t4_from_atr(base_entry: float) -> float:
        if atr14_local <= 0:
            return cut_dec(levels["buy_t4"])
        raw_target = base_entry + 2 * atr14_local
        candidates = [
            levels["buy_t2"],
            levels["buy_t25"],
            levels["buy_t3"],
            levels["buy_t35"],
            levels["buy_t4"],
        ]
        below = [x for x in candidates if x <= raw_target]
        return cut_dec(max(below) if below else max(candidates))

    def pick_sell_t4_from_atr(base_entry: float) -> float:
        if atr14_local <= 0:
            return cut_dec(levels["sell_t4"])
        raw_target = base_entry - 2 * atr14_local
        candidates = [
            levels["sell_t2"],
            levels["sell_t25"],
            levels["sell_t3"],
            levels["sell_t35"],
            levels["sell_t4"],
        ]
        above = [x for x in candidates if x >= raw_target]
        return cut_dec(min(above) if above else min(candidates))

    if trigger_side == "BUY":
        v1req.buy.level = cut_dec(levels["buy_entry"])
        v1req.buy.t2 = cut_dec(levels["buy_t2"])
        v1req.buy.t4 = pick_buy_t4_from_atr(v1req.buy.level)

        v1req.sell.level = cut_dec(levels["sell_entry"])
        v1req.sell.t2 = cut_dec(levels["sell_t2"])
        v1req.sell.t4 = pick_sell_t4_from_atr(v1req.sell.level)
    else:
        v1req.sell.level = cut_dec(levels["sell_entry"])
        v1req.sell.t2 = cut_dec(levels["sell_t2"])
        v1req.sell.t4 = pick_sell_t4_from_atr(v1req.sell.level)

        v1req.buy.level = cut_dec(levels["buy_entry"])
        v1req.buy.t2 = cut_dec(levels["buy_t2"])
        v1req.buy.t4 = pick_buy_t4_from_atr(v1req.buy.level)

    v1req.buy.sl = cut_dec(levels["sell_entry"])
    v1req.sell.sl = cut_dec(levels["buy_entry"])
