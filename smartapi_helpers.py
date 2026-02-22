# smartapi_helpers.py

from typing import Tuple  # top pe
from datetime import datetime, timedelta, time as dtime
from typing import Optional, Dict, Any

import pandas as pd
from SmartApi import SmartConnect
from price_rounding import round_index_price_for_side
import pyotp
import json

from config import (
    APIKEY,
    CLIENTID,
    PASSWORD,
    TOTPSECRET,
    NIFTYINDEXTOKEN,
    SENSEXINDEXTOKEN,
    SCRIPMASTERFILE,
    MARKING_START,
    MARKING_END,
)

# ========== SMARTAPI LOGIN ==========


def smartlogin() -> Optional[SmartConnect]:
    totp = pyotp.TOTP(TOTPSECRET).now()
    api = SmartConnect(api_key=APIKEY)
    data = api.generateSession(CLIENTID, PASSWORD, totp)
    if not data.get("status"):
        print("SmartAPI login failed", data)
        return None
    return api


# ========== INDEX DATA (1-MIN) ==========

def getindex1min(
    api: SmartConnect,
    tradedate: str,
    symboltoken: Optional[str] = None,
) -> pd.DataFrame:
    d = datetime.strptime(tradedate, "%Y-%m-%d")
    fromdt = d.replace(hour=9, minute=15)
    todt = d.replace(hour=15, minute=30)

    token = symboltoken or NIFTYINDEXTOKEN

    exch = "NSE"
    if token == SENSEXINDEXTOKEN:
        exch = "BSE"

    payload = {
        "exchange": exch,
        "symboltoken": token,
        "interval": "ONE_MINUTE",
        "fromdate": fromdt.strftime("%Y-%m-%d %H:%M"),
        "todate": todt.strftime("%Y-%m-%d %H:%M"),
    }

    hist = api.getCandleData(payload)
    print(
        "DEBUG INDEX HIST:",
        exch,
        token,
        hist.get("status"),
        len(hist.get("data") or []),
    )

    if not hist.get("status"):
        msg = hist.get("message") or "Unknown error"
        print("Index getCandleData error:", msg, "token", token)
        return pd.DataFrame()

    df = pd.DataFrame(
        hist["data"],
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = df.set_index("time")
    return df


# ========== ORB BREAKOUT (15-MIN) ==========

def get_orb_breakout_15min(idx1: pd.DataFrame) -> Dict[str, Any]:
    """
    ORB + breakout logic (STRICT WINDOW):

    - ORB candle: 10:00–10:15 (15-min)
    - Valid breakout candles:
        10:15 se 12:15 tak wali 15-min candles ke CLOSE
        (yaani 10:15, 10:30, ..., 12:15 close tak)
    - Breakout candle:
        CLOSE > ORB high  -> BUY
        CLOSE < ORB low   -> SELL
    - Gann CMP:
        BUY  -> breakout candle ka HIGH
        SELL -> breakout candle ka LOW
    """
    if idx1.empty:
        return {"status": "error", "message": "Empty index DF"}

    # Ensure datetime index
    if not isinstance(idx1.index, pd.DatetimeIndex):
        if "time" in idx1.columns:
            idx1 = idx1.copy()
            idx1["time"] = pd.to_datetime(idx1["time"])
            idx1 = idx1.set_index("time")
        else:
            return {"status": "error", "message": "No datetime index/time column"}

    # 1-min -> 15-min
    idx15 = (
        idx1.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    if idx15.empty:
        return {"status": "error", "message": "No 15-min data"}

    trade_date = idx15.index[0].date()

    # ORB candle timestamp = 10:00
    orb_start = datetime.combine(
        trade_date, datetime.strptime("10:00", "%H:%M").time())
    # last VALID breakout candle timestamp = 12:15
    orb_last = datetime.combine(
        trade_date, datetime.strptime("12:15", "%H:%M").time())

    # 10:00–10:15 ORB candle
    if orb_start not in idx15.index:
        return {"status": "error", "message": "No 10:00–10:15 15-min candle"}

    orb_row = idx15.loc[orb_start]
    marked_high = float(orb_row["high"])
    marked_low = float(orb_row["low"])

    print("[ORB-DEBUG] ORB 10:00-10:15 High/Low:", marked_high, marked_low)

    # Breakout sirf 10:15 se 12:15 ke beech
    trigger_df = idx15[(idx15.index > orb_start) & (idx15.index <= orb_last)]

    if trigger_df.empty:
        return {
            "status": "no_orb_breakout",
            "message": "No 15-min candles in ORB breakout window (10:15–12:15)",
            "marked_high": marked_high,
            "marked_low": marked_low,
        }

    for ts, row in trigger_df.iterrows():
        c = float(row["close"])
        h = float(row["high"])
        l = float(row["low"])

        if c > marked_high:
            print("[ORB-DEBUG] BUY breakout at", ts, "close", c, "high", h)
            return {
                "status": "ok",
                "trigger_side": "BUY",
                "trigger_time": ts,
                "trigger_price": h,  # breakout candle HIGH
                "marked_high": marked_high,
                "marked_low": marked_low,
            }

        if c < marked_low:
            print("[ORB-DEBUG] SELL breakout at", ts, "close", c, "low", l)
            return {
                "status": "ok",
                "trigger_side": "SELL",
                "trigger_time": ts,
                "trigger_price": l,  # breakout candle LOW
                "marked_high": marked_high,
                "marked_low": marked_low,
            }

    return {
        "status": "no_orb_breakout",
        "message": "No ORB close breakout between 10:15 and 12:15",
        "marked_high": marked_high,
        "marked_low": marked_low,
    }


def get_midday_orb_breakout_15min(idx1: pd.DataFrame) -> Dict[str, Any]:
    """
    MID-DAY ORB breakout logic:

    - MID-DAY ORB candle: 12:30–12:45 (15-min)  -> timestamp 12:30
    - Breakout candles: 12:45 ke baad saari 15-min candles, EOD tak allowed.
    - Breakout:
        CLOSE > ORB high  -> BUY
        CLOSE < ORB low   -> SELL
    - Gann CMP / trigger_price:
        BUY/SELL dono ke liye breakout candle ka CLOSE (NIFTY rounding rule ke saath)
    """
    if idx1.empty:
        return {"status": "error", "message": "Empty index DF"}

    # Ensure datetime index
    if not isinstance(idx1.index, pd.DatetimeIndex):
        if "time" in idx1.columns:
            idx1 = idx1.copy()
            idx1["time"] = pd.to_datetime(idx1["time"])
            idx1 = idx1.set_index("time")
        else:
            return {"status": "error", "message": "No datetime index/time column"}

    # 1-min -> 15-min
    idx15 = (
        idx1.resample("15min")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )

    if idx15.empty:
        return {"status": "error", "message": "No 15-min data"}

    trade_date = idx15.index[0].date()

    # MID-DAY ORB candle timestamp = 12:30
    from datetime import datetime as _dt, time as _time

    orb_mid_start = _dt.combine(trade_date, _time(12, 30))

    if orb_mid_start not in idx15.index:
        return {"status": "error", "message": "No 12:30–12:45 15-min candle"}

    orb_row = idx15.loc[orb_mid_start]
    marked_high = float(orb_row["high"])
    marked_low = float(orb_row["low"])

    print("[MID-ORB-DEBUG] MID ORB 12:30-12:45 High/Low:",
          marked_high, marked_low)

    # NIFTY index ke rounded MIDDAY trigger levels (ORB high/low se)
    rounded_buy_mid = round_index_price_for_side(marked_high, "BUY")
    rounded_sell_mid = round_index_price_for_side(marked_low, "SELL")

    # Breakout: saari 15-min candles AFTER 12:30 candle (no upper cutoff)
    trigger_df = idx15[idx15.index > orb_mid_start]

    if trigger_df.empty:
        return {
            "status": "no_midday_orb_breakout",
            "message": "No 15-min candles after MID-DAY ORB candle",
            "marked_high": marked_high,
            "marked_low": marked_low,
        }

    for ts, row in trigger_df.iterrows():
        c = float(row["close"])
        h = float(row["high"])
        l = float(row["low"])

        # BUY breakout: close must reach rounded_buy_mid
        if c >= rounded_buy_mid:
            trigger_price = round_index_price_for_side(c, "BUY")
            print("[MID-ORB-DEBUG] BUY breakout at",
                  ts, "close", c, "tp", trigger_price)
            return {
                "status": "ok",
                "trigger_side": "BUY",
                "trigger_time": ts,
                "trigger_price": trigger_price,  # breakout CLOSE (rounded)
                "marked_high": marked_high,
                "marked_low": marked_low,
                "mode": "MIDDAY",
            }

        # SELL breakout: close must reach rounded_sell_mid
        if c <= rounded_sell_mid:
            trigger_price = round_index_price_for_side(c, "SELL")
            print("[MID-ORB-DEBUG] SELL breakout at",
                  ts, "close", c, "tp", trigger_price)
            return {
                "status": "ok",
                "trigger_side": "SELL",
                "trigger_time": ts,
                "trigger_price": trigger_price,  # breakout CLOSE (rounded)
                "marked_high": marked_high,
                "marked_low": marked_low,
                "mode": "MIDDAY",
            }

    return {
        "status": "no_midday_orb_breakout",
        "message": "No MID-DAY ORB close breakout after 12:45",
        "marked_high": marked_high,
        "marked_low": marked_low,
    }


# ========== PREVIOUS DAY HIGH / LOW ==========

def get_previous_day_high_low(api: SmartConnect, tradedate: str) -> Dict[str, float]:
    """
    Previous TRADING day ka NIFTY high/low fetch karta hai.
    Weekend + random weekday holiday, dono skip karega.
    Tries up to last 10 calendar days.
    """
    try:
        trade_dt = datetime.strptime(tradedate, "%Y-%m-%d")

        prev_day = trade_dt - timedelta(days=1)
        max_lookback = 10

        for _ in range(max_lookback):
            fromdt = prev_day.replace(hour=9, minute=15)
            todt = prev_day.replace(hour=15, minute=30)

            payload = {
                "exchange": "NSE",
                "symboltoken": NIFTYINDEXTOKEN,
                "interval": "ONE_MINUTE",
                "fromdate": fromdt.strftime("%Y-%m-%d %H:%M"),
                "todate": todt.strftime("%Y-%m-%d %H:%M"),
            }

            hist = api.getCandleData(payload)

            if hist.get("status") and hist.get("data"):
                df = pd.DataFrame(
                    hist["data"],
                    columns=["time", "open", "high", "low", "close", "volume"],
                )
                prev_high = float(df["high"].max())
                prev_low = float(df["low"].min())
                print(
                    f"Previous trading day ({prev_day.date()}) HIGH: {prev_high}, LOW: {prev_low}"
                )
                return {"prev_high": prev_high, "prev_low": prev_low}

            prev_day = prev_day - timedelta(days=1)

        print("Previous trading day data not available within lookback window")
        return {"prev_high": 0.0, "prev_low": 0.0}

    except Exception as e:
        print("get_previous_day_high_low error", e)
        return {"prev_high": 0.0, "prev_low": 0.0}


# ========== PREV DAY BREAKOUT CHECK ==========

def check_breakout(idxdf: pd.DataFrame, prev_high: float, prev_low: float) -> Dict[str, Any]:
    if idxdf.empty or prev_high == 0.0 or prev_low == 0.0:
        return {
            "breakout": False,
            "breakout_type": None,
            "breakout_time": None,
            "breakout_price": None,
        }

    for ts, row in idxdf.iterrows():
        hi = float(row["high"])
        lo = float(row["low"])

        if lo < prev_low:
            return {
                "breakout": True,
                "breakout_type": "LOW",
                "breakout_time": ts,
                "breakout_price": lo,
            }

        if hi > prev_high:
            return {
                "breakout": True,
                "breakout_type": "HIGH",
                "breakout_time": ts,
                "breakout_price": hi,
            }

    return {
        "breakout": False,
        "breakout_type": None,
        "breakout_time": None,
        "breakout_price": None,
    }


# ========== GAP DAY DETECTION (NEW LOGIC) ==========

def detect_gap_day(idxdf: pd.DataFrame, prev_high: float, prev_low: float) -> Dict[str, Any]:
    """
    1st 15-min candle (9:15–9:30) vs prev day high/low:
    - Gap Up:  first_low > prev_high
    - Gap Down: first_high < prev_low
    - Else: NORMAL
    """
    if idxdf.empty or prev_high == 0.0 or prev_low == 0.0:
        return {"gap_type": "UNKNOWN", "gap_up": False, "gap_down": False}

    # 9:15–9:30 window
    date0 = idxdf.index[0].date()
    t_start = datetime.combine(date0, dtime(9, 15))
    t_end = datetime.combine(date0, dtime(9, 30))

    first_15 = idxdf[(idxdf.index >= t_start) & (idxdf.index < t_end)]
    if first_15.empty:
        return {"gap_type": "UNKNOWN", "gap_up": False, "gap_down": False}

    first_low = float(first_15["low"].min())
    first_high = float(first_15["high"].max())

    gap_up = first_low > prev_high
    gap_down = first_high < prev_low

    if gap_up:
        gtype = "GAP_UP"
    elif gap_down:
        gtype = "GAP_DOWN"
    else:
        gtype = "NORMAL"

    print(
        f"[GAP] first_low={first_low}, first_high={first_high}, prev_high={prev_high}, prev_low={prev_low}, type={gtype}")

    return {"gap_type": gtype, "gap_up": gap_up, "gap_down": gap_down}


# ========== STRIKE ROUNDING (NEW ATM RULE) ==========

def round_to_atm_strike(index_price: float) -> int:
    """
    50 tak DOWN (same strike), 51 se UP (next 50).
    Example:
      25050 -> 25000
      25051 -> 25100
    """
    # nearest 50 base
    base = int(index_price // 50) * 50
    diff = index_price - base

    if diff <= 50:   # 0..50 -> base
        return base
    else:            # 50 se upar -> base + 50
        return base + 50


def calc_atm_strikes(openprice: float) -> Tuple[int, int]:
    """
    CE/PE dono same ATM strike (round_to_atm_strike se).
    """
    strike = round_to_atm_strike(openprice)
    print(f"[STRIKE] open={openprice}, atm_strike={strike}")
    return int(strike), int(strike)


# ========== OPTION TOKEN & DATA ==========

def getoptiontoken(strike: int, expiry: str, opttype: str) -> Optional[str]:
    try:
        with open(SCRIPMASTERFILE, "r") as f:
            data = json.load(f)
        records = data if isinstance(data, list) else data.get("data", [])

        strike_in_file_units = float(strike) * 100.0
        expirycode = datetime.strptime(
            expiry, "%Y-%m-%d").strftime("%d%b%Y").upper()

        print(
            "DEBUG getoptiontoken: strike_idx", strike,
            "strike_file", strike_in_file_units,
            "expirycode", expirycode,
            "type", opttype,
        )

        for row in records:
            if (
                row.get("exch_seg") == "NFO"
                and row.get("name") == "NIFTY"
                and row.get("instrumenttype") == "OPTIDX"
                and row.get("symbol", "").endswith(opttype.upper())
            ):
                row_strike = float(row.get("strike", 0.0))
                row_expiry = (row.get("expiry") or "").upper()

                if row_strike == strike_in_file_units and row_expiry == expirycode:
                    print("DEBUG token hit:", row.get(
                        "symbol"), row_strike, row_expiry)
                    return str(row.get("token"))

        print("DEBUG token not found for",
              strike_in_file_units, expirycode, opttype)
        return None
    except Exception as e:
        print("getoptiontoken error", e)
        return None


def getoption1min(api: SmartConnect, token: str, tradedate: str) -> pd.DataFrame:
    d = datetime.strptime(tradedate, "%Y-%m-%d")
    fromdt = d.replace(hour=9, minute=15)
    todt = d.replace(hour=15, minute=30)

    payload = {
        "exchange": "NFO",
        "symboltoken": token,
        "interval": "ONE_MINUTE",
        "fromdate": fromdt.strftime("%Y-%m-%d %H:%M"),
        "todate": todt.strftime("%Y-%m-%d %H:%M"),
    }

    hist = api.getCandleData(payload)
    if not hist.get("status"):
        msg = hist.get("message") or "Unknown error"
        print("Option getCandleData error:", msg, "token", token)
        return pd.DataFrame()

    df = pd.DataFrame(
        hist["data"],
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    df["time"] = pd.to_datetime(df["time"]).dt.tz_localize(None)
    df = df.set_index("time")
    return df


def getoptioncloseat(optdf: pd.DataFrame, ts: datetime) -> Optional[float]:
    if ts in optdf.index:
        return float(optdf.loc[ts, "close"])
    prev = optdf[optdf.index < ts]
    if not prev.empty:
        return float(prev.iloc[-1]["close"])
    return None


# ========== ORDER HELPERS (SAME AS PEHLE) ==========


def place_market_order(
    api: SmartConnect,
    tradingsymbol: str,
    symboltoken: str,
    transactiontype: str,
    quantity: int,
    exchange: str = "NFO",
    product: str = "NRML",
    ordertype: str = "MARKET",
    variety: str = "NORMAL",
) -> dict:
    from datetime import datetime
    try:
        payload = {
            "variety": variety,
            "tradingsymbol": tradingsymbol,
            "symboltoken": symboltoken,
            "transactiontype": transactiontype.upper(),
            "exchange": exchange,
            "ordertype": ordertype,
            "producttype": product,
            "duration": "DAY",
            "price": 0,
            "triggerprice": 0,
            "quantity": int(quantity),
            "squareoff": 0,
            "stoploss": 0,
            "trailingStopLoss": 0,
        }
        print("[ORDER-REQ TIME]", datetime.now(), payload)
        # SmartAPI ka official signature: placeOrder(payload_dict)
        res = api.placeOrder(payload)
        print("[RAW ORDER RES]", res)
        return {"status": "ok", "response": res}
    except Exception as e:
        import traceback
        print("[ORDER ERROR RAW]", repr(e))
        traceback.print_exc()
        return {"status": "error", "message": str(e)}


def exit_position(
    api: SmartConnect,
    tradingsymbol: str,
    symboltoken: str,
    side: str,
    quantity: int,
    exchange: str = "NFO",
    product: str = "NRML",
    ordertype: str = "MARKET",
    variety: str = "NORMAL",
) -> dict:
    from datetime import datetime
    try:
        opposite = "SELL" if side.upper() == "BUY" else "BUY"
        payload = {
            "variety": variety,
            "tradingsymbol": tradingsymbol,
            "symboltoken": symboltoken,
            "transactiontype": opposite,
            "exchange": exchange,
            "ordertype": ordertype,
            "producttype": product,
            "duration": "DAY",
            "price": 0,
            "triggerprice": 0,
            "quantity": int(quantity),
            "squareoff": 0,
            "stoploss": 0,
            "trailingStopLoss": 0,
        }
        print("[EXIT-REQ TIME]", datetime.now(), payload)
        res = api.placeOrder(payload)
        print("[RAW EXIT RES]", res)
        return {"status": "ok", "response": res}
    except Exception as e:
        import traceback
        print("[EXIT ERROR RAW]", repr(e))
        traceback.print_exc()
        return {"status": "error", "message": str(e)}
