# gann_engine.py
import json
from pathlib import Path
from typing import Dict, Optional, Any

ROOT = Path(__file__).resolve().parent

GANN_EXCEL_PATH = ROOT / "GANN ONLY NIFTY BOT.xlsx"
GANNEXCELPATH_MIDDAY = ROOT / "GANN ONLY NIFTY MIDDAY BOT.xlsx"
GANN_JSON_PATH = ROOT / "gann_lookup_24000_27000.json"
GANN_MIDDAY_JSON_PATH = ROOT / "gann_midday_lookup_24000_27000.json"

with open(GANN_JSON_PATH, "r") as f:
    GANN_TABLE = json.load(f)

with open(GANN_MIDDAY_JSON_PATH, "r") as f:
    GANN_MIDDAY_TABLE = json.load(f)


def cut_dec(x: float) -> float:
    """Decimal hata do, rounding nahi."""
    return float(int(x))


def get_gann_row_from_json(cmp_price: float, midpoint: bool = False) -> dict:
    cmp_int = int(round(cmp_price))
    cmp_int = max(24000, min(27000, cmp_int))
    table = GANN_MIDDAY_TABLE if midpoint else GANN_TABLE
    return table[str(cmp_int)]


def read_gann_levels_from_excel(excel_path: Path) -> dict:
    import pandas as pd
    df = pd.read_excel(excel_path, sheet_name="15 min NIFTY", header=None)

    buy_row_idx = df[df.iloc[:, 8] == "Buy at/above"].index[0]
    buy_level = float(df.iloc[buy_row_idx, 9])
    sell_row_idx = df[df.iloc[:, 10] == "Sell at/below"].index[0]
    sell_level = float(df.iloc[sell_row_idx, 11])

    buy_entry_row_idx = df[df.iloc[:, 9] == "BUY ENTRY"].index[0]
    buy_entry = float(df.iloc[buy_entry_row_idx, 10])
    sell_entry_row_idx = df[df.iloc[:, 11] == "SELL ENTRY"].index[0]
    sell_entry = float(df.iloc[sell_entry_row_idx, 12])

    buy_t2_row_idx = df[df.iloc[:, 11] == "Target 2"].index[0]
    buy_t2 = float(df.iloc[buy_t2_row_idx, 9])
    sell_t2_row_idx = df[df.iloc[:, 11] == "Target 2"].index[-1]
    sell_t2 = float(df.iloc[sell_t2_row_idx, 13])

    support1_row_idx = df[df.iloc[:, 8] == "Support 1"].index[0]
    support1 = float(df.iloc[support1_row_idx, 9])
    resistance1_row_idx = df[df.iloc[:, 8] == "Resistance 1"].index[0]
    resistance1 = float(df.iloc[resistance1_row_idx, 9])

    return {
        "buy_level_label":  buy_level,
        "sell_level_label": sell_level,
        "buy_entry":  buy_entry,
        "sell_entry": sell_entry,
        "buy_t2":     buy_t2,
        "sell_t2":    sell_t2,
        "support1":   support1,
        "resistance1": resistance1,
    }


def calc_gann_levels_with_excel(
    cmp_price: float,
    side: str,
    excel_path: Optional[Path] = None,
) -> Dict[str, float]:
    midpoint = (excel_path == GANNEXCELPATH_MIDDAY)
    row = get_gann_row_from_json(cmp_price, midpoint=midpoint)

    if side == "BUY":
        buy_entry = row["buy_entry"]
        buy_t2 = row["buy_t2"]
        buy_t3 = row["buy_t3"]
        buy_t4 = row["buy_t4"]
        buy_t25 = row["buy_t25"]
        buy_t35 = row["buy_t35"]
        sell_entry = row["sell_entry_opp"]
        sell_t15 = row["sell_t15"]
        sell_t2 = row["sell_t2"]
        sell_t3 = row["sell_t3"]
        sell_t4 = row["sell_t4"]
        sell_t25 = row["sell_t25"]
        sell_t35 = row["sell_t35"]

    elif side == "SELL":
        sell_entry = row["sell_entry"]
        sell_t2 = row["sell_t2"]
        sell_t3 = row["sell_t3"]
        sell_t4 = row["sell_t4"]
        sell_t25 = row["sell_t25"]
        sell_t35 = row["sell_t35"]
        sell_t15 = row["sell_t15"]
        buy_entry = row["buy_entry_opp"]
        buy_t15 = row["buy_t15"]
        buy_t2 = row["buy_t2"]
        buy_t3 = row["buy_t3"]
        buy_t4 = row["buy_t4"]
        buy_t25 = row["buy_t25"]
        buy_t35 = row["buy_t35"]

    else:
        raise ValueError(f"Invalid side: {side}")

    buy_sl = row["buy_sl"]
    sell_sl = row["sell_sl"]

    levels: Dict[str, float] = {
        "buy_entry":  float(buy_entry),
        "buy_t2":     float(buy_t2),
        "buy_t25":    float(buy_t25),
        "buy_t3":     float(buy_t3),
        "buy_t35":    float(buy_t35),
        "buy_t4":     float(buy
