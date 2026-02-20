#!/usr/bin/env python3

"""
Standalone MCX test script with index-based triggers.

Logic (BUY side example):

- FUT (index) price watch karo:
    buy_entry_price   -> option BUY entry
    buy_target_price  -> option SELL exit
    buy_sl_price      -> optional SL exit

Sab decisions FUT price pe, option sirf instrument hai.
"""

import time
from typing import Dict, Any, Optional

from vix_server import (
    load_accounts,
    smartlogin_for_account,
    SmartApiSafeLogger,
    smart_mod,
)
from SmartApi import SmartConnect

# ---------- CONFIG: symbols & tokens ----------

# FUTURE (index-like) jiska price watch karna hai
FUT_SYMBOL = "CRUDEOILM19FEB26FUT"
FUT_TOKEN = "467014"      # FUT token (trading master se)

# OPTION jisme trade lena hai (6050 CE, 17-MAR-2026 expiry)
OPTION_SYMBOL = "CRUDEOILM17MAR266000CE"
OPTION_TOKEN = "499243"

QTY = 10                  # lot size ke mutabik (Crude mini: 10)

# ---------- CONFIG: BUY side triggers (FUT price based) ----------

BUY_ENABLED = True

BUY_ENTRY_PRICE = 6020.0     # FUT entry price
BUY_TARGET_PRICE = 6030.0    # FUT target price
BUY_SL_PRICE: Optional[float] = None  # e.g. 6028.0; None => SL disabled

PRICE_TOLERANCE = 5.0        # itne range me price match manenge
POLL_INTERVAL = 1.0          # seconds

# ---------- (Optional) SELL side structure ready ----------

SELL_ENABLED = False         # abhi test ke liye False rakha hai

SELL_ENTRY_PRICE = 0.0
SELL_TARGET_PRICE = 0.0
SELL_SL_PRICE: Optional[float] = None

# ---------- Logger patch (as in vix_server) ----------

_smartapi_logger = smart_mod.logger
smart_mod.logger = SmartApiSafeLogger(_smartapi_logger, {})

# ---------- Helper: generic market order ----------


def place_mcx_market_order(
    api: SmartConnect,
    token: str,
    symbol: str,
    side: str,
    qty: int,
) -> Dict[str, Any]:
    """Simple MCX MARKET order for given symbol+token."""
    payload = {
        "variety": "NORMAL",
        "tradingsymbol": symbol,
        "symboltoken": token,
        "transactiontype": side,   # "BUY" or "SELL"
        "exchange": "MCX",
        "ordertype": "MARKET",
        "producttype": "INTRADAY",
        "duration": "DAY",
        "quantity": qty,
        "price": 0,
        "triggerprice": 0,
    }

    print(f"[MCX ORDER REQ] {side} {qty} {symbol} @ {token} payload={payload}")
    try:
        res = api.placeOrder(payload)
        print(f"[MCX ORDER RES] {res}")
        # SmartAPI kabhi string order_id return karta hai, kabhi dict.
        if isinstance(res, str):
            return {"status": True, "order_id": res}
        return res
    except Exception as e:
        print(f"[MCX ORDER ERROR] {e}")
        return {"status": False, "message": str(e)}

# ---------- Helper: quote / LTP fetch ----------


def get_ltp_safe(api: SmartConnect, exchange: str, tradingsymbol: str, token: str) -> Optional[float]:
    """SmartAPI se LTP safely fetch karo, error pe None."""
    try:
        data = api.ltpData(exchange, tradingsymbol, token)
        ltp = data.get("data", {}).get("ltp")
        print(f"[LTP] {exchange} {tradingsymbol} ({token}) -> {ltp}")
        return float(ltp) if ltp is not None else None
    except Exception as e:
        print(f"[LTP ERROR] {exchange} {tradingsymbol} ({token}): {e}")
        return None

# ---------- Core: BUY side index-based flow ----------


def run_buy_side(api: SmartConnect) -> None:
    """
    BUY side:
        FUT hits BUY_ENTRY_PRICE -> option BUY
        FUT then hits BUY_TARGET_PRICE -> option SELL (target)
        FUT then hits BUY_SL_PRICE (if set) -> option SELL (SL)
    """
    position_open = False

    print(
        f"[MCX BUY MODE] FUT={FUT_SYMBOL} entry={BUY_ENTRY_PRICE}, "
        f"target={BUY_TARGET_PRICE}, sl={BUY_SL_PRICE}"
    )

    while True:
        fut_ltp = get_ltp_safe(api, "MCX", FUT_SYMBOL, FUT_TOKEN)
        if fut_ltp is None:
            time.sleep(POLL_INTERVAL)
            continue

        # 1) Entry condition: FUT ~ BUY_ENTRY_PRICE -> BUY option
        if not position_open:
            if abs(fut_ltp - BUY_ENTRY_PRICE) <= PRICE_TOLERANCE:
                print(
                    f"[MCX BUY ENTRY TRIGGER] FUT={fut_ltp} ~ {BUY_ENTRY_PRICE}")
                buy_res = place_mcx_market_order(
                    api=api,
                    token=OPTION_TOKEN,
                    symbol=OPTION_SYMBOL,
                    side="BUY",
                    qty=QTY,
                )
                if not buy_res.get("status", True):
                    print(f"[MCX BUY ENTRY FAIL] {buy_res}")
                else:
                    print(
                        f"[MCX BUY ENTRY OK] Position opened, order_id={buy_res.get('order_id')}")
                    position_open = True

        # 2) Agar position open hai to target/SL check karna
        else:
            # Target
            if abs(fut_ltp - BUY_TARGET_PRICE) <= PRICE_TOLERANCE or fut_ltp >= BUY_TARGET_PRICE:
                print(
                    f"[MCX BUY TARGET HIT] FUT={fut_ltp} target={BUY_TARGET_PRICE}")
                sell_res = place_mcx_market_order(
                    api=api,
                    token=OPTION_TOKEN,
                    symbol=OPTION_SYMBOL,
                    side="SELL",
                    qty=QTY,
                )
                print(f"[MCX BUY EXIT TARGET RES] {sell_res}")
                return

            # SL (optional)
            if BUY_SL_PRICE is not None:
                if abs(fut_ltp - BUY_SL_PRICE) <= PRICE_TOLERANCE or fut_ltp <= BUY_SL_PRICE:
                    print(f"[MCX BUY SL HIT] FUT={fut_ltp} sl={BUY_SL_PRICE}")
                    sell_res = place_mcx_market_order(
                        api=api,
                        token=OPTION_TOKEN,
                        symbol=OPTION_SYMBOL,
                        side="SELL",
                        qty=QTY,
                    )
                    print(f"[MCX BUY EXIT SL RES] {sell_res}")
                    return

        time.sleep(POLL_INTERVAL)

# ---------- (Optional) Core: SELL side index-based flow ----------


def run_sell_side(api: SmartConnect) -> None:
    """
    SELL side (structure ready, enable when needed):
        FUT hits SELL_ENTRY_PRICE -> option SELL (short)
        FUT then hits SELL_TARGET_PRICE -> option BUY (cover, target)
        FUT then hits SELL_SL_PRICE (if set) -> option BUY (cover, SL)
    """
    position_open = False

    print(
        f"[MCX SELL MODE] FUT={FUT_SYMBOL} entry={SELL_ENTRY_PRICE}, "
        f"target={SELL_TARGET_PRICE}, sl={SELL_SL_PRICE}"
    )

    while True:
        fut_ltp = get_ltp_safe(api, "MCX", FUT_SYMBOL, FUT_TOKEN)
        if fut_ltp is None:
            time.sleep(POLL_INTERVAL)
            continue

        # 1) Entry condition: FUT ~ SELL_ENTRY_PRICE -> SELL option
        if not position_open:
            if abs(fut_ltp - SELL_ENTRY_PRICE) <= PRICE_TOLERANCE:
                print(
                    f"[MCX SELL ENTRY TRIGGER] FUT={fut_ltp} ~ {SELL_ENTRY_PRICE}")
                sell_res = place_mcx_market_order(
                    api=api,
                    token=OPTION_TOKEN,
                    symbol=OPTION_SYMBOL,
                    side="SELL",
                    qty=QTY,
                )
                if not sell_res.get("status", True):
                    print(f"[MCX SELL ENTRY FAIL] {sell_res}")
                else:
                    print("[MCX SELL ENTRY OK] Position opened (short)")
                    position_open = True

        else:
            # Target (for short, target usually below entry)
            if abs(fut_ltp - SELL_TARGET_PRICE) <= PRICE_TOLERANCE or fut_ltp <= SELL_TARGET_PRICE:
                print(
                    f"[MCX SELL TARGET HIT] FUT={fut_ltp} target={SELL_TARGET_PRICE}")
                buy_res = place_mcx_market_order(
                    api=api,
                    token=OPTION_TOKEN,
                    symbol=OPTION_SYMBOL,
                    side="BUY",
                    qty=QTY,
                )
                print(f"[MCX SELL EXIT TARGET RES] {buy_res}")
                return

            # SL (optional, usually above entry for short)
            if SELL_SL_PRICE is not None:
                if abs(fut_ltp - SELL_SL_PRICE) <= PRICE_TOLERANCE or fut_ltp >= SELL_SL_PRICE:
                    print(
                        f"[MCX SELL SL HIT] FUT={fut_ltp} sl={SELL_SL_PRICE}")
                    buy_res = place_mcx_market_order(
                        api=api,
                        token=OPTION_TOKEN,
                        symbol=OPTION_SYMBOL,
                        side="BUY",
                        qty=QTY,
                    )
                    print(f"[MCX SELL EXIT SL RES] {buy_res}")
                    return

        time.sleep(POLL_INTERVAL)

# ---------- Main ----------


def main():
    # 1) Account pick karo
    accounts = load_accounts()
    if not accounts:
        print("No accounts found in accountsconfig.json")
        return

    acc = accounts[0]
    print(f"Using account: {acc.name}")

    # 2) SmartAPI login
    try:
        api = smartlogin_for_account(acc)
        print("Login success")
    except Exception as e:
        print(f"Login failed: {e}")
        return

    # 3) BUY / SELL flows
    if BUY_ENABLED:
        run_buy_side(api)

    if SELL_ENABLED:
        run_sell_side(api)

    # 4) Session close
    try:
        api.terminateSession(acc.clientid)
    except Exception:
        pass


if __name__ == "__main__":
    main()
