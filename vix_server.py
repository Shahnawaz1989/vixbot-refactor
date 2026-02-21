# from first15min_sync_breakout import apply_first15min_sync_filter
from prev_day_hl_breakout import check_breakout as check_breakout_local
from borestriction_entry import (
    apply_default_bo_start_filter,
    find_entry_candles_for_both_legs,
)
from entry_timing_rule import get_entry_start_time
from orb_timing_rule import choose_orb_mode_and_info
from gann_mapping_rule import map_gann_levels_to_v1req
from choti_rule import apply_choti_rule
from orb_rule import (
    detect_orb_atr_ratio,
    detectbreakout15matrratio,
    get_marking_and_trigger,
    get_midday_entry_start,
)
from gann_engine import (
    calc_gann_levels_with_excel,
    get_gann_row_from_json,
    read_gann_levels_from_excel,
    cut_dec,
    GANN_EXCEL_PATH,
    GANNEXCELPATH_MIDDAY,
    GANN_TABLE,
    GANN_MIDDAY_TABLE,
)
from half_gap_rule import detect_half_gap, get_angel_atr_14, atr_tradingview_style
from gap_day_rule import detect_gap_day
from config import (
    APIKEY,
    CLIENTID,
    PASSWORD,
    TOTPSECRET,
    DEFAULTBOSTART,
    RISKSTARTTIME,
    BACKTESTDIR,
    NIFTYINDEXTOKEN,
    SENSEXINDEXTOKEN,
    VIXINDEXTOKEN,
)
from strategy import (
    findentryidx,
    applyborestriction,
    processnormal,
)
from smartapi_helpers import (
    # data helpers
    getindex1min,
    get_orb_breakout_15min,
    get_midday_orb_breakout_15min,
    getoptiontoken,
    getoption1min,
    calc_atm_strikes,
    get_previous_day_high_low,
    check_breakout,
    # order helpers yahan se ab local use honge
    # place_market_order,
    # exit_position,
)
from models import (
    AccountConfig,
    LiveAccountsPayload,
    SideConfig,
    VixRequest,
    SimpleVixConfig,
    LiveTradeSimpleRequest,
)
from typing import List, Dict
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional, Literal
import os
import json
import pandas as pd
import openpyxl  # Gann Excel ke liye
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime, timedelta, time
import numpy as np
from typing import Optional  # upar imports me ensure kar lo
from expiry_store import refresh_and_get_expiries
import xlwings as xw
import pyotp
from SmartApi import SmartConnect
from SmartApi import smartConnect as smart_module
import logging
import SmartApi.smartConnect as smart_mod
from fastapi.responses import PlainTextResponse
import os


# ===== Globals =====
LAST_MANUAL_MONITOR_RUN: Optional[str] = None
MANUAL_POSITIONS: Dict[str, list] = {}

# ===== PATCH: SmartApi internal logger ko safe bana do =====

_smartapi_logger = smart_mod.logger


class SmartApiSafeLogger(logging.LoggerAdapter):
    def error(self, msg, *args, **kwargs):
        try:
            return super().error(msg, *args, **kwargs)
        except Exception:
            return _smartapi_logger.error(msg, *args, **kwargs)


smart_mod.logger = SmartApiSafeLogger(_smartapi_logger, {})

# ===== END PATCH =====


# ========== V2 SIMPLE REQUEST MODEL (DATE + EXPIRY ONLY) ==========

class VixV2Request(BaseModel):
    """
    V2 ke liye minimal request:
    - sirf date + expiry (INDEX ya actual expiry date).
    Baaki sab backend ORB+Gann engine handle karega.
    """
    date: str            # "YYYY-MM-DD"
    expiry: str          # "YYYY-MM-DD" ya "INDEX"


# ---------- PROJECT ROOT PATH FIX ----------
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
# -------------------------------------------


app = FastAPI()

# ========= MCX HELPERS (CRUDE TEST) =========

FUT_SYMBOL = "CRUDEOILM19MAR26FUT"      # mcx_test.py se
FUT_TOKEN = "472790"
OPTION_SYMBOL = "CRUDEOILM17MAR266000CE"
OPTION_TOKEN = "499243"
MCX_QTY = 10              # Crude mini lot size

BUY_ENTRY_PRICE = 6020.0  # abhi hardcode test
BUY_TARGET_PRICE = 6035.0
BUY_SL_PRICE: Optional[float] = None
PRICE_TOLERANCE = 5.0
POLL_INTERVAL = 1.0


def place_mcx_market_order(
    api: SmartConnect,
    token: str,
    symbol: str,
    side: str,
    qty: int,
) -> Dict[str, Any]:
    payload = {
        "variety": "NORMAL",
        "tradingsymbol": symbol,
        "symboltoken": token,
        "transactiontype": side,  # "BUY" / "SELL"
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
        if isinstance(res, str):
            return {"status": True, "order_id": res}
        return res
    except Exception as e:
        print(f"[MCX ORDER ERROR] {e}")
        return {"status": False, "message": str(e)}


def get_ltp_safe(api: SmartConnect, exchange: str, tradingsymbol: str, token: str) -> Optional[float]:
    """SmartAPI se LTP safely fetch karo, error pe None."""
    try:
        data = api.ltpData(exchange, tradingsymbol, token)
        print("[RAW LTP RESP]", data)

        # error case: dict nahi ya status False
        if not isinstance(data, dict) or not data.get("status"):
            print(
                f"[LTP ERROR RESP] {exchange} {tradingsymbol} ({token}): {data}")
            return None

        inner = data.get("data") or {}
        ltp = inner.get("ltp")
        print(f"[LTP] {exchange} {tradingsymbol} ({token}) -> {ltp}")
        return float(ltp) if ltp is not None else None
    except Exception as e:
        print(f"[LTP ERROR] {exchange} {tradingsymbol} ({token}): {e}")
        return None

# ---------- ADMIN: Manual OpenAPI Scrip Master update ----------


@app.post("/admin/update-openapi")
def update_openapi():
    """
    Manual trigger: OpenAPIScripMaster.json ko latest AngelOne URL se update karega.
    Wohi script run karta hai jo cron use kar raha hai.
    """
    import subprocess
    import pathlib

    try:
        # Repo directory (vix_server.py ka parent folder)
        repo_dir = pathlib.Path(__file__).resolve().parent
        script_path = repo_dir / "update_openapi_scripmaster.sh"

        if not script_path.exists():
            return {
                "status": "ERROR",
                "message": f"Script not found: {script_path}",
            }

        result = subprocess.run(
            ["bash", str(script_path)],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=180,
        )

        ok = (result.returncode == 0)

        return {
            "status": "OK" if ok else "ERROR",
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    except Exception as e:
        return {
            "status": "ERROR",
            "message": str(e),
        }


@app.get("/admin/poll")
def poll_status():
    """
    App ke liye light polling endpoint.
    Manual monitor ka last run, time, etc. de sakte ho.
    """
    return {
        "status": "OK",
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "last_manual_monitor": LAST_MANUAL_MONITOR_RUN,
    }


LOG_FILE_PATH = "vix.log"  # jahan tum uvicorn ka output log karaoge


@app.get("/admin/logs", response_class=PlainTextResponse)
async def get_logs(lines: int = 300):
    """
    Last N lines of log file as plain text.
    """
    if not os.path.exists(LOG_FILE_PATH):
        return f"Log file not found: {LOG_FILE_PATH}"

    try:
        with open(LOG_FILE_PATH, "r") as f:
            all_lines = f.readlines()
        tail = all_lines[-lines:]
        return "".join(tail)
    except Exception as e:
        return f"Error reading logs: {e}"


def monitor_manual_positions():
    """
    Har 30 sec MANUAL_POSITIONS check:
    - BUY:  LTP >= target  OR  LTP <= SL  -> exit
    - SELL: LTP <= target  OR  LTP >= SL  -> exit
    """
    global MANUAL_POSITIONS

    if not MANUAL_POSITIONS:
        return

    accounts = load_accounts()

    for acc_name, positions in list(MANUAL_POSITIONS.items()):
        acc = next((a for a in accounts if a.name == acc_name), None)
        if acc is None:
            continue

        try:
            api = smartlogin_for_account(acc)
        except Exception as e:
            print(f"[MANUAL] login failed {acc_name}: {e}")
            continue

        try:
            for pos in positions[:]:  # copy for safe remove
                token = pos["token"]
                side = pos["side"]
                target = pos.get("target")
                sl = pos.get("sl")
                qty = pos.get("qty", 65)

                ltp = get_live_option_ltp(api, token)
                if ltp <= 0:
                    continue

                should_exit = False

                if side == "BUY":
                    if target is not None and ltp >= target:
                        should_exit = True
                    if sl is not None and ltp <= sl:
                        should_exit = True
                else:  # SELL
                    if target is not None and ltp <= target:
                        should_exit = True
                    if sl is not None and ltp >= sl:
                        should_exit = True

                if should_exit:
                    try:
                        exit_res = exit_position(api, token, side, qty)
                        print(
                            f"[MANUAL EXIT] {acc_name} {side} {pos['strike']} LTP={ltp} -> {exit_res}")
                    except Exception as e:
                        print(f"[MANUAL EXIT ERROR] {acc_name} {token}: {e}")
                    positions.remove(pos)

            if not positions:
                del MANUAL_POSITIONS[acc_name]

        finally:
            try:
                api.terminateSession(acc.clientid)
            except Exception:
                pass


# APScheduler init for auto live scheduling
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
scheduler.start()

scheduler.add_job(monitor_manual_positions, "interval", seconds=30)

# in-memory schedule store
SCHEDULE_STORE: Dict[str, dict] = {}  # key: schedule_id

MANUAL_POSITIONS = {}  # {accountname: [ManualPosition list]}


# ==== AUTO LIVE MODELS ====


class LiveScheduleRequest(BaseModel):
    date: str          # "YYYY-MM-DD" (trade date)
    expiry: str        # "INDEX" ya expiry date string
    accounts: List[str]
    mode: str = "AUTO_9_15"


class LiveScheduleCancel(BaseModel):
    schedule_id: str = "NIFTY_AUTO"


class LiveScheduleResponse(BaseModel):
    status: str
    message: str
    schedule_id: str | None = None
    run_at: str | None = None


class ManualOverrideRequest(BaseModel):
    accountname: str
    buy_prices: List[float] = []
    buy_targets: List[float] = []
    buy_sls: Optional[List[float]] = None
    sell_prices: List[float] = []
    sell_targets: List[float] = []
    sell_sls: Optional[List[float]] = None
    lots: int = 1
    expiry: str = "INDEX"


class ManualPosition(BaseModel):
    strike: float
    token: str
    side: str
    target: float
    sl: Optional[float] = None
    entry_order: dict = {}


# ========= BASIC SMARTAPI LOGIN (SINGLE ACCOUNT) =========


def smartlogin() -> Optional[SmartConnect]:
    """
    Legacy single-account login (APIKEY / CLIENTID / PASSWORD / TOTPSECRET from config).
    Backtest ke liye use ho raha hai.
    """
    totp = pyotp.TOTP(TOTPSECRET).now()
    api = SmartConnect(api_key=APIKEY)
    data = api.generateSession(CLIENTID, PASSWORD, totp)
    if not data.get("status"):
        print("SmartAPI login failed", data)
        return None
    return api


# ========= SMARTAPI LOGIN PER ACCOUNT =========


def smartlogin_for_account(acc: AccountConfig) -> SmartConnect:
    """
    Single account ke credentials se SmartAPI session banata hai.
    Live trading ke liye use hoga.
    """
    totp = pyotp.TOTP(acc.totpsecret).now()
    api = SmartConnect(api_key=acc.apikey)
    data = api.generateSession(acc.clientid, acc.password, totp)

    if not data.get("status"):
        msg = data.get("message") or "Login failed"
        print(f"SmartAPI login failed for {acc.name}: {msg}")
        raise ValueError(f"{acc.name}: {msg}")

    return api


# ========= LIVE ACCOUNTS CONFIG (JSON FILE) =========

ACCOUNTS_FILE = ROOT / "accounts_config.json"


def load_accounts() -> List[AccountConfig]:
    """
    accounts_config.json se accounts read karke
    AccountConfig ki list return karta hai.
    File format: [ { ... AccountConfig fields ... }, ... ]
    """
    if not os.path.exists(ACCOUNTS_FILE):
        return []
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [AccountConfig(**a) for a in data]
        return []
    except Exception as e:
        print("load_accounts error", e)
        return []


def save_accounts(cfg: LiveAccountsPayload) -> None:
    """
    LiveAccountsPayload ko simple list ke form me file me dump karta hai.
    """
    try:
        with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
            json.dump([acc.dict() for acc in cfg.accounts], f, indent=2)
    except Exception as e:
        print("save_accounts error", e)


@app.post("/live-accounts")
def set_live_accounts(cfg: LiveAccountsPayload) -> Dict[str, Any]:
    """
    App se multi-account config yahan aayega, server JSON file me store karega.
    """
    save_accounts(cfg)
    return {"status": "ok", "accounts": len(cfg.accounts)}


@app.get("/live-accounts")
def get_live_accounts() -> LiveAccountsPayload:
    """
    File se accounts read karke app ko bhejta hai.
    """
    accounts = load_accounts()
    return LiveAccountsPayload(accounts=accounts)


@app.get("/test-account-login")
def test_account_login():
    """
    Saare saved accounts ke liye SmartAPI login try karega.
    Koi order place nahi hoga, sirf login success/failure ka status return karega.
    """
    try:
        accounts = load_accounts()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Failed to load accounts: {e}"
        )

    if not accounts:
        return {
            "test_results": {},
            "message": "No accounts found in accounts_config.json",
        }

    results: Dict[str, Any] = {}

    for acc in accounts:
        try:
            # yahan naam bhi api rakho, session nahi
            api = smartlogin_for_account(acc)
            results[acc.name] = {
                "status": "OK",
                "note": "login_success",
            }
            # IMPORTANT: terminateSession hata do, yahi SmartConnect.root wala bug trigger kar raha
            # try:
            #     api.terminateSession(acc.clientid)
            # except Exception:
            #     pass
        except Exception as e:
            results[acc.name] = {
                "status": "ERROR",
                "message": str(e),
            }

    return {"test_results": results}


# ========= EXPIRIES ENDPOINTS =========


@app.get("/nifty-expiries-backtest")
def get_nifty_expiries_backtest() -> List[str]:
    """
    Backtest ke liye NIFTY OPTIDX expiries (YYYY-MM-DD),
    past + future sab dates.
    """
    return refresh_and_get_expiries(include_past=True)


@app.get("/nifty-expiries-live")
def get_nifty_expiries_live() -> List[str]:
    """
    Live trading ke liye NIFTY OPTIDX expiries (YYYY-MM-DD),
    sirf aaj ke baad wali.
    """
    return refresh_and_get_expiries(include_past=False)


@app.get("/day-open-vix")
def get_day_open_vix(date: str) -> Dict[str, Any]:
    """
    Diye gaye trading date ke liye:
    - NIFTY ka first 1-min candle open
    - INDIA VIX index ka first 1-min candle open
    """
    api = smartlogin()
    if api is None:
        raise HTTPException(status_code=500, detail="SmartAPI login failed")

    # NIFTY 1-min
    nifty_df = getindex1min(api, date, symboltoken=NIFTYINDEXTOKEN)
    if nifty_df.empty:
        raise HTTPException(
            status_code=404, detail="No NIFTY data for this date")

    nifty_df["time"] = pd.to_datetime(nifty_df["time"])
    nifty_df = nifty_df.sort_values("time")
    nifty_first = nifty_df.iloc[0]
    index_open = float(nifty_first["open"])

    # INDIA VIX 1-min
    vix_df = getindex1min(api, date, symboltoken=VIXINDEXTOKEN)
    if vix_df.empty:
        # NIFTY mil gaya, VIX nahi mila → sirf index_open bhej do
        return {
            "status": "partial",
            "date": date,
            "index_open": index_open,
            "vix": 0.0,
            "message": "No VIX data for this date",
        }

    vix_df["time"] = pd.to_datetime(vix_df["time"])
    vix_df = vix_df.sort_values("time")
    vix_first = vix_df.iloc[0]
    vix_open = float(vix_first["open"])

    return {
        "status": "ok",
        "date": date,
        "index_open": index_open,
        "vix": vix_open,
    }


# ========= BACKTEST REQUEST SAVE =========


def savebacktestrequest(req: VixRequest) -> None:
    """
    Har request ko backtests/<date>/<timestamp>_<C1>_<C2>_<RULE>.json me dump karo.
    """
    try:
        os.makedirs(BACKTESTDIR, exist_ok=True)
        d = req.date  # already YYYY-MM-DD
        daydir = os.path.join(BACKTESTDIR, d)
        os.makedirs(daydir, exist_ok=True)

        ts = datetime.now().strftime("%H%M%S")

        # V2 ke liye safe getattr, purane fields optional
        c1 = getattr(req, "candletype1", "") or getattr(
            req, "candletype", "") or ""
        c2 = getattr(req, "candletype2", "") or ""
        prof = getattr(req, "ruleprofile", "") or ""

        c1 = c1.upper()
        c2 = c2.upper()
        prof = prof.upper()

        tag = "_".join(x for x in (c1, c2, prof) if x)

        fname = f"{ts}_{tag}.json" if tag else f"{ts}.json"
        path = os.path.join(daydir, fname)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(req.dict(), f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Error saving backtest", e)


@app.post("/v2/vixbacktest")
def vixbacktest(req: VixV2Request) -> Dict[str, Any]:
    """
    V2 ORB+Gann backtest:
    - Android se sirf date + expiry aata hai (VixV2Request).
    - Yahan se hum internally VixRequest banate hain (dummy values),
      jo niche existing V2 engine (run_v2_orb_gann_backtest_logic) use karta hai.
    """
    dummy_side = {
        "level": 0.0,
        "t2": None,
        "t3": None,
        "t4": None,
        "sl": None,
    }

    v1req = VixRequest(
        candletype="NORMAL",
        open=0.0,
        vix=0.0,
        buy=dummy_side,
        sell=dummy_side,
        date=req.date,
        expiry=req.expiry,
        boside=None,
        bostart=None,
        lots=1,
        gapprevclose=None,
        gapatr=None,
        gapmode="OFF",
        borestrictside=None,
        borestrictuntil=None,
    )

    print("V2 RAW REQUEST BODY:", req.dict())
    print("V2 DERIVED V1 REQUEST:", v1req.dict())

    # Backtest request ko file me save karo
    savebacktestrequest(v1req)

    # SmartAPI login (config.py credentials)
    api = smartlogin()
    if api is None:
        return {
            "status": "error",
            "message": "SmartAPI login failed (Invalid TOTP credentials).",
        }

    # Fake AccountConfig jahan sirf name use ho raha hai
    try:
        fake_acc = AccountConfig(
            name="BACKTEST",
            apikey="",
            clientid="",
            password="",
            totpsecret="",
        )
    except TypeError:
        class DummyAcc:
            def __init__(self, name: str) -> None:
                self.name = name

        fake_acc = DummyAcc("BACKTEST")

    # Single source of truth: full engine yahin se chalega
    result = run_v2_orb_gann_backtest_logic(api, fake_acc, v1req)

    return result


@app.post("/v2/manual-test-trade")
def manual_test_trade():
    """
    Sirf testing ke liye: 
    - current NIFTY spot se nearest ATM PE lo
    - 1 lot BUY MARKET
    - turant SELL MARKET exit
    """

    # 1) Login using global config
    try:
        api = smartlogin_for_account(None)  # ya smartlogin_for_account()
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"SmartAPI login failed: {e}")

    try:
        # 2) Aaj ka NIFTY index data lo
        today = datetime.now().strftime("%Y-%m-%d")
        idxdf = getindex1min(api, today, symboltoken=NIFTYINDEXTOKEN)
        if idxdf.empty:
            raise HTTPException(
                status_code=500, detail="No index data for today")
        idxdf.index = pd.to_datetime(idxdf.index)
        last_close = float(idxdf["close"].iloc[-1])

        # 3) Nearest 50-strike PE
        strike = int(round(last_close / 50.0) * 50)

        # Yahan tumhari system ke hisaab se expiry string do:
        expiry = "INDEX"  # agar getoptiontoken INDEX handle karta hai
        # example: expiry = "20FEB2026"

        petoken = getoptiontoken(strike, expiry, "PE")
        if not petoken:
            raise HTTPException(
                status_code=500,
                detail=f"PE token not found for strike={strike} expiry={expiry}",
            )

        qty = 65  # NIFTY lot size 2026

        # 4) BUY MARKET
        buy_payload = {
            "variety": "NORMAL",
            "tradingsymbol": "NIFTY",
            "symboltoken": petoken,
            "transactiontype": "BUY",
            "exchange": "NFO",
            "ordertype": "MARKET",
            "producttype": "MIS",
            "duration": "DAY",
            "price": 0,
            "triggerprice": 0,
            "quantity": qty,
            "squareoff": 0,
            "stoploss": 0,
            "trailingStopLoss": 0,
        }
        print("[MANUAL BUY REQ]", buy_payload)
        buy_res = api.placeOrder(buy_payload)
        print("[MANUAL BUY RES]", buy_res)

        # 5) SELL MARKET (exit)
        sell_payload = buy_payload.copy()
        sell_payload["transactiontype"] = "SELL"
        print("[MANUAL SELL REQ]", sell_payload)
        sell_res = api.placeOrder(sell_payload)
        print("[MANUAL SELL RES]", sell_res)

        return {
            "status": "ok",
            "spot": last_close,
            "strike": strike,
            "expiry": expiry,
            "petoken": petoken,
            "buy_response": buy_res,
            "sell_response": sell_res,
        }

    finally:
        try:
            api.terminateSession(CLIENTID)
        except Exception:
            pass


@app.post("/mcx/manual-test-trade")
def mcx_manual_test_trade():
    """
    MCX crude mini ke liye simple:
    - FUT LTP lo
    - Option 1 lot BUY MARKET
    - Turant SELL MARKET exit
    """
    # 1) Account pick + login
    accounts = load_accounts()
    if not accounts:
        raise HTTPException(
            status_code=500, detail="No accounts in accountsconfig.json")
    acc = accounts[0]

    try:
        api = smartlogin_for_account(acc)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"SmartAPI login failed: {e}")

    try:
        fut_ltp = get_ltp_safe(api, "MCX", FUT_SYMBOL, FUT_TOKEN)
        if fut_ltp is None:
            raise HTTPException(
                status_code=500, detail="No FUT LTP for MCX crude")

        # BUY
        buy_res = place_mcx_market_order(
            api=api,
            token=OPTION_TOKEN,
            symbol=OPTION_SYMBOL,
            side="BUY",
            qty=MCX_QTY,
        )

        # turant SELL exit
        sell_res = place_mcx_market_order(
            api=api,
            token=OPTION_TOKEN,
            symbol=OPTION_SYMBOL,
            side="SELL",
            qty=MCX_QTY,
        )

        return {
            "status": "ok",
            "fut_ltp": fut_ltp,
            "fut_symbol": FUT_SYMBOL,
            "option_symbol": OPTION_SYMBOL,
            "option_token": OPTION_TOKEN,
            "qty": MCX_QTY,
            "buy_response": buy_res,
            "sell_response": sell_res,
        }
    finally:
        try:
            api.terminateSession(acc.clientid)
        except Exception:
            pass


# ========= SMARTAPI HELPERS (LOCAL, SAFE) =========

def place_market_order(
    api: SmartConnect,
    token: str,
    side: str,
    qty: int,
    target: Optional[float] = None,
    sl: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Simple NIFTY options MARKET order helper.
    SmartAPI official style ke according payload banata hai.
    """
    payload = {
        "variety": "NORMAL",
        "tradingsymbol": "NIFTY",      # tumhare token se expiry+strike map hoti hai
        "symboltoken": token,
        "transactiontype": side,       # "BUY" / "SELL"
        "exchange": "NFO",
        "ordertype": "MARKET",
        "producttype": "NRML",         # ya MIS agar tum intraday chahte ho
        "duration": "DAY",
        "quantity": qty,
        "price": "0",
        "triggerprice": "0",
        "squareoff": "0",
        "stoploss": "0",
    }

    print(f"[ORDER REQ] {side} {qty} @ {token} payload={payload}")
    try:
        res = api.placeOrder(payload)
        print(f"[ORDER RES] {side} {qty} @ {token}: {res}")

        # SmartAPI kabhi string order id, kabhi dict deta hai
        if isinstance(res, str):
            return {"status": True, "orderid": res}
        # dict me status flag nahi ho to bhi normalize kar lo
        if isinstance(res, dict) and "status" not in res:
            res = {"status": True, **res}
        return res
    except Exception as e:
        print(f"[ORDER ERROR] {side} {qty} @ {token}: {e}")
        return {"status": False, "message": str(e)}


def exit_position(
    api: SmartConnect,
    token: str,
    side: str,
    qty: int,
) -> Dict[str, Any]:
    """
    Existing position exit kare:
    - Agar entry BUY thi to yahan side='BUY' doge, ye SELL karega.
    - Agar entry SELL thi to yahan side='SELL', ye BUY karega.
    """
    payload = {
        "variety": "NORMAL",
        "tradingsymbol": "NIFTY",
        "symboltoken": token,
        "transactiontype": "SELL" if side == "BUY" else "BUY",
        "exchange": "NFO",
        "ordertype": "MARKET",
        "producttype": "NRML",
        "duration": "DAY",
        "quantity": qty,
        "price": "0",
        "triggerprice": "0",
        "squareoff": "0",
        "stoploss": "0",
    }

    print(f"[EXIT REQ] {side} {qty} @ {token} payload={payload}")
    try:
        res = api.placeOrder(payload)
        print(f"[EXIT RES] {side} {qty} @ {token}: {res}")
        if isinstance(res, str):
            return {"status": True, "orderid": res}
        if isinstance(res, dict) and "status" not in res:
            res = {"status": True, **res}
        return res
    except Exception as e:
        print(f"[EXIT ERROR] {side} {qty} @ {token}: {e}")
        return {"status": False, "message": str(e)}


def get_live_option_ltp(api: SmartConnect, token: str) -> float:
    """
    NIFTY option ka live LTP safely laata hai.
    Error pe 0.0 return.
    """
    try:
        data = api.ltpData("NFO", "NIFTY", token)
        ltp = float(data["data"]["ltp"])
        print(f"[LTP] NFO NIFTY {token} -> {ltp}")
        return ltp
    except Exception as e:
        print(f"[MANUAL LTP ERROR] {token} {e}")
        return 0.0


# ========= LIVE TRADE REQUEST + AUTO LIVE MODELS =========

class SimpleVixConfig(BaseModel):
    date: str    # "YYYY-MM-DD"
    expiry: str  # "INDEX" ya expiry date string


class LiveTradeSimpleRequest(BaseModel):
    account_name: str        # kaunse account se orders jayenge
    config: SimpleVixConfig  # sirf date + expiry app se


# ==== AUTO LIVE MODELS ====

class LiveScheduleRequest(BaseModel):
    date: str          # "YYYY-MM-DD" (trade date)
    expiry: str        # "INDEX" ya expiry date string
    accounts: List[str]
    mode: str = "AUTO_9_15"


class LiveScheduleCancel(BaseModel):
    schedule_id: str = "NIFTY_AUTO"


class LiveScheduleResponse(BaseModel):
    status: str
    message: str
    schedule_id: str | None = None
    run_at: str | None = None


# ========= REAL LIVE TRADE ENDPOINT (V2 ENGINE) =========
# 1) PURE STRATEGY HELPER (backtest logic yahan shift)


def run_v2_orb_gann_backtest_logic(
    api,
    acc,
    v1req: VixRequest,
) -> Dict[str, Any]:
    """
    Ye function purane /v2/live-trade ke andar ka pura backtest + decision logic chalata hai,
    bas SmartAPI order placement ya HTTPResponse nahi banata.
    Isko hum endpoint se bhi call karenge aur auto-live se bhi.
    """

    # -------- NIFTY + SENSEX 1-min DATA --------
    nifty_idxdf = getindex1min(api, v1req.date, symboltoken=NIFTYINDEXTOKEN)
    sensex_idxdf = getindex1min(api, v1req.date, symboltoken=SENSEXINDEXTOKEN)
    if nifty_idxdf.empty or sensex_idxdf.empty:
        return {"status": "error", "message": "No NIFTY/SENSEX data"}

    nifty_idxdf.index = pd.to_datetime(nifty_idxdf.index)
    sensex_idxdf.index = pd.to_datetime(sensex_idxdf.index)

    # -------- PREVIOUS DAY HIGH/LOW --------
    prevdaydata = get_previous_day_high_low(api, v1req.date)
    prevhigh = prevdaydata.get("prev_high") or prevdaydata.get("prevhigh")
    prevlow = prevdaydata.get("prev_low") or prevdaydata.get("prevlow")
    if not prevhigh or not prevlow:
        return {
            "status": "error",
            "message": "Previous day high/low data not available",
        }

    # -------- FULL GAP CHECK (9:15–9:30 vs prev H/L) --------
    gapinfo = detect_gap_day(nifty_idxdf, prevhigh, prevlow)
    if gapinfo["gap_type"] in ("GAP_UP", "GAP_DOWN"):
        return {
            "status": "GAP_DAY",
            "message": "GAP HUWA 1 HOUR MEIN JAO",
            "gapinfo": gapinfo,
        }

    # -------- HALF GAP CHECK (ATR 14 Angel) --------
    half_gap = detect_half_gap(api, nifty_idxdf, v1req.date)
    atr14 = float(half_gap.get("atr_14") or 0.0)

    # -------- ORB RANGE / ATR14 RATIO (10:00–10:15) --------
    orb_atr_info = detect_orb_atr_ratio(nifty_idxdf, atr14)
    high_vol_orb_range = orb_atr_info.get("is_high_vol", False)

    is_half_gap = half_gap.get("is_half_gap", False)
    half_gap_type = half_gap.get("half_gap_type")

    # Common full_idxdf
    full_idxdf = nifty_idxdf.copy()
    if not isinstance(full_idxdf.index, pd.DatetimeIndex):
        if "time" in full_idxdf.columns:
            full_idxdf["time"] = pd.to_datetime(full_idxdf["time"])
            full_idxdf = full_idxdf.set_index("time")

    # -------- PREV DAY TICK BREAKOUT TILL 13:30 --------
    trade_date = full_idxdf.index[0].date()
    cutoff_1330 = datetime.combine(
        trade_date, datetime.strptime("13:30", "%H:%M").time()
    )
    idx_till_1330 = full_idxdf[full_idxdf.index <= cutoff_1330]
    prev_break_till_1330 = check_breakout(idx_till_1330, prevhigh, prevlow)
    prev_break_flag_1330 = bool(prev_break_till_1330.get("breakout"))

    # -------- PREV DAY 15-MIN BREAKOUT --------
    idx15 = full_idxdf.resample("15min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    breakout_result = check_breakout(idx15, prevhigh, prevlow)
    prev_break_15_flag = bool(breakout_result.get("breakout"))
    breakout_type = breakout_result.get("breakouttype")
    breakout_time = breakout_result.get("breakouttime")
    breakout_price = breakout_result.get("breakoutprice")

    # -------- MID-DAY FORCE FLAG (RULE 2) --------
    use_midday_orb_only = False
    if not prev_break_flag_1330:
        use_midday_orb_only = True

    # -------- 10:00–10:15 ORB + MID-DAY FALLBACK --------
    orb_info = get_marking_and_trigger(
        full_idxdf)  # NEW 15-min + HIGH/LOW logic
    if orb_info["status"] == "ok" and not use_midday_orb_only:
        orb_mode = "MORNING"
    else:
        # MID-DAY ORB variant, same 15-min + HIGH/LOW style
        orb_info = get_midday_orb_breakout_15min(full_idxdf)
        if orb_info["status"] != "ok":
            return {
                "status": orb_info["status"],
                "message": orb_info.get("message", ""),
                "marked_high": orb_info.get("marked_high"),
                "marked_low": orb_info.get("marked_low"),
            }
        orb_mode = "MIDDAY"

    trigger_side = orb_info["trigger_side"]
    trigger_time = orb_info["trigger_time"]
    trigger_price = orb_info["trigger_price"]
    marked_high = orb_info["marked_high"]
    marked_low = orb_info["marked_low"]

    # -------- BREAKOUT 15-MIN CANDLE / ATR14 RATIO --------
    bo15_atr_info = detectbreakout15matrratio(idx15, trigger_time, atr14)
    high_vol_bo_candle = bo15_atr_info.get("ishighvol", False)
    high_vol_orb = high_vol_orb_range or high_vol_bo_candle

    (
        is_choti_day,
        orb_mode,
        trigger_side,
        trigger_time,
        trigger_price,
        marked_high,
        marked_low,
        boside_up_override,
    ) = apply_choti_rule(
        full_idxdf=full_idxdf,
        orb_mode=orb_mode,
        trigger_side=trigger_side,
        trigger_time=trigger_time,
        trigger_price=trigger_price,
        marked_high=marked_high,
        marked_low=marked_low,
        bo15_atr_info=bo15_atr_info,
    )

    if orb_mode == "MIDDAY" and trigger_time is None:
        orb_info = get_midday_orb_breakout_15min(full_idxdf)
        if orb_info.get("status") != "ok":
            return {"status": "CHOTI-MIDDAY-FAIL", "message": "No MIDDAY ORB after CHOTI"}
        trigger_side = orb_info["trigger_side"]
        trigger_time = orb_info["trigger_time"]
        trigger_price = orb_info["trigger_price"]
        marked_high = orb_info["marked_high"]
        marked_low = orb_info["marked_low"]

    if boside_up_override is not None:
        boside_up = "BUYBO" if boside_up_override else "SELLBO"

    # -------- GANN CMP (decimal ignore) --------
    cmp_for_gann = int(trigger_price)

    # -------- RULE TAGGING --------
    rule = "ATR_NORMAL"
    if is_half_gap:
        rule = "HALF_GAP"

    # MORNING vs MID-DAY Excel
    if orb_mode == "MIDDAY":
        excel_path = GANNEXCELPATH_MIDDAY
    else:
        excel_path = GANN_EXCEL_PATH

    try:
        levels = calc_gann_levels_with_excel(
            cmp_for_gann, side=trigger_side, excel_path=excel_path
        )
    except Exception as e:
        return {"status": "error", "message": f"GANN Excel error: {e}"}

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
    else:
        atr14_local = half_gap.get("atr_14", 0.0) or half_gap.get("atr14", 0.0)

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

    # -------- ENTRY WINDOW + BOSTART FILTER --------

    # Breakout ke 15 min baad se hi search
    trade_date2 = full_idxdf.index[0].date()

    # Breakout ke 15 min baad se hi search (default)
    if trigger_time is not None:
        entry_start_time = trigger_time + timedelta(minutes=15)
    else:
        # fallback: 10:15
        entry_start_time = datetime.combine(
            trade_date2, datetime.strptime("10:15", "%H:%M").time()
        )

    # MIDDAY ORB ke liye hard-coded fixed start time (e.g. 13:30)
    if orb_mode == "MIDDAY":
        entry_start_time = get_midday_entry_start(trade_date2)

    # Base DF from entry_start_time (NO 13:45 clamp)
    base_idxdf = full_idxdf[full_idxdf.index >= entry_start_time].copy()

    # -------- CHOTI DAY ENTRY WINDOWS (2h / 1h) --------
    if orb_mode == "MORNING" and is_choti_day and trigger_time is not None:
        timer_start = trigger_time  # 15-min candle close time
        # default 2h
        buy_window_end = timer_start + timedelta(hours=2)
        sell_window_end = timer_start + timedelta(hours=2)

        if trigger_side == "BUY":
            buy_window_end = timer_start + timedelta(hours=2)
            sell_window_end = timer_start + timedelta(hours=1, minutes=15)
        elif trigger_side == "SELL":
            sell_window_end = timer_start + timedelta(hours=2)
            buy_window_end = timer_start + timedelta(hours=1, minutes=15)

        idxdf_buy_window = base_idxdf[
            (base_idxdf.index >= entry_start_time)
            & (base_idxdf.index <= buy_window_end)
        ].copy()
        idxdf_sell_window = base_idxdf[
            (base_idxdf.index >= entry_start_time)
            & (base_idxdf.index <= sell_window_end)
        ].copy()

        print(
            "[CHOTI-WINDOW]",
            "trigger_side=", trigger_side,
            "start=", entry_start_time,
            "buy_end=", buy_window_end,
            "sell_end=", sell_window_end,
        )
    else:
        # Normal day: both legs full base_idxdf
        idxdf_buy_window = base_idxdf.copy()
        idxdf_sell_window = base_idxdf.copy()

    # DEFAULTBOSTART filter
    bot = DEFAULTBOSTART
    starttime = datetime.combine(base_idxdf.index[0].date(), bot)
    idxdf_buy_window = idxdf_buy_window[idxdf_buy_window.index >= starttime]
    idxdf_sell_window = idxdf_sell_window[idxdf_sell_window.index >= starttime]

    # -------- INDEX MODE --------
    expiry_up = (v1req.expiry or "").upper()
    index_mode = expiry_up == "INDEX"

    # -------- BOSIDE NORMALISATION --------
    rawboside = (v1req.boside or "").upper()
    rawboside_clean = rawboside.replace("_", "")
    if rawboside_clean in ("BUY", "BUYBO"):
        boside = "BUYBO"
    elif rawboside_clean in ("SELL", "SELLBO"):
        boside = "SELLBO"
    else:
        boside = "BUYBO"
    boside_up = boside.upper()

    buyentrylevel = v1req.buy.level
    sellentrylevel = v1req.sell.level

    # -------- BO RESTRICTION + ENTRY CANDLES --------
    idxdfbuy = applyborestriction(idxdf_buy_window, v1req, legside="BUYBO")
    idxdfsell = applyborestriction(idxdf_sell_window, v1req, legside="SELLBO")
    buyentrycandle = findentryidx(idxdfbuy, buyentrylevel)
    sellentrycandle = findentryidx(idxdfsell, sellentrylevel)
    print(
        "[ENTRY-RESULT-CHOTI]",
        "is_choti_day=", is_choti_day,
        "buytime=", getattr(buyentrycandle, "name", None),
        "selltime=", getattr(sellentrycandle, "name", None),
    )

    # CHOTI: agar dono side pe entry nahi mili window ke andar -> morning invalid
    if orb_mode == "MORNING" and is_choti_day:
        if buyentrycandle is None and sellentrycandle is None:
            return {
                "status": "MORNING_INVALID_NOENTRY_CHOTI",
                "message": "No entry within choti timer window, use MIDDAY ORB",
                "orb_mode": orb_mode,
                "marked_high": marked_high,
                "marked_low": marked_low,
            }

    buyresult: Dict[str, Any] = {"status": "NOENTRY"}
    sellresult: Dict[str, Any] = {"status": "NOENTRY"}
    lots = v1req.lots or 1

    # -------- OPTION DATA (ATM FROM GANN LEVELS) --------
    if not index_mode:
        ce_raw = v1req.buy.level
        pe_raw = v1req.sell.level

        def round_to_nearest_50(x: float) -> int:
            return int(round(x / 50.0) * 50)

        cestrike = round_to_nearest_50(ce_raw)
        pestrike = round_to_nearest_50(pe_raw)

        cetoken = getoptiontoken(cestrike, v1req.expiry, "CE")
        petoken = getoptiontoken(pestrike, v1req.expiry, "PE")
        movement = 0.0
        if not cetoken or not petoken:
            return {
                "status": "error",
                "message": "Token not found",
                "cetoken": cetoken,
                "petoken": petoken,
            }

        ceoptdf = getoption1min(api, cetoken, v1req.date)
        peoptdf = getoption1min(api, petoken, v1req.date)
        if ceoptdf.empty or peoptdf.empty:
            return {
                "status": "error",
                "message": "No option data for this date",
            }
    else:
        cestrike = 0
        pestrike = 0
        ceoptdf = base_idxdf
        peoptdf = base_idxdf
        movement = 0.0

    # -------- BUY SIDE PROCESS --------
    rule_for_buy = None
    if buyentrycandle is not None:
        buyentrytime = buyentrycandle.name
        if orb_mode == "MORNING" and buyentrytime.time() >= datetime.strptime(
            "13:30", "%H:%M"
        ).time():
            rule_for_buy = "ORB_LATE"
        else:
            rule_for_buy = rule

        buy_target = v1req.buy.t4 or 0.0
        if rule_for_buy == "ORB_LATE":
            buy_target = v1req.buy.t2

        used_optdf = ceoptdf if not index_mode else base_idxdf
        buyresult = processnormal(
            base_idxdf,
            used_optdf,
            buyentrytime,
            buyentrylevel,
            buy_target,
            v1req.buy.sl or 0.0,
            "BUY",
            lots,
            is_half_gap=(rule_for_buy == "HALF_GAP"),
            half_gap_type=half_gap_type,
            rule=rule_for_buy,
        )
        buyresult["entrytime"] = str(buyentrytime)
        buyresult["entryindex"] = float(buyentrycandle["close"])

    # -------- SELL SIDE PROCESS --------
    rule_for_sell = None
    if sellentrycandle is not None:
        sellentrytime = sellentrycandle.name
        if orb_mode == "MORNING" and sellentrytime.time() >= datetime.strptime(
            "13:30", "%H:%M"
        ).time():
            rule_for_sell = "ORB_LATE"
        else:
            rule_for_sell = rule

        sell_target = v1req.sell.t4 or 0.0
        if rule_for_sell == "ORB_LATE":
            sell_target = v1req.sell.t2

        used_optdf = peoptdf if not index_mode else base_idxdf
        sellresult = processnormal(
            base_idxdf,
            used_optdf,
            sellentrytime,
            sellentrylevel,
            sell_target,
            v1req.sell.sl or 0.0,
            "SELL",
            lots,
            is_half_gap=(rule_for_sell == "HALF_GAP"),
            half_gap_type=half_gap_type,
            rule=rule_for_sell,
        )
        sellresult["entrytime"] = str(sellentrytime)
        sellresult["entryindex"] = float(sellentrycandle["close"])

    # -------- PRIMARY LEG (direction) --------
    if boside_up == "BUYBO":
        primary = buyresult
        primary_side = "BUY"
    elif boside_up == "SELLBO":
        primary = sellresult
        primary_side = "SELL"
    else:
        if buyresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary = buyresult
            primary_side = "BUY"
        elif sellresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary = sellresult
            primary_side = "SELL"
        else:
            primary = {"status": "NOENTRY"}
            primary_side = "BUY"

    # -------- DETAILED RESULT FIELDS FOR APP (NEW) --------

    # Breakout / ORB info
    breakout_details: Dict[str, Any] = {
        # MORNING / MIDDAY context
        "orb_mode": orb_mode,  # "MORNING" or "MIDDAY"
        "trigger_side": trigger_side,
        "trigger_time": trigger_time.strftime("%H:%M") if trigger_time else None,
        "trigger_price": float(trigger_price) if trigger_price is not None else None,
        "marked_high": float(marked_high) if marked_high is not None else None,
        "marked_low": float(marked_low) if marked_low is not None else None,
        "is_choti_day": is_choti_day,
        "use_midday_orb_only": use_midday_orb_only,
    }

    # CHOTI specific metadata
    if is_choti_day:
        breakout_details["choti_reason"] = "BO15/ORB range ratio > 1.99"
        # NEW ORB high/low already in marked_high/marked_low
        if use_midday_orb_only:
            breakout_details[
                "shift_reason"
            ] = "CHOTI-NEW-ORB NO BREAKOUT TILL 12:00 → MIDDAY ORB USED"
        else:
            breakout_details["shift_reason"] = "CHOTI-NEW-ORB 15-min close breakout"

    # Gann mapping snapshot (CMP + levels from v1req after mapping)
    gann_details: Dict[str, Any] = {
        "cmp_at_trigger": int(trigger_price) if trigger_price is not None else None,
        "gann_rule": rule,  # "ATR_NORMAL" / "HALF_GAP"
        "high_vol_orb": high_vol_orb,
        "excel_mode": "MIDDAY" if orb_mode == "MIDDAY" else "MORNING",
        "buy": {
            "entry": float(v1req.buy.level or 0.0),
            "t2": float(getattr(v1req.buy, "t2", 0.0) or 0.0),
            "t4": float(getattr(v1req.buy, "t4", 0.0) or 0.0),
            "sl": float(v1req.buy.sl or 0.0),
        },
        "sell": {
            "entry": float(v1req.sell.level or 0.0),
            "t2": float(getattr(v1req.sell, "t2", 0.0) or 0.0),
            "t4": float(getattr(v1req.sell, "t4", 0.0) or 0.0),
            "sl": float(v1req.sell.sl or 0.0),
        },
    }

    # Rule tags list for UI (chips)
    rule_tags: List[str] = []
    if is_choti_day:
        rule_tags.append("CHOTI_DAY")
    if use_midday_orb_only or orb_mode == "MIDDAY":
        rule_tags.append("MIDDAY")
    if rule == "HALF_GAP":
        rule_tags.append("HALF_GAP")
    else:
        rule_tags.append("ATR_NORMAL")
    if high_vol_orb:
        rule_tags.append("HIGH_VOL_ORB")

    # Primary leg entry/exit compact view
    primary_entry_time: Optional[str] = None
    primary_entry_price: Optional[float] = None
    primary_exit_time: Optional[str] = None
    primary_exit_price: Optional[float] = None
    primary_exit_reason: Optional[str] = None
    primary_pnl: Optional[float] = None

    if primary_side == "BUY" and buyresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
        primary_entry_time = buyresult.get("entrytime")
        primary_entry_price = float(buyresult.get("entryindex", 0.0))
        primary_exit_time = buyresult.get("exittime")
        primary_exit_price = float(buyresult.get("exitindex", 0.0)) if buyresult.get(
            "exitindex") is not None else None
        primary_exit_reason = buyresult.get("exitreason")
        primary_pnl = float(buyresult.get("pnl", 0.0))
    elif primary_side == "SELL" and sellresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
        primary_entry_time = sellresult.get("entrytime")
        primary_entry_price = float(sellresult.get("entryindex", 0.0))
        primary_exit_time = sellresult.get("exittime")
        primary_exit_price = float(sellresult.get("exitindex", 0.0)) if sellresult.get(
            "exitindex") is not None else None
        primary_exit_reason = sellresult.get("exitreason")
        primary_pnl = float(sellresult.get("pnl", 0.0))

    trade_details: Dict[str, Any] = {
        "primary_side": primary_side,
        "entry_time": primary_entry_time,
        "entry_price": primary_entry_price,
        "exit_time": primary_exit_time,
        "exit_price": primary_exit_price,
        "exit_reason": primary_exit_reason,
        "pnl_points": primary_pnl,
    }

    return {
        "status": "ok",
        "account": acc.name,
        "v1req": v1req,
        "boside": boside,
        "boside_up": boside_up,
        "index_mode": index_mode,
        "buyresult": buyresult,
        "sellresult": sellresult,
        "primary": primary,
        "primary_side": primary_side,
        "cestrike": cestrike,
        "pestrike": pestrike,
        "cetoken": None if index_mode else cetoken,
        "petoken": None if index_mode else petoken,

        # existing:
        "is_choti_day": is_choti_day,
        "orb_mode": orb_mode,

        # NEW:
        "breakout_details": breakout_details,
        "gann_details": gann_details,
        "rule_tags": rule_tags,
        "trade_details": trade_details,
    }


# ===== SIMPLE LIVE HELPERS (ENTRY/EXIT) =====

def get_live_option_ltp(api, token: str) -> float:
    """SmartAPI se current LTP lao (simple version)."""
    try:
        data = api.ltpData("NFO", "NIFTY", token)
        # SmartAPI structure docs check karo, usually:
        # data['data']['ltp'] ya data['data']['last_price']
        ltp = float(data["data"].get("ltp") or data["data"].get("last_price"))
        return ltp
    except Exception as e:
        print(f"[LIVE] LTP error token={token}: {e}")
        return 0.0


def run_live_loop_for_account(api, acc, date: str, expiry: str):
    """
    Simple LIVE loop:
    - 9:15–15:30 ke beech har 60 sec me aaj tak ka index 1-min data fetch
    - run_v2_orb_gann_backtest_logic se levels nikaalo
    - entry condition aate hi 1 baar real order
    - target/SL hit hote hi 1 baar exit
    """
    trade_date = datetime.fromisoformat(date).date()
    lots = 1

    entry_taken = False
    exit_done = False
    primary_side = None
    primary_token = None

    print(f"[LIVE-LOOP] Started for {acc.name} date={date} expiry={expiry}")

    while True:
        now = datetime.now()
        # Market time window
        if now.time() < time(9, 15):
            # market open ka wait
            time.sleep(10)
            continue
        if now.time() > time(15, 30):
            print(f"[LIVE-LOOP] Market closed, stopping for {acc.name}")
            break

        # V1 request for today
        v1req = VixRequest(
            candletype="NORMAL",
            open=0.0,
            vix=0.0,
            buy=SideConfig(level=0.0),
            sell=SideConfig(level=0.0),
            date=date,
            expiry=expiry,
            boside=None,
            bostart=None,
            lots=lots,
            gapprevclose=None,
            gapatr=None,
            gapmode="OFF",
            borestrictside=None,
            borestrictuntil=None,
        )

        strat = run_v2_orb_gann_backtest_logic(api=api, acc=acc, v1req=v1req)

        if strat.get("status") not in ("ok", "GAP_DAY"):
            print(
                f"[LIVE-LOOP] strategy status={strat.get('status')} msg={strat.get('message')}")
            time.sleep(30)
            continue

        if strat["status"] == "GAP_DAY":
            print(f"[LIVE-LOOP] GAP DAY, skipping. msg={strat.get('message')}")
            break

        v1req = strat["v1req"]
        index_mode = strat["index_mode"]
        primary = strat["primary"]
        primary_side = strat["primary_side"]
        cetoken = strat["cetoken"]
        petoken = strat["petoken"]
        lots = v1req.lots or 1

        # Agar abhi tak entry nahi li aur primary ready hai
        if (not entry_taken) and primary.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            if not index_mode:
                primary_token = cetoken if primary_side == "BUY" else petoken
            else:
                primary_token = ""

            if not index_mode and primary_token:
                qty = lots * 65
                entryorder_res = place_market_order(
                    api, primary_token, primary_side, qty)
                print(
                    f"[LIVE-LOOP] ENTRY {primary_side} {qty} for {acc.name}: {entryorder_res}")
                entry_taken = True

            else:
                print(
                    f"[LIVE-LOOP] INDEX MODE, no option order for {acc.name}")
                entry_taken = True  # index mode, aage exit check skip

        # Agar entry ho chuki hai, exit check karo
        if entry_taken and not exit_done and (not index_mode) and primary_token:
            ltp = get_live_option_ltp(api, primary_token)
            if ltp <= 0:
                time.sleep(10)
                continue

            if primary_side == "BUY":
                tgt = v1req.buy.t4 or v1req.buy.t2 or 0.0
                sl = v1req.buy.sl or 0.0
                hit_target = tgt > 0 and ltp >= tgt
                hit_sl = sl > 0 and ltp <= sl
            else:
                tgt = v1req.sell.t4 or v1req.sell.t2 or 0.0
                sl = v1req.sell.sl or 0.0
                hit_target = tgt > 0 and ltp <= tgt
                hit_sl = sl > 0 and ltp >= sl

            print(
                f"[LIVE-LOOP] LTP={ltp} tgt={tgt} sl={sl} side={primary_side} hit_tgt={hit_target} hit_sl={hit_sl}")

            if hit_target or hit_sl:
                qty = lots * 65
                exitorder_res = exit_position(
                    api, primary_token, primary_side, qty)
                print(
                    f"[LIVE-LOOP] EXIT {primary_side} {qty} for {acc.name}: {exitorder_res}")
                exit_done = True
                break

        # Loop interval
        time.sleep(60)

    print(f"[LIVE-LOOP] Finished for {acc.name}")


def do_live_trade_for_account(account_name: str, date: str, expiry: str):
    """
    AUTO LIVE: schedule / manual dono ke liye
    - is account_name ka AccountConfig load karo
    - smartlogin_for_account(acc) se SmartConnect banao
    - run_v2_orb_gann_backtest_logic logic chalao
    - place_market_order / exit_position se real orders
    """
    print(
        f"[AUTO LIVE] Running for {account_name} date={date} expiry={expiry}")

    accounts = load_accounts()
    acc = next((a for a in accounts if a.name == account_name), None)
    if acc is None:
        print(f"[AUTO LIVE] Account {account_name} not found")
        return

    try:
        api = smartlogin_for_account(acc)
    except Exception as e:
        print(f"[AUTO LIVE] SmartAPI login failed: {e}")
        return

    try:
        v1req = VixRequest(
            candletype="NORMAL",
            open=0.0,
            vix=0.0,
            buy=SideConfig(level=0.0),
            sell=SideConfig(level=0.0),
            date=date,
            expiry=expiry,
            boside=None,
            bostart=None,
            lots=1,
            gapprevclose=None,
            gapatr=None,
            gapmode="OFF",
            borestrictside=None,
            borestrictuntil=None,
        )

        strat = run_v2_orb_gann_backtest_logic(api=api, acc=acc, v1req=v1req)

        if strat.get("status") not in ("ok", "GAP_DAY"):
            print(
                f"[AUTO LIVE] Strategy status={strat.get('status')} msg={strat.get('message')}")
            return

        if strat["status"] == "GAP_DAY":
            print(f"[AUTO LIVE] GAP DAY: {strat.get('message')}")
            return

        v1req = strat["v1req"]
        index_mode = strat["index_mode"]
        primary = strat["primary"]
        primary_side = strat["primary_side"]
        cetoken = strat["cetoken"]
        petoken = strat["petoken"]
        lots = v1req.lots or 1

        if primary.get("status") in (None, "NOENTRY", "NO_ENTRY"):
            print(f"[AUTO LIVE] NO ENTRY for {account_name}")
            return

        if not index_mode:
            primary_token = cetoken if primary_side == "BUY" else petoken
        else:
            primary_token = ""

        if not index_mode and primary_token:
            qty = lots * 65
            entryorder_res = place_market_order(
                api=api,
                tradingsymbol="NIFTY",
                symboltoken=primary_token,
                transactiontype=primary_side,
                quantity=qty,
                exchange="NFO",
                product="NRML",
                ordertype="MARKET",
                variety="NORMAL",
            )
            print(
                f"[AUTO LIVE] ENTRY {primary_side} {qty} for {account_name}: {entryorder_res}")

            exitorder_res = exit_position(
                api, primary_token, primary_side, qty)
            print(
                f"[AUTO LIVE] EXIT {primary_side} {qty} for {account_name}: {exitorder_res}")

            print(
                f"[AUTO LIVE] INDEX MODE, no option order for {account_name}")

    finally:
        try:
            api.terminateSession(acc.clientid)
        except Exception:
            pass


def round_to_nearest_50(x: float) -> int:
    return int(round(x / 50.0) * 50)


@app.post("/manual-override")
def manual_override(req: ManualOverrideRequest) -> Dict[str, Any]:
    """
    Manual multi-strike override:
    - buy_prices:  [26000, 26100]  -> CE BUY
    - sell_prices: [25000]         -> PE SELL
    - har strike ke liye market entry, fir background loop T/SL check karega
    """
    accounts = load_accounts()
    acc = next((a for a in accounts if a.name == req.accountname), None)
    if acc is None:
        return {"status": "error", "message": "Account not found"}

    try:
        api = smartlogin_for_account(acc)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"SmartAPI login failed: {e}"
        )

    qty = 65 * req.lots
    positions: list[dict] = []

    try:
        # BUY side (CE)
        for i, raw_price in enumerate(req.buy_prices or []):
            strike = round_to_nearest_50(raw_price)
            token = getoptiontoken(strike, req.expiry, "CE")
            if not token:
                print(
                    f"[MANUAL] CE token not found {raw_price} -> {strike} {req.expiry}")
                continue

            # simple 4‑arg call
            entry_res = place_market_order(api, token, "BUY", qty)

            target = req.buy_targets[i] if i < len(req.buy_targets) else None
            sl = None
            if req.buy_sls and i < len(req.buy_sls):
                sl = req.buy_sls[i]

            positions.append({
                "strike": strike,
                "token": token,
                "side": "BUY",
                "target": target,
                "sl": sl,
                "qty": qty,
                "entry_order": entry_res,
            })

        # SELL side (PE)
        for i, raw_price in enumerate(req.sell_prices or []):
            strike = round_to_nearest_50(raw_price)
            token = getoptiontoken(strike, req.expiry, "PE")
            if not token:
                print(
                    f"[MANUAL] PE token not found {raw_price} -> {strike} {req.expiry}")
                continue

            # simple 4‑arg call
            entry_res = place_market_order(api, token, "SELL", qty)

            target = req.sell_targets[i] if i < len(req.sell_targets) else None
            sl = None
            if req.sell_sls and i < len(req.sell_sls):
                sl = req.sell_sls[i]

            positions.append({
                "strike": strike,
                "token": token,
                "side": "SELL",
                "target": target,
                "sl": sl,
                "qty": qty,
                "entry_order": entry_res,
            })

        MANUAL_POSITIONS[req.accountname] = positions
        print(
            f"[MANUAL] Stored {len(positions)} positions for {req.accountname}")

        return {
            "status": "ok",
            "account": req.accountname,
            "positions": positions,
        }
    finally:
        try:
            api.terminateSession(acc.clientid)
        except Exception:
            pass


@app.post("/v2/live-trade")
def live_trade(req: LiveTradeSimpleRequest) -> Dict[str, Any]:
    """
    V2 ORB+Gann backtest-engine based LIVE trade:
    - req.config = SimpleVixConfig (sirf date + expiry app se)
    - Backend v2 backtest engine ka flow follow karega,
      sirf last step par option side pe live orders place karega.
    """

    # 1) Account resolve
    accounts = load_accounts()
    acc = next((a for a in accounts if a.name == req.account_name), None)
    if acc is None:
        raise HTTPException(
            status_code=400, detail=f"Account {req.account_name} not found")

    # SimpleVixConfig -> full VixRequest
    simple = req.config  # SimpleVixConfig

    v1req = VixRequest(
        candletype="NORMAL",
        open=0.0,
        vix=0.0,
        buy=SideConfig(level=0.0),
        sell=SideConfig(level=0.0),
        date=simple.date,
        expiry=simple.expiry,
        boside=None,
        bostart=None,
        lots=1,
        gapprevclose=None,
        gapatr=None,
        gapmode="OFF",
        borestrictside=None,
        borestrictuntil=None,
    )

    # 2) SmartAPI login
    try:
        api = smartlogin_for_account(acc)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"SmartAPI login failed: {e}")

    try:
        # -------- PURE STRATEGY CALL --------
        strat = run_v2_orb_gann_backtest_logic(api=api, acc=acc, v1req=v1req)

        if strat.get("status") not in ("ok", "GAP_DAY"):
            return strat

        if strat["status"] == "GAP_DAY":
            return strat

        v1req = strat["v1req"]
        boside = strat["boside"]
        boside_up = strat["boside_up"]
        index_mode = strat["index_mode"]
        buyresult = strat["buyresult"]
        sellresult = strat["sellresult"]
        primary = strat["primary"]
        primary_side = strat["primary_side"]
        cestrike = strat["cestrike"]
        pestrike = strat["pestrike"]
        cetoken = strat["cetoken"]
        petoken = strat["petoken"]
        lots = v1req.lots or 1

        if primary.get("status") in (None, "NOENTRY", "NO_ENTRY"):
            return {
                "status": "STANDBY",
                "message": f"No entry for direction {primary_side}",
                "buy": buyresult,
                "sell": sellresult,
                "index_mode": index_mode,
            }

        # -------- LIVE ORDER EXECUTION --------
        if not index_mode:
            primary_token = cetoken if primary_side == "BUY" else petoken
        else:
            primary_token = ""

        entrytime_str = primary.get("entrytime")
        exittime_str = primary.get("exittime") or primary.get("exittime")
        entryprice = primary.get("entry") or primary.get("entryprice")
        exitprice = primary.get("exit") or primary.get("exitprice")
        pnl = primary.get("pnl")
        reason = primary.get("status")

        entryorder_res: Dict[str, Any] = {}
        exitorder_res: Dict[str, Any] = {}

        if not index_mode and primary_token:
            qty = lots * 65
            entryorder_res = place_market_order(
                api=api,
                tradingsymbol="NIFTY",
                symboltoken=primary_token,
                transactiontype=primary_side,
                quantity=qty,
                exchange="NFO",
                product="NRML",
                ordertype="MARKET",
                variety="NORMAL",
            )
            exitorder_res = exit_position(
                api, primary_token, primary_side, qty)

        return {
            "status": "ok",
            "account": acc.name,
            "boside": boside,
            "trade_side": primary_side,
            "index_mode": index_mode,
            "cestrike": cestrike,
            "pestrike": pestrike,
            "entrytime": entrytime_str,
            "exittime": exittime_str,
            "entryprice": entryprice,
            "exitprice": exitprice,
            "pnl": pnl,
            "reason": reason,
            "entryorder": entryorder_res,
            "exitorder": exitorder_res,
            "buy": buyresult,
            "sell": sellresult,
        }
    finally:
        try:
            api.terminateSession(acc.clientid)
        except Exception:
            pass


def execute_live_job(schedule_id: str):
    cfg = SCHEDULE_STORE.get(schedule_id)
    if not cfg or not cfg.get("armed"):
        print(f"[AUTO LIVE] schedule {schedule_id} not armed, skipping.")
        return

    date = cfg["date"]
    expiry = cfg["expiry"]
    accounts = cfg["accounts"]

    print(
        f"[AUTO LIVE] starting for schedule={schedule_id}, date={date}, expiry={expiry}, accounts={accounts}")

    for acc_name in accounts:
        try:
            do_live_trade_for_account(acc_name, date=date, expiry=expiry)
        except Exception as e:
            print(f"[AUTO LIVE] error for account={acc_name}: {e}")


MCX_SCHEDULE_ID = "MCX_AUTO"


def execute_mcx_job():
    """
    Simple MCX crude mini auto job:
    - account login
    - FUT LTP
    - option 1 lot BUY + turant SELL
    """
    accounts = load_accounts()
    if not accounts:
        print("[MCX_AUTO] No accounts found in accountsconfig.json")
        return

    acc = accounts[0]
    print(f"[MCX_AUTO] Using account: {acc.name}")

    try:
        api = smartlogin_for_account(acc)
    except Exception as e:
        print(f"[MCX_AUTO] login failed: {e}")
        return

    try:
        fut_ltp = get_ltp_safe(api, "MCX", FUT_SYMBOL, FUT_TOKEN)
        if fut_ltp is None:
            print("[MCX_AUTO] No FUT LTP for MCX crude")
            return

        buy_res = place_mcx_market_order(
            api=api,
            token=OPTION_TOKEN,
            symbol=OPTION_SYMBOL,
            side="BUY",
            qty=MCX_QTY,
        )
        sell_res = place_mcx_market_order(
            api=api,
            token=OPTION_TOKEN,
            symbol=OPTION_SYMBOL,
            side="SELL",
            qty=MCX_QTY,
        )

        print("[MCX_AUTO] DONE", {
            "fut_ltp": fut_ltp,
            "buy_res": buy_res,
            "sell_res": sell_res,
        })
    finally:
        try:
            api.terminateSession(acc.clientid)
        except Exception:
            pass


# ==== AUTO LIVE ENDPOINTS ====

class McxScheduleRequest(BaseModel):
    run_time: str  # "22:15"


@app.post("/mcx/schedule-auto")
def mcx_schedule_auto(req: McxScheduleRequest):
    """
    run_time: "21:00" (24h, IST) – aaj ya kal ke liye MCX_AUTO ek baar fire karega.
    """
    today = datetime.now().date()
    hour, minute = map(int, req.run_time.split(":"))
    run_dt = datetime.combine(today, time(hour=hour, minute=minute))

    now = datetime.now(run_dt.tzinfo)
    if run_dt <= now:
        run_dt = datetime.combine(
            today + timedelta(days=1), time(hour=hour, minute=minute))

    try:
        scheduler.remove_job(MCX_SCHEDULE_ID)
    except Exception:
        pass

    trigger = DateTrigger(run_date=run_dt)
    scheduler.add_job(
        execute_mcx_job,
        trigger,
        id=MCX_SCHEDULE_ID,
        replace_existing=True,
    )

    print(f"[MCX_AUTO] scheduled at {run_dt.isoformat()}")
    return {
        "status": "ok",
        "schedule_id": MCX_SCHEDULE_ID,
        "run_at": run_dt.isoformat(),
    }


@app.post("/v2/schedule-live", response_model=LiveScheduleResponse)
def schedule_live(req: LiveScheduleRequest):
    """
    Raat ko call: given date ke liye 9:15 pe auto V2 live chalao.
    """

    schedule_id = "NIFTY_AUTO"

    # parse date
    trade_date = datetime.fromisoformat(req.date).date()
    run_dt = datetime.combine(trade_date, time(hour=9, minute=15))

    now = datetime.now(run_dt.tzinfo)
    if run_dt <= now:
        return LiveScheduleResponse(
            status="error",
            message="run time is in the past",
            schedule_id=schedule_id,
            run_at=None,
        )

    # delete old job if exists
    try:
        scheduler.remove_job(schedule_id)
    except Exception:
        pass

    trigger = DateTrigger(run_date=run_dt)
    scheduler.add_job(
        execute_live_job,
        trigger,
        id=schedule_id,
        args=[schedule_id],
        replace_existing=True,
    )

    SCHEDULE_STORE[schedule_id] = {
        "date": req.date,
        "expiry": req.expiry,
        "accounts": req.accounts,
        "mode": req.mode,
        "run_at": run_dt.isoformat(),
        "armed": True,
    }

    return LiveScheduleResponse(
        status="ok",
        message="scheduled",
        schedule_id=schedule_id,
        run_at=run_dt.isoformat(),
    )


@app.post("/v2/cancel-schedule", response_model=LiveScheduleResponse)
def cancel_schedule(req: LiveScheduleCancel):
    schedule_id = req.schedule_id

    try:
        scheduler.remove_job(schedule_id)
    except Exception:
        pass

    if schedule_id in SCHEDULE_STORE:
        SCHEDULE_STORE[schedule_id]["armed"] = False

    return LiveScheduleResponse(
        status="ok",
        message="cancelled",
        schedule_id=schedule_id,
        run_at=SCHEDULE_STORE.get(schedule_id, {}).get("run_at"),
    )
