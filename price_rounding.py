import math


def round_index_price_for_side(idx_price: float, side: str) -> int:
    """
    NIFTY index ke breakout/entry ke liye rounding:
    BUY  -> next integer
    SELL -> floor integer
    """
    if side.upper() == "BUY":
        return math.floor(idx_price) + 1
    elif side.upper() == "SELL":
        return math.floor(idx_price)
    else:
        raise ValueError(f"Invalid side for rounding: {side}")
