import csv
from datetime import datetime, timedelta
import pandas as pd

from vix_server import (
    smartlogin,
    getindex1min,
    get_orb_breakout_15min,
    get_midday_orb_breakout_15min,
    detect_half_gap,
    detect_orb_atr_ratio,
    detect_breakout_15m_atr_ratio,
    get_previous_day_high_low,
    check_breakout,
    NIFTYINDEXTOKEN,
)


OUTFILE = "quick_index_backtest.csv"


def ensure_dt_index(df: pd.DataFrame) -> pd.DataFrame:
    if isinstance(df.index, pd.DatetimeIndex):
        return df.sort_index()
    if "time" in df.columns:
        df = df.copy()
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")
        return df.sort_index()
    raise ValueError("No datetime index/time column")


def backtest_one_day(api, trade_date: str):
    row = {
        "date": trade_date,
        "mode": "",             # MORNING / MIDDAY
        "rule": "",             # HALF_GAP / ATR_NORMAL / ORB_LATE / NO_TRADE
        "gap_type": "",
        "half_gap_type": "",
        "atr14_15m": 0.0,
        "orb_high": 0.0,
        "orb_low": 0.0,
        "orb_range": 0.0,
        "bo15_high": 0.0,
        "bo15_low": 0.0,
        "bo15_range": 0.0,
        "bo15_ratio_atr": 0.0,
        "bo_over_orb": 0.0,
        "trigger_side": "",
        "trigger_time": "",
        "trigger_price": 0.0,
        "buy_entry_time": "",
        "buy_entry_price": 0.0,
        "buy_exit_time": "",
        "buy_exit_price": 0.0,
        "buy_pnl": 0.0,
        "sell_entry_time": "",
        "sell_entry_price": 0.0,
        "sell_exit_time": "",
        "sell_exit_price": 0.0,
        "sell_pnl": 0.0,
        "total_pnl": 0.0,
        "reason": "",
    }

    # ====== NIFTY 1-min data ======
    idxdf = getindex1min(api, trade_date, symboltoken=NIFTYINDEXTOKEN)
    if idxdf.empty:
        row["reason"] = "NO_INDEX_DATA"
        return row

    try:
        full_idxdf = ensure_dt_index(idxdf)
    except Exception:
        row["reason"] = "NO_DATETIME_INDEX"
        return row

    # ====== PREV DAY HIGH/LOW ======
    prev = get_previous_day_high_low(api, trade_date)
    prevhigh = prev.get("prev_high") or prev.get("prevhigh")
    prevlow = prev.get("prev_low") or prev.get("prevlow")
    if not prevhigh or not prevlow:
        row["reason"] = "NO_PREV_HL"
        return row

    # ====== HALF-GAP (includes ATR14) ======
    half_gap = detect_half_gap(api, full_idxdf, trade_date)
    row["half_gap_type"] = half_gap.get("half_gap_type", "")
    row["gap_type"] = half_gap.get("half_gap_type", "")
    atr14 = float(half_gap.get("atr_14") or 0.0)
    row["atr14_15m"] = atr14

    # ====== PREV DAY TICK BREAKOUT TILL 13:30 ======
    trade_date_obj = full_idxdf.index[0].date()
    cutoff_1330 = datetime.combine(
        trade_date_obj, datetime.strptime("13:30", "%H:%M").time())
    idx_till_1330 = full_idxdf[full_idxdf.index <= cutoff_1330]
    prev_break_till_1330 = check_breakout(idx_till_1330, prevhigh, prevlow)
    prev_break_flag_1330 = bool(prev_break_till_1330.get("breakout"))

    # ====== MORNING vs MIDDAY ORB ======
    use_midday_orb_only = not prev_break_flag_1330
    orb_mode = "MORNING"

    orb_info = get_orb_breakout_15min(full_idxdf)
    if orb_info.get("status") == "ok" and not use_midday_orb_only:
        orb_mode = "MORNING"
    else:
        orb_info = get_midday_orb_breakout_15min(full_idxdf)
        if orb_info.get("status") != "ok":
            row["reason"] = f"NO_ORB_{orb_info.get('status')}"
            return row
        orb_mode = "MIDDAY"

    row["mode"] = orb_mode

    trigger_side = orb_info["trigger_side"]
    trigger_time = orb_info["trigger_time"]
    trigger_price = float(orb_info["trigger_price"])
    marked_high = float(orb_info["marked_high"])
    marked_low = float(orb_info["marked_low"])
    orb_range = marked_high - marked_low

    row["trigger_side"] = trigger_side
    row["trigger_time"] = str(trigger_time)
    row["trigger_price"] = trigger_price
    row["orb_high"] = marked_high
    row["orb_low"] = marked_low
    row["orb_range"] = orb_range

    # ====== ORB RANGE / ATR14 (info only) ======
    _ = detect_orb_atr_ratio(full_idxdf, atr14)

    # ====== BREAKOUT 15-MIN CANDLE / ATR14 ======
    idx15 = full_idxdf.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    bo15_info = detect_breakout_15m_atr_ratio(idx15, trigger_time, atr14)
    bo15_high = bo15_info.get("bo15_high", 0.0)
    bo15_low = bo15_info.get("bo15_low", 0.0)
    bo15_range = bo15_info.get("bo15_range", 0.0)
    bo15_ratio = bo15_info.get("bo15_ratio", 0.0)

    row["bo15_high"] = bo15_high
    row["bo15_low"] = bo15_low
    row["bo15_range"] = bo15_range
    row["bo15_ratio_atr"] = bo15_ratio
    row["bo_over_orb"] = (bo15_range / orb_range) if orb_range > 0 else 0.0

    # ====== RULE TAGGING ======
    is_half_gap = half_gap.get("is_half_gap", False)
    rule = "HALF_GAP" if is_half_gap else "ATR_NORMAL"

    # ORB_LATE: only if MORNING and entry >= 13:30
    rule_for_buy = rule
    rule_for_sell = rule

    # Simple entry time approx = trigger_time (index-only backtest)
    if orb_mode == "MORNING" and trigger_time.time() >= datetime.strptime("13:30", "%H:%M").time():
        rule_for_buy = "ORB_LATE"
        rule_for_sell = "ORB_LATE"

    row["rule"] = rule_for_buy  # main tag

    reasons = [orb_mode, rule]
    if orb_mode == "MORNING" and not prev_break_flag_1330:
        reasons.append("PREV_HL_INTACT")
    if bo15_ratio >= 1.8:
        reasons.append("BO15_HIGH_VOL")
    if rule_for_buy == "ORB_LATE":
        reasons.append("ORB_LATE")
    row["reason"] = "+".join(reasons)

    # ====== INDEX-ONLY ENTRY/EXIT & PNL ======
    # ENTRY: trigger close price, at trigger_time
    # EXIT:  15-min breakout candle close price, at bucket_start+15m
    # (simple but fast backtest, no processnormal, koi options nahi)

    # Find breakout 15m candle close
    if bo15_range > 0:
        # bucket_start was used inside detect_breakout_15m_atr_ratio:
        bucket_start = trigger_time.replace(
            minute=(trigger_time.minute // 15) * 15,
            second=0,
            microsecond=0,
        )
        if bucket_start in idx15.index:
            bo15_close = float(idx15.loc[bucket_start]["close"])
            bo15_close_time = bucket_start + timedelta(minutes=15)
        else:
            bo15_close = trigger_price
            bo15_close_time = trigger_time
    else:
        bo15_close = trigger_price
        bo15_close_time = trigger_time

    # BUY leg PnL (BUY index: profit = exit - entry)
    buy_entry_price = trigger_price if trigger_side == "BUY" else None
    sell_entry_price = trigger_price if trigger_side == "SELL" else None

    buy_pnl = 0.0
    sell_pnl = 0.0

    if buy_entry_price is not None:
        buy_exit_price = bo15_close
        buy_pnl = buy_exit_price - buy_entry_price
        row["buy_entry_time"] = str(trigger_time)
        row["buy_entry_price"] = buy_entry_price
        row["buy_exit_time"] = str(bo15_close_time)
        row["buy_exit_price"] = buy_exit_price
        row["buy_pnl"] = buy_pnl

    if sell_entry_price is not None:
        sell_exit_price = bo15_close
        # SELL index: profit = entry - exit  (normal sign)
        sell_pnl = sell_entry_price - sell_exit_price
        row["sell_entry_time"] = str(trigger_time)
        row["sell_entry_price"] = sell_entry_price
        row["sell_exit_time"] = str(bo15_close_time)
        row["sell_exit_price"] = sell_exit_price
        row["sell_pnl"] = sell_pnl

    row["total_pnl"] = buy_pnl + sell_pnl

    return row


def run_backtest(from_date: str, to_date: str):
    api = smartlogin()
    if api is None:
        print("Login failed")
        return

    from_dt = datetime.strptime(from_date, "%Y-%m-%d").date()
    to_dt = datetime.strptime(to_date, "%Y-%m-%d").date()

    fieldnames = [
        "date", "mode", "rule", "gap_type", "half_gap_type",
        "atr14_15m", "orb_high", "orb_low", "orb_range",
        "bo15_high", "bo15_low", "bo15_range", "bo15_ratio_atr",
        "bo_over_orb",
        "trigger_side", "trigger_time", "trigger_price",
        "buy_entry_time", "buy_entry_price", "buy_exit_time",
        "buy_exit_price", "buy_pnl",
        "sell_entry_time", "sell_entry_price", "sell_exit_time",
        "sell_exit_price", "sell_pnl",
        "total_pnl", "reason",
    ]

    with open(OUTFILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        cur = from_dt
        while cur <= to_dt:
            d_str = cur.strftime("%Y-%m-%d")
            row = backtest_one_day(api, d_str)
            writer.writerow(row)
            print("Done", d_str, "|", row["reason"],
                  "| PnL:", row["total_pnl"])
            cur += timedelta(days=1)

    print("Saved to", OUTFILE)


if __name__ == "__main__":
    # yahan apna date range daalo
    run_backtest("2026-01-01", "2026-02-17")
