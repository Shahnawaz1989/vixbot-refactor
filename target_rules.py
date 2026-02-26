from typing import Dict, Tuple
from datetime import datetime
from dataclasses import dataclass


@dataclass
class ATRMultiplierRule:
    engine: str
    is_hooked: bool
    atr_threshold: float
    multiplier_low: float
    multiplier_high: float


# All ATR multiplier rules
MULTIPLIER_RULES: Dict[str, ATRMultiplierRule] = {
    # Midday ORB (any hook status)
    "MIDDAY": ATRMultiplierRule("MIDDAY", False, 40.0, 2.0, 1.5),

    # UNHOOKED Main Branch (10ORB, 915ORB, CHOTI, HIGH_VOL, NORMAL)
    "UNHOOK_MAIN": ATRMultiplierRule("UNHOOK_MAIN", False, 50.0, 2.0, 1.5),

    # HOOKED Main Branch (same engines)
    "HOOK_MAIN": ATRMultiplierRule("HOOK_MAIN", True, 55.0, 2.0, 1.5),
}


def get_atr_multiplier(engine: str, is_hooked: bool, atr_value: float) -> float:
    """
    Engine + hook status se ATR multiplier return karta hai.

    Args:
        engine: "MIDDAY", "10ORB", "915ORB", "CHOTI", "HIGH_VOL", "NORMAL"
        is_hooked: bot_state.is_hooked
        atr_value: ATR14_15min value

    Returns:
        Multiplier: 1.5 ya 2.0
    """
    if engine == "MIDDAY":
        rule = MULTIPLIER_RULES["MIDDAY"]
    elif not is_hooked:
        # UNHOOKED main branch (sab engines)
        rule = MULTIPLIER_RULES["UNHOOK_MAIN"]
    else:
        # HOOKED main branch
        rule = MULTIPLIER_RULES["HOOK_MAIN"]

    multiplier = rule.multiplier_low if atr_value < rule.atr_threshold else rule.multiplier_high
    print(
        f"[ATR-MULT] engine={engine} hooked={is_hooked} atr={atr_value:.1f} mult={multiplier}")
    return multiplier


def classify_engine(orb_mode: str, is_choti: bool, high_vol: bool) -> str:
    """
    Current conditions se execution engine classify karta hai.
    """
    if orb_mode == "MIDDAY":
        return "MIDDAY"

    # Main branch engines (10ORB/915ORB same multiplier)
    if is_choti:
        return "CHOTI"
    elif high_vol:
        return "HIGH_VOL"
    else:
        return "NORMAL"  # 10ORB / 915ORB / default
