from target_rules import get_atr_multiplier, classify_engine
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
from half_gap_rule import (
    detect_half_gap,
    get_angel_atr_14,
    atr_tradingview_style,
    detect_hook_930_exact,
)
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
    HOOK_DETECTION_TIME,
    BREAKOUT_WAIT_MINUTES,
)
from strategy import (
    findentryidx,
    applyborestriction,
    processnormal,
)
from smartapi_helpers import (
    getindex1min,
    get_orb_breakout_15min,
    get_midday_orb_breakout_15min,
    getoptiontoken,
    getoption1min,
    calc_atm_strikes,
    get_previous_day_high_low,
    check_breakout,
)
from models import (
    AccountConfig,
    LiveAccountsPayload,
    SideConfig,
    VixRequest,
    SimpleVixConfig,
    LiveTradeSimpleRequest,
)

from typing import Dict, Any, List, Optional, Literal
from pathlib import Path
import sys
import os
import json
import logging

import pandas as pd
import numpy as np
import openpyxl  # Gann Excel ke liye
import xlwings as xw
import pyotp

from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from datetime import datetime, timedelta, time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger

from SmartApi import SmartConnect
from SmartApi import smartConnect as smart_module
import SmartApi.smartConnect as smart_mod

from expiry_store import refresh_and_get_expiries
from jumpback_rule import decide_orb_or_jumpback
from price_rounding import round_index_price_for_side
from bot3_high_vol_rule import (
    run_bot3_high_vol_strategy,
    run_bot3_entry_engine,
)
from trading_state import bot_state

from config import BOT3_HIGH_VOL_THRESHOLD

from bot3_high_vol_rule import run_bot3_high_vol_strategy

from trade_state_engine import TradingState
from order_engine import place_option_buy

from logging.handlers import RotatingFileHandler

trading_state = TradingState()


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


# ---- Shared log file path ----
LOG_FILE_PATH = Path("/home/ubuntu/logs/vix.log")
LOG_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)


@app.get("/admin/poll")
def poll_status():
    """
    Light polling endpoint for app.
    Can show server time, last manual monitor run, etc.
    """
    return {
        "status": "OK",
        "server_time": datetime.now().isoformat(timespec="seconds"),
        "last_manual_monitor": LAST_MANUAL_MONITOR_RUN,
    }


@app.get("/admin/logs", response_class=PlainTextResponse)
async def get_logs(lines: int = 300):
    """
    Last N lines of log file as plain text.
    """
    if not LOG_FILE_PATH.exists():
        return PlainTextResponse("No logs yet.")

    try:
        with LOG_FILE_PATH.open("r", errors="ignore") as f:
            all_lines = f.readlines()

        tail = all_lines[-lines:]
        text = "".join(tail)
        if not text:
            return PlainTextResponse("No logs yet.")
        return PlainTextResponse(text)
    except Exception as e:
        return PlainTextResponse(f"Error reading logs: {e}")


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

    # ---- BOT-3 HIGH-VOL GATE ----
    if is_bot3_high_vol_day(api, v1req.date):
        print("[BOT3] High-vol day detected → Bot-3 ONLY mode")

        nifty_idxdf = getindex1min(
            api, v1req.date, symboltoken=NIFTYINDEXTOKEN)
        if nifty_idxdf.empty:
            return {"status": "error", "message": "No NIFTY data for Bot-3"}
        nifty_idxdf.index = pd.to_datetime(nifty_idxdf.index)
        full_idxdf = nifty_idxdf.copy()

        daily_df = (
            full_idxdf
            .resample("1D")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
            .reset_index()
        )
        daily_df = daily_df.rename(columns={"index": "date"})

        bot3_result = run_bot3_high_vol_strategy(
            idxdf_daily=daily_df,
            idxdf_1min=full_idxdf,
            trade_date=v1req.date,
            gann_levels={},  # engine andar se JSON se lega
        )

        return {
            "status": bot3_result.get("status", "BOT3_DONE"),
            "mode": "BOT3_ONLY",
            "bot3": bot3_result,
        }

    # ---- NORMAL FLOW (Bot-1 / Bot-2 / 9:15 etc.) ----
    result = run_v2_orb_gann_backtest_logic(api, fake_acc, v1req)

    if result.get("status") == "JUMP_TO_915_ORB":
        jump_decision_time = result.get("jump_decision_time")
        print(
            "[V2-ENDPOINT] Switching to 9:15 ORB bot, fallback_after_time=",
            jump_decision_time,
        )
        result = run_915_orb_gann_backtest_logic(
            api,
            fake_acc,
            v1req,
            fallback_after_time=jump_decision_time,
        )

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
    symbol: str,
    side: str,
    qty: int,
) -> Dict[str, Any]:

    payload = {
        "variety": "NORMAL",
        "tradingsymbol": symbol,  # FIXED (no hardcoded NIFTY)
        "symboltoken": token,
        "transactiontype": "SELL" if side == "BUY" else "BUY",
        "exchange": "NFO",
        "ordertype": "MARKET",
        "producttype": "INTRADAY",  # FIXED
        "duration": "DAY",
        "quantity": qty,
        "price": 0,
        "squareoff": 0,
        "stoploss": 0,
    }

    print(f"[EXIT REQ] {side} {qty} {symbol}")

    try:
        res = api.placeOrder(payload)
        print(f"[EXIT RES] {res}")
        return {"status": True, "orderid": res}
    except Exception as e:
        print(f"[EXIT ERROR] {e}")
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


def get_nifty_daily_history_for_atr(api: SmartConnect, trade_date: str, lookback_days: int = 40) -> Optional[pd.DataFrame]:
    """
    NIFTY ka 1D OHLC history SmartAPI se laata hai,
    ATR regime filter ke liye.
    trade_date: "YYYY-MM-DD" (current test day)
    """
    try:
        end_dt = datetime.strptime(trade_date, "%Y-%m-%d").date()
        start_dt = end_dt - timedelta(days=lookback_days)

        params = {
            "exchange": "NSE",
            "symboltoken": NIFTYINDEXTOKEN,  # 99926000
            "interval": "ONE_DAY",
            "fromdate": f"{start_dt} 09:15",
            "todate": f"{end_dt} 15:30",
        }
        resp = api.getCandleData(params)

        # YEH NAYA DEBUG PRINT
        print("[ATR-DAILY-RAW]", resp.get("status"),
              len(resp.get("data") or []))

        if not resp.get("status") or not resp.get("data"):
            print("[ATR-DAILY] No daily data for ATR regime:", resp)
            return None

        df = pd.DataFrame(
            resp["data"],
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["open"] = df["open"].astype(float)
        df["high"] = df["high"].astype(float)
        df["low"] = df["low"].astype(float)
        df["close"] = df["close"].astype(float)
        df["date"] = pd.to_datetime(df["timestamp"]).dt.date

        # AUR YEH NAYA DEBUG PRINT
        print("[ATR-DAILY-DF-TAIL]")
        print(df[["date", "high", "low", "close"]].tail(5))

        return df[["date", "open", "high", "low", "close"]]
    except Exception as e:
        print(f"[ATR-DAILY-ERROR] {e}")
        return None


def calculate_daily_atr_and_ratio(idxdf_daily, period=14):
    """
    idxdf_daily: daily NIFTY DF with columns: ['date','high','low','close']
    Return: PREVIOUS TRADING DAY row with atr & ratio (current day nahi)
    """
    df = idxdf_daily.copy()

    if df.empty:
        return None

    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()

    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(window=period).mean()
    df['range'] = df['high'] - df['low']
    df['ratio'] = df['range'] / df['atr']

    df = df.dropna(subset=['ratio'])
    if df.empty:
        return None

    # PREVIOUS TRADING DAY return karo (trade_date ke pehle wala)
    if len(df) > 1:
        return df.iloc[-2]  # second-last row
    return df.iloc[-1]  # agar sirf ek row hai


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

def is_bot3_high_vol_day(api, trade_date) -> bool:
    """
    Prev day HL/ATR > BOT3_HIGH_VOL_THRESHOLD?
    True → Bot-3 only day.
    """
    daily_hist = get_nifty_daily_history_for_atr(api, trade_date)
    if daily_hist is None:
        print("[BOT3-HIGHVOL] daily_hist is None")
        return False

    prev_row = calculate_daily_atr_and_ratio(daily_hist)
    if prev_row is None:
        print("[BOT3-HIGHVOL] insufficient daily history")
        return False

    prev_high = float(prev_row["high"])
    prev_low = float(prev_row["low"])
    prev_atr = float(prev_row["atr"])
    prev_range = prev_high - prev_low
    prev_ratio = float(prev_row["ratio"])

    print(
        f"[BOT3-HIGHVOL] prev_date={prev_row['date']} "
        f"range={prev_range:.2f} atr14={prev_atr:.2f} ratio={prev_ratio:.2f} "
        f"threshold={BOT3_HIGH_VOL_THRESHOLD}"
    )

    return prev_ratio > BOT3_HIGH_VOL_THRESHOLD


def run_v2_orb_gann_backtest_logic(
    api,
    acc,
    v1req: VixRequest,
) -> Dict[str, Any]:
    """
    10:00 ORB + Gann backtest logic.
    BACKTEST MODE me yahan 9:30 hook/unhook detection bhi hota hai.
    """

    # ===== BACKTEST HOOK / UNHOOK LOGIC =====
    bot_state.hook_detected = False
    bot_state.is_hooked = False

    hook_info: Dict[str, Any] = {}

    print("[HOOK] BACKTEST hook detection start")
    hook_info = detect_hook_930_exact(api, v1req.date)
    bot_state.hook_detected = True
    bot_state.is_hooked = hook_info.get("is_hooked", True)
    bot_state.breakout_level = hook_info.get("breakout_level", 0.0)
    print("[HOOK] result:", hook_info)

    # Sirf log karo, return mat karo - baaki flow normal chalega
    if hook_info.get("hook_status") == "UNHOOKED":
        print(
            f"[HOOK] UNHOOKED DAY detected: gap_dir={hook_info.get('gap_direction')} "
            f"dist={hook_info.get('distance_rs')} Rs – "
            "normal ORB+Gann flow continue karega"
        )

    # -------- NIFTY + SENSEX 1-min DATA --------
    nifty_idxdf = getindex1min(api, v1req.date, symboltoken=NIFTYINDEXTOKEN)
    sensex_idxdf = getindex1min(api, v1req.date, symboltoken=SENSEXINDEXTOKEN)
    if nifty_idxdf.empty or sensex_idxdf.empty:
        return {"status": "error", "message": "No NIFTY/SENSEX data"}

    nifty_idxdf.index = pd.to_datetime(nifty_idxdf.index)
    sensex_idxdf.index = pd.to_datetime(sensex_idxdf.index)

    # -------- DAILY DF FROM 1-MIN (for ATR regime) --------
    daily_df = (
        nifty_idxdf
        .resample("1D")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
        .reset_index()
    )
    daily_df = daily_df.rename(columns={"index": "date"})

    is_high_vol_day = False  # FLAG

    # DAILY DF ATR block ko sirf logging rakho:
    prev_row_df = calculate_daily_atr_and_ratio(daily_df)
    if prev_row_df is not None:
        prev_ratio_df = float(prev_row_df["ratio"])
        print(f"[ATR REGIME] prev HL/ATR = {prev_ratio_df:.2f}")
    else:
        print("[ATR REGIME] skip: insufficient daily data for ATR")

    # -------- ATR REGIME USING DAILY HISTORY (NEW) – LOG + FALLBACK FLAG --------
    daily_hist = get_nifty_daily_history_for_atr(api, v1req.date)
    if daily_hist is None:
        print("[ATR-REGIME-DAILY] daily_hist is None")
    else:
        print("[ATR-REGIME-DAILY] rows:", len(daily_hist))
        prev_row = calculate_daily_atr_and_ratio(daily_hist)
        if prev_row is not None:
            prev_high = float(prev_row["high"])
            prev_low = float(prev_row["low"])
            prev_atr = float(prev_row["atr"])
            prev_range = prev_high - prev_low
            prev_ratio = float(prev_row["ratio"])
            print(
                f"[ATR-REGIME-DAILY] prev_date={prev_row['date']} "
                f"range={prev_range:.2f} atr14={prev_atr:.2f} ratio={prev_ratio:.2f}"
            )
        else:
            print("[ATR-REGIME-DAILY] skip: insufficient daily history")

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

    print(
        "[PREV-DAY-1330]",
        "break_flag=", prev_break_flag_1330,
        "break_info=", prev_break_till_1330,
    )

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
    # Ab 13:30 wala flag sirf info ke liye hai, MIDDAY force nahi kar rahe
    use_midday_orb_only = False

    # -------- 10:00–10:15 ORB + MID-DAY FALLBACK --------
    orb_info = get_marking_and_trigger(full_idxdf)
    if orb_info["status"] == "ok" and not use_midday_orb_only:
        orb_mode = "MORNING"
    else:
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

    # ====== JUMP BACK DECISION ON 10:00 ORB ======
    if orb_mode == "MORNING" and trigger_time is not None:
        jb = decide_orb_or_jumpback(
            full_idxdf=full_idxdf,
            prev_high=prevhigh,
            prev_low=prevlow,
            orb_break_time=trigger_time,
        )
        print(
            "[JUMPBACK-DECISION]",
            "mode=", jb["mode"],
            "reason=", jb["reason"],
        )

        if jb["mode"] == "JUMP_BACK":
            # Bot-1 ka decision time ko next 15-min bucket pe round karo
            dt = trigger_time
            minute_bucket = (dt.minute // 15 + 1) * 15
            if minute_bucket >= 60:
                dt = dt.replace(hour=dt.hour + 1, minute=0,
                                second=0, microsecond=0)
            else:
                dt = dt.replace(minute=minute_bucket, second=0, microsecond=0)

            fb_str = dt.strftime("%H:%M")

            print(
                "[MAIN-ORB-JUMP-BACK]",
                "10:00 ORB breakout ke waqt prev day HL nahi toota, "
                "is bot me trade nahi lenge",
            )
            return {
                "status": "JUMP_TO_915_ORB",
                "message": (
                    "10:00 ORB breakout ke waqt prev day H/L break nahi hua, "
                    "9:15 ORB bot use karo"
                ),
                "date": str(v1req.date),
                "prev_high": float(prevhigh),
                "prev_low": float(prevlow),
                "ten_am_orb_marked_high": float(marked_high),
                "ten_am_orb_marked_low": float(marked_low),
                "jump_orb_high": float(jb["jump_orb_high"]),
                "jump_orb_low": float(jb["jump_orb_low"]),
                "jump_break_time": str(jb["jump_break_time"]),
                "jump_break_dir": jb["jump_break_dir"],
                # Bot-1 ka decision time (rounded to next 15-min candle)
                "jump_decision_time": fb_str,
            }
        else:
            print(
                "[MAIN-ORB-OK]",
                "Using 10:00 ORB breakout (prev day HL already broken before ORB)",
            )
    # ====== END JUMP BACK DECISION ======

    # -------- BREAKOUT 15-MIN CANDLE / ATR14 RATIO --------
    bo15_atr_info = detectbreakout15matrratio(idx15, trigger_time, atr14)
    high_vol_bo_candle = bo15_atr_info.get("is_high_vol", False)
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
            return {
                "status": "CHOTI-MIDDAY-FAIL",
                "message": "No MIDDAY ORB after CHOTI",
            }
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
        # ===== ATR_NORMAL WITH ENGINE-WISE MULTIPLIER =====
        atr14_local = half_gap.get("atr_14", 0.0) or half_gap.get("atr14", 0.0)

        # Engine classify: MIDDAY / NORMAL / CHOTI / HIGH_VOL (915 & 10:00 ORB same bucket)
        engine = classify_engine(orb_mode, is_choti_day, high_vol_orb)

        # ATR multiplier rule per engine + hook status
        atr_mult = get_atr_multiplier(
            engine, bot_state.is_hooked, float(atr14_local))

        def pick_buy_t4_from_atr(base_entry: float) -> float:
            if atr14_local <= 0:
                return cut_dec(levels["buy_t4"])
            raw_target = base_entry + (atr14_local * atr_mult)
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
            raw_target = base_entry - (atr14_local * atr_mult)
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

    # -------- BOT-3 GANN LEVELS (raw) --------
    bot3_gann_levels = {
        "buy_entry":  float(levels.get("buy_entry",  0.0)),
        "buy_t15":    float(levels.get("buy_t15",    0.0)),
        "buy_t2":     float(levels.get("buy_t2",     0.0)),
        "buy_t25":    float(levels.get("buy_t25",    0.0)),
        "buy_t3":     float(levels.get("buy_t3",     0.0)),
        "buy_t35":    float(levels.get("buy_t35",    0.0)),
        "buy_t4":     float(levels.get("buy_t4",     0.0)),
        "sell_entry": float(levels.get("sell_entry", 0.0)),
        "sell_t15":   float(levels.get("sell_t15",   0.0)),
        "sell_t2":    float(levels.get("sell_t2",    0.0)),
        "sell_t25":   float(levels.get("sell_t25",   0.0)),
        "sell_t3":    float(levels.get("sell_t3",    0.0)),
        "sell_t35":   float(levels.get("sell_t35",   0.0)),
        "sell_t4":    float(levels.get("sell_t4",    0.0)),
    }

    # -------- ATR REGIME USING DAILY HISTORY (NEW) – LOG ONLY --------
    daily_hist = get_nifty_daily_history_for_atr(api, v1req.date)
    if daily_hist is None:
        print("[ATR-REGIME-DAILY] daily_hist is None")
    else:
        print("[ATR-REGIME-DAILY] rows:", len(daily_hist))
        prev_row = calculate_daily_atr_and_ratio(daily_hist)
        if prev_row is not None:
            prev_high = float(prev_row["high"])
            prev_low = float(prev_row["low"])
            prev_atr = float(prev_row["atr"])
            prev_range = prev_high - prev_low
            prev_ratio = float(prev_row["ratio"])

            print(
                f"[ATR-REGIME-DAILY] prev_date={prev_row['date']} "
                f"range={prev_range:.2f} atr14={prev_atr:.2f} ratio={prev_ratio:.2f}"
            )
        else:
            print("[ATR-REGIME-DAILY] skip: insufficient daily history")

    # -------- ENTRY WINDOW + BOSTART FILTER --------
    trade_date2 = full_idxdf.index[0].date()

    if trigger_time is not None:
        entry_start_time = trigger_time + timedelta(minutes=15)
    else:
        entry_start_time = datetime.combine(
            trade_date2, datetime.strptime("10:15", "%H:%M").time()
        )

    if orb_mode == "MIDDAY":
        entry_start_time = get_midday_entry_start(trade_date2)

    base_idxdf = full_idxdf[full_idxdf.index >= entry_start_time].copy()

    # -------- UNHOOKED DAY OPP LEG 1-HOUR DELAY --------
    # Default: no extra delay
    buy_window_start = entry_start_time
    sell_window_start = entry_start_time

    # Sirf UNHOOKED din pe (hooked ka koi special rule nahi)
    if not bot_state.is_hooked and trigger_time is not None and orb_mode == "MORNING":
        # Breakout side = primary; opposite side ke liye 1 hour wait
        opp_delay_start = trigger_time + timedelta(hours=1)

        if trigger_side == "BUY":
            # Primary = BUY, Opp = SELL -> SELL window 1 hour baad se
            sell_window_start = max(entry_start_time, opp_delay_start)
        elif trigger_side == "SELL":
            # Primary = SELL, Opp = BUY -> BUY window 1 hour baad se
            buy_window_start = max(entry_start_time, opp_delay_start)

        print(
            "[UNHOOK-OPP-DELAY]",
            "is_hooked=", bot_state.is_hooked,
            "trigger_side=", trigger_side,
            "trigger_time=", trigger_time,
            "buy_start=", buy_window_start,
            "sell_start=", sell_window_start,
        )

    # -------- CHOTI DAY ENTRY WINDOWS (2h / 1h) --------
    if orb_mode == "MORNING" and is_choti_day and trigger_time is not None:
        timer_start = trigger_time
        buy_window_end = timer_start + timedelta(hours=2)
        sell_window_end = timer_start + timedelta(hours=2)

        if trigger_side == "BUY":
            buy_window_end = timer_start + timedelta(hours=2)
            sell_window_end = timer_start + timedelta(hours=1, minutes=15)
        elif trigger_side == "SELL":
            sell_window_end = timer_start + timedelta(hours=2)
            buy_window_end = timer_start + timedelta(hours=1, minutes=15)

        idxdf_buy_window = base_idxdf[
            (base_idxdf.index >= buy_window_start)
            & (base_idxdf.index <= buy_window_end)
        ].copy()
        idxdf_sell_window = base_idxdf[
            (base_idxdf.index >= sell_window_start)
            & (base_idxdf.index <= sell_window_end)
        ].copy()

        print(
            "[CHOTI-WINDOW]",
            "trigger_side=", trigger_side,
            "start=", entry_start_time,
            "buy_start=", buy_window_start,
            "sell_start=", sell_window_start,
            "buy_end=", buy_window_end,
            "sell_end=", sell_window_end,
        )
    else:
        idxdf_buy_window = base_idxdf[base_idxdf.index >=
                                      buy_window_start].copy()
        idxdf_sell_window = base_idxdf[base_idxdf.index >=
                                       sell_window_start].copy()

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

        # Expiry ko clean date string ensure karo (YYYY-MM-DD)
        expiry_raw = (v1req.expiry or "").strip()
        # Agar galti se "CE"/"PE" aa gaya ho to usko ignore karo
        if expiry_raw in ("CE", "PE"):
            print("WARNING: v1req.expiry was",
                  expiry_raw, "resetting to trade date")
            expiry_raw = v1req.date  # fallback: index trade date

        cetoken = getoptiontoken(cestrike, expiry_raw, "CE")
        petoken = getoptiontoken(pestrike, expiry_raw, "PE")

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
    breakout_details: Dict[str, Any] = {
        "orb_mode": orb_mode,
        "trigger_side": trigger_side,
        "trigger_time": trigger_time.strftime("%H:%M") if trigger_time else None,
        "trigger_price": float(trigger_price) if trigger_price is not None else None,
        "marked_high": float(marked_high) if marked_high is not None else None,
        "marked_low": float(marked_low) if marked_low is not None else None,
        "is_choti_day": is_choti_day,
        "use_midday_orb_only": use_midday_orb_only,
    }

    if is_choti_day:
        breakout_details["choti_reason"] = "BO15/ORB range ratio > 1.99"
        if use_midday_orb_only:
            breakout_details[
                "shift_reason"
            ] = "CHOTI-NEW-ORB NO BREAKOUT TILL 12:00 → MIDDAY ORB USED"
        else:
            breakout_details["shift_reason"] = "CHOTI-NEW-ORB 15-min close breakout"

    gann_details: Dict[str, Any] = {
        "cmp_at_trigger": int(trigger_price) if trigger_price is not None else None,
        "gann_rule": rule,
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

    POINT_VALUE_INDEX = 65
    try:
        qty_lots = v1req.lots
    except AttributeError:
        qty_lots = v1req.get("lots", 1)

    primary_entry_time: Optional[str] = None
    primary_entry_price: Optional[float] = None
    primary_exit_time: Optional[str] = None
    primary_exit_price: Optional[float] = None
    primary_exit_reason: Optional[str] = None
    primary_pnl: Optional[float] = None

    if index_mode:
        if primary_side == "BUY" and buyresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary_entry_time = buyresult.get("entrytime")
            primary_entry_price = float(buyresult.get("entryindex", 0.0))
            primary_exit_time = buyresult.get("exittime")
            primary_exit_price = float(buyresult.get("exitindex", 0.0)) if buyresult.get(
                "exitindex"
            ) is not None else None
            primary_exit_reason = buyresult.get("exitreason")

            if primary_exit_price is not None:
                points = primary_exit_price - primary_entry_price
                primary_pnl = points * POINT_VALUE_INDEX * qty_lots

        elif primary_side == "SELL" and sellresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary_entry_time = sellresult.get("entrytime")
            primary_entry_price = float(sellresult.get("entryindex", 0.0))
            primary_exit_time = sellresult.get("exittime")
            primary_exit_price = float(sellresult.get("exitindex", 0.0)) if sellresult.get(
                "exitindex"
            ) is not None else None
            primary_exit_reason = sellresult.get("exitreason")

            if primary_exit_price is not None:
                points = primary_entry_price - primary_exit_price
                primary_pnl = points * POINT_VALUE_INDEX * qty_lots

    else:
        if primary_side == "BUY" and buyresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary_entry_time = buyresult.get("entrytime")
            primary_entry_price = float(buyresult.get("entryindex", 0.0))
            primary_exit_time = buyresult.get("exittime")
            primary_exit_price = float(buyresult.get("exitindex", 0.0)) if buyresult.get(
                "exitindex"
            ) is not None else None
            primary_exit_reason = buyresult.get("exitreason")
            primary_pnl = float(buyresult.get("pnl", 0.0))

        elif primary_side == "SELL" and sellresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary_entry_time = sellresult.get("entrytime")
            primary_entry_price = float(sellresult.get("entryindex", 0.0))
            primary_exit_time = sellresult.get("exittime")
            primary_exit_price = float(sellresult.get("exitindex", 0.0)) if sellresult.get(
                "exitindex"
            ) is not None else None
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
    # -------- ATR DETAILS FOR JSON OUTPUT --------
    # Safe defaults agar atr14_local / atr_mult / prev_ratio scope me na ho
    try:
        atr_15 = float(atr14_local)
    except Exception:
        atr_15 = float(half_gap.get("atr_14", 0.0) or 0.0)

    atr_mult_used = float(atr_mult) if "atr_mult" in locals() else 1.0
    engine_class = engine if "engine" in locals() else orb_mode

    try:
        prev_ratio_val = float(prev_ratio)
    except Exception:
        prev_ratio_val = 0.0

    atr_details = {
        "atr_15min": atr_15,
        "atr_multiplier_used": atr_mult_used,
        "engine_classified": engine_class,
        "day_hook_status": bool(getattr(bot_state, "is_hooked", False)),
        "prev_day_regime_ratio": prev_ratio_val,
        "half_gap_detected": bool(is_half_gap),
    }

    # Nested ATR info inside trade_details as well (optional)
    trade_details["atr_info"] = atr_details

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
        "is_choti_day": is_choti_day,
        "orb_mode": orb_mode,
        "breakout_details": breakout_details,
        "gann_details": gann_details,
        "rule_tags": rule_tags,
        "atr_info": atr_details,      # ← yeh line
        "trade_details": trade_details,
    }


def run_915_orb_gann_backtest_logic(
    api,
    acc,
    v1req: VixRequest,
    fallback_after_time: Optional[str] = None,  # "HH:MM" -> bot-1 JUMP time
) -> Dict[str, Any]:
    """
    9:15–9:30 ORB based Gann bot:
    - 9:15 ki 15-min ORB mark
    - us ORB ka 15-min close-based breakout (09:30 se aage)
    - Gann + HALF_GAP / ATR_NORMAL mapping same as 10AM bot
    - CHOTI & 10:00 ORB rules yahan nahi lagenge
    """

    # -------- NIFTY 1-min DATA --------
    nifty_idxdf = getindex1min(api, v1req.date, symboltoken=NIFTYINDEXTOKEN)
    if nifty_idxdf.empty:
        return {"status": "error", "message": "No NIFTY data"}

    nifty_idxdf.index = pd.to_datetime(nifty_idxdf.index)
    full_idxdf = nifty_idxdf.copy()

    # -------- HALF GAP + ATR14 (Angel) --------
    half_gap = detect_half_gap(api, full_idxdf, v1req.date)
    atr14 = float(half_gap.get("atr_14") or 0.0)
    is_half_gap = half_gap.get("is_half_gap", False)
    half_gap_type = half_gap.get("half_gap_type")

    # 9:15 bot pe HALF_GAP day invalid
    if is_half_gap:
        return {
            "status": "INVALID_915_HALF_GAP",
            "message": "9:15 ORB bot disabled on HALF-GAP day, trade mat dekh (jaise 30 JAN 2026).",
            "half_gap_type": half_gap_type,
            "atr_14": atr14,
        }

    # -------- 9:15–9:30 ORB MARKING (15-min) --------
    trade_date = full_idxdf.index[0].date()
    start_915 = datetime.combine(
        trade_date, datetime.strptime("09:15", "%H:%M").time()
    )
    end_930 = datetime.combine(
        trade_date, datetime.strptime("09:30", "%H:%M").time()
    )

    orb_915 = full_idxdf[(full_idxdf.index >= start_915)
                         & (full_idxdf.index < end_930)]
    if orb_915.empty:
        return {"status": "error", "message": "No 9:15–9:30 data"}

    marked_high = float(orb_915["high"].max())
    marked_low = float(orb_915["low"].min())

    print(
        "[915-ORB-MARK]",
        "high=", marked_high,
        "low=", marked_low,
    )

    # -------- 15-MIN CLOSE-BASED BREAKOUT (from 09:30 onwards) --------
    trigger_window = full_idxdf[full_idxdf.index >= end_930]
    if trigger_window.empty:
        return {
            "status": "NO_TRIGGER_WINDOW_915",
            "message": "No data after 9:30 for 9:15 ORB breakout",
            "marked_high": marked_high,
            "marked_low": marked_low,
        }

    agg_dict = {"open": "first", "high": "max", "low": "min", "close": "last"}
    trigger_df15m = trigger_window.resample("15min").agg(agg_dict).dropna()

    trigger_side = None
    trigger_time = None
    trigger_price = None

    for ts, row in trigger_df15m.iterrows():
        close_price = float(row["close"])
        high_price = float(row["high"])
        low_price = float(row["low"])
        print(
            f"[915-ORB-15M-CHECK] {ts} close {close_price} high {high_price} low {low_price}"
        )

        if close_price > marked_high:
            trigger_side = "BUY"
            trigger_time = ts
            trigger_price = round_index_price_for_side(close_price, "BUY")
            break

        if close_price < marked_low:
            trigger_side = "SELL"
            trigger_time = ts
            trigger_price = round_index_price_for_side(close_price, "SELL")
            break

    if trigger_time is None:
        return {
            "status": "NO_BREAK_915_ORB",
            "message": "No 9:15 ORB 15-min close breakout till end of window",
            "marked_high": marked_high,
            "marked_low": marked_low,
        }

    print(
        "[915-ORB-BREAK]",
        "time=", trigger_time,
        "side=", trigger_side,
        "trigger_price=", trigger_price,
    )

    # -------- RULE TAGGING --------
    rule = "ATR_NORMAL"
    # (HALF_GAP yahan kabhi true nahi aayega, upar hi exit kar rahe)

    # -------- GANN CMP (decimal ignore) --------
    cmp_for_gann = int(trigger_price)
    orb_mode = "ORB_915"  # UI ke liye tag

    # MORNING Excel hi use hoga
    excel_path = GANN_EXCEL_PATH

    try:
        levels = calc_gann_levels_with_excel(
            cmp_for_gann, side=trigger_side, excel_path=excel_path
        )
    except Exception as e:
        return {"status": "error", "message": f"GANN Excel error (915 ORB): {e}"}

    # -------- HIGH-VOL FLAG (simple) --------
    high_vol_orb = False

    # -------- GANN MAPPING (ATR_NORMAL only) --------
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

    # -------- ENTRY WINDOW + BOSTART (no CHOTI) --------
    trade_date2 = full_idxdf.index[0].date()

    # Breakout ke 15 min baad
    logical_start = trigger_time + timedelta(minutes=15)

    # Fallback: bot-1 JUMP ke baad hi entry search start
    if fallback_after_time:
        fb_dt = datetime.combine(
            trade_date2,
            datetime.strptime(fallback_after_time, "%H:%M").time(),
        )
        entry_start_time = max(logical_start, fb_dt)
    else:
        entry_start_time = logical_start

    base_idxdf = full_idxdf[full_idxdf.index >= entry_start_time].copy()

    idxdf_buy_window = base_idxdf.copy()
    idxdf_sell_window = base_idxdf.copy()

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
        "[915-ENTRY-RESULT]",
        "buytime=", getattr(buyentrycandle, "name", None),
        "selltime=", getattr(sellentrycandle, "name", None),
    )

    buyresult: Dict[str, Any] = {"status": "NOENTRY"}
    sellresult: Dict[str, Any] = {"status": "NOENTRY"}
    lots = v1req.lots or 1

    # -------- OPTION / INDEX DATA SELECT --------
    if not index_mode:
        ce_raw = v1req.buy.level
        pe_raw = v1req.sell.level

        def round_to_nearest_50(x: float) -> int:
            return int(round(x / 50.0) * 50)

        cestrike = round_to_nearest_50(ce_raw)
        pestrike = round_to_nearest_50(pe_raw)

        # Expiry ko clean date string ensure karo (YYYY-MM-DD)
        expiry_raw = (v1req.expiry or "").strip()
        # Agar galti se "CE"/"PE" aa gaya ho to trade date use karo
        if expiry_raw in ("CE", "PE", ""):
            print("WARNING: v1req.expiry was", repr(
                expiry_raw), "resetting to trade date")
            expiry_raw = v1req.date

        cetoken = getoptiontoken(cestrike, expiry_raw, "CE")
        petoken = getoptiontoken(pestrike, expiry_raw, "PE")
    else:
        cestrike = 0
        pestrike = 0
        cetoken = None
        petoken = None

    if not index_mode:
        ceoptdf = getoption1min(api, cetoken, v1req.date)
        peoptdf = getoption1min(api, petoken, v1req.date)
        if ceoptdf.empty or peoptdf.empty:
            return {
                "status": "error",
                "message": "No option data for this date",
            }
    else:
        ceoptdf = base_idxdf
        peoptdf = base_idxdf

    # -------- BUY SIDE PROCESS --------
    rule_for_buy = None
    if buyentrycandle is not None:
        buyentrytime = buyentrycandle.name
        rule_for_buy = rule

        buy_target = v1req.buy.t4 or 0.0

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
            is_half_gap=False,
            half_gap_type=None,
            rule=rule_for_buy,
        )
        buyresult["entrytime"] = str(buyentrytime)
        buyresult["entryindex"] = float(buyentrycandle["close"])

    # -------- SELL SIDE PROCESS --------
    rule_for_sell = None
    if sellentrycandle is not None:
        sellentrytime = sellentrycandle.name
        rule_for_sell = rule

        sell_target = v1req.sell.t4 or 0.0

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
            is_half_gap=False,
            half_gap_type=None,
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

    # -------- SUMMARY FIELDS --------
    breakout_details: Dict[str, Any] = {
        "orb_mode": orb_mode,
        "trigger_side": trigger_side,
        "trigger_time": trigger_time.strftime("%H:%M") if trigger_time else None,
        "trigger_price": float(trigger_price) if trigger_price is not None else None,
        "marked_high": float(marked_high),
        "marked_low": float(marked_low),
        "is_choti_day": False,
        "use_midday_orb_only": False,
    }

    gann_details: Dict[str, Any] = {
        "cmp_at_trigger": int(trigger_price) if trigger_price is not None else None,
        "gann_rule": rule,
        "high_vol_orb": high_vol_orb,
        "excel_mode": "MORNING",
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

    rule_tags: List[str] = []
    rule_tags.append("ATR_NORMAL")
    rule_tags.append("ORB_915")

    POINT_VALUE_INDEX = 65
    try:
        qty_lots = v1req.lots
    except AttributeError:
        qty_lots = v1req.get("lots", 1)

    primary_entry_time: Optional[str] = None
    primary_entry_price: Optional[float] = None
    primary_exit_time: Optional[str] = None
    primary_exit_price: Optional[float] = None
    primary_exit_reason: Optional[str] = None
    primary_pnl: Optional[float] = None

    if index_mode:
        if primary_side == "BUY" and buyresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary_entry_time = buyresult.get("entrytime")
            primary_entry_price = float(buyresult.get("entryindex", 0.0))
            primary_exit_time = buyresult.get("exittime")
            primary_exit_price = float(buyresult.get("exitindex", 0.0)) if buyresult.get(
                "exitindex"
            ) is not None else None
            primary_exit_reason = buyresult.get("exitreason")
            if primary_exit_price is not None:
                points = primary_exit_price - primary_entry_price
                primary_pnl = points * POINT_VALUE_INDEX * qty_lots
        elif primary_side == "SELL" and sellresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary_entry_time = sellresult.get("entrytime")
            primary_entry_price = float(sellresult.get("entryindex", 0.0))
            primary_exit_time = sellresult.get("exittime")
            primary_exit_price = float(sellresult.get("exitindex", 0.0)) if sellresult.get(
                "exitindex"
            ) is not None else None
            primary_exit_reason = sellresult.get("exitreason")
            if primary_exit_price is not None:
                points = primary_entry_price - primary_exit_price
                primary_pnl = points * POINT_VALUE_INDEX * qty_lots
    else:
        if primary_side == "BUY" and buyresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary_entry_time = buyresult.get("entrytime")
            primary_entry_price = float(buyresult.get("entryindex", 0.0))
            primary_exit_time = buyresult.get("exittime")
            primary_exit_price = float(buyresult.get("exitindex", 0.0)) if buyresult.get(
                "exitindex"
            ) is not None else None
            primary_exit_reason = buyresult.get("exitreason")
            primary_pnl = float(buyresult.get("pnl", 0.0))
        elif primary_side == "SELL" and sellresult.get("status") not in (None, "NOENTRY", "NO_ENTRY"):
            primary_entry_time = sellresult.get("entrytime")
            primary_entry_price = float(sellresult.get("entryindex", 0.0))
            primary_exit_time = sellresult.get("exittime")
            primary_exit_price = float(sellresult.get("exitindex", 0.0)) if sellresult.get(
                "exitindex"
            ) is not None else None
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
        "is_choti_day": False,
        "orb_mode": orb_mode,
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

    trade_date = datetime.fromisoformat(date).date()
    lots = 1

    print(f"[LIVE-LOOP] Started for {acc.name} date={date} expiry={expiry}")

    while True:
        now = datetime.now()

        trading_state.check_date_reset()

        # -------- HOOK LOGIC --------
        if (not bot_state.hook_detected
                and now.time() >= HOOK_DETECTION_TIME):
            print("[HOOK] LIVE 9:30 hook detection start")
            hook_info = detect_hook_930_exact(api, date)
            bot_state.hook_detected = True
            bot_state.is_hooked = hook_info.get("is_hooked", False)
            bot_state.breakout_level = hook_info.get("breakout_level", 0.0)
            print("[HOOK] LIVE result:", hook_info)

        if bot_state.hook_detected and not bot_state.is_hooked:
            time.sleep(60)
            continue

        # -------- Market Time Check --------
        if now.time() < time(9, 15):
            time.sleep(10)
            continue

        if now.time() > time(15, 30):
            print(f"[LIVE-LOOP] Market closed for {acc.name}")
            break

        # -------- Strategy Call --------
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
            time.sleep(30)
            continue

        if strat["status"] == "GAP_DAY":
            break

        v1req = strat["v1req"]
        index_mode = strat["index_mode"]
        primary = strat["primary"]
        primary_side = strat["primary_side"]
        cetoken = strat["cetoken"]
        petoken = strat["petoken"]
        lots = v1req.lots or 1

        # -------- ENTRY --------
        if primary.get("status") not in (None, "NOENTRY", "NO_ENTRY"):

            if not index_mode:

                primary_token = cetoken if primary_side == "BUY" else petoken
                qty = lots * 65

                if trading_state.can_enter(primary_side):

                    trading_state.mark_entry(
                        primary_side,
                        entry_price=0,
                        symbol=primary_token
                    )

                    entry_res = place_option_buy(
                        api=api,
                        symbol=primary_token,
                        token=primary_token,
                        quantity=qty
                    )

                    print(f"[LIVE ENTRY] {primary_side} {qty}: {entry_res}")

                    if not entry_res["status"]:
                        trading_state.active_position = None

        # -------- EXIT --------
        if trading_state.active_position and not index_mode:

            primary_token = trading_state.option_symbol
            ltp = get_live_option_ltp(api, primary_token)

            if ltp > 0:

                if trading_state.active_position == "BUY":
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
                    f"[LIVE EXIT CHECK] LTP={ltp} tgt={tgt} sl={sl}"
                )

                if hit_target or hit_sl:

                    qty = lots * 65

                    exit_res = exit_position(
                        api=api,
                        token=primary_token,
                        symbol=primary_token,
                        side=trading_state.active_position,
                        qty=qty
                    )

                    print(f"[LIVE EXIT] {exit_res}")

                    if exit_res["status"]:
                        trading_state.mark_exit()

        time.sleep(60)

    print(f"[LIVE-LOOP] Finished for {acc.name}")


def do_live_trade_for_account(account_name: str, date: str, expiry: str):

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
        trading_state.check_date_reset()

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
            print(f"[AUTO LIVE] Strategy status={strat.get('status')}")
            return

        if strat["status"] == "GAP_DAY":
            print(f"[AUTO LIVE] GAP DAY")
            return

        v1req = strat["v1req"]
        index_mode = strat["index_mode"]
        primary = strat["primary"]
        primary_side = strat["primary_side"]
        cetoken = strat["cetoken"]
        petoken = strat["petoken"]
        lots = v1req.lots or 1

        if primary.get("status") in (None, "NOENTRY", "NO_ENTRY"):
            print(f"[AUTO LIVE] NO ENTRY")
            return

        if index_mode:
            print("[AUTO LIVE] INDEX MODE — no option trade")
            return

        primary_token = cetoken if primary_side == "BUY" else petoken
        qty = lots * 65

        # ---- SAFE ENTRY ----
        if trading_state.can_enter(primary_side):

            trading_state.mark_entry(
                primary_side,
                entry_price=0,
                symbol=primary_token
            )

            entry_res = place_option_buy(
                api=api,
                symbol=primary_token,
                token=primary_token,
                quantity=qty
            )

            print(f"[AUTO LIVE ENTRY] {primary_side} {qty}: {entry_res}")

            if not entry_res["status"]:
                trading_state.active_position = None
                return

        else:
            print(
                f"[AUTO LIVE] Direction already traded today: {primary_side}")
            return

        # NOTE:
        # Exit yaha nahi karenge.
        # Exit live loop ya SL/Target watcher karega.

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

    accounts = load_accounts()
    acc = next((a for a in accounts if a.name == req.account_name), None)

    if acc is None:
        raise HTTPException(
            status_code=400,
            detail=f"Account {req.account_name} not found"
        )

    simple = req.config

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

    try:
        api = smartlogin_for_account(acc)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"SmartAPI login failed: {e}"
        )

    try:
        trading_state.check_date_reset()

        strat = run_v2_orb_gann_backtest_logic(api=api, acc=acc, v1req=v1req)

        if strat.get("status") not in ("ok", "GAP_DAY"):
            return strat

        if strat["status"] == "GAP_DAY":
            return strat

        v1req = strat["v1req"]
        index_mode = strat["index_mode"]
        primary = strat["primary"]
        primary_side = strat["primary_side"]
        cetoken = strat["cetoken"]
        petoken = strat["petoken"]
        lots = v1req.lots or 1

        if primary.get("status") in (None, "NOENTRY", "NO_ENTRY"):
            return {
                "status": "STANDBY",
                "message": f"No entry for {primary_side}"
            }

        if index_mode:
            return {
                "status": "INDEX_MODE",
                "message": "No option order in index mode"
            }

        primary_token = cetoken if primary_side == "BUY" else petoken
        qty = lots * 65

        # -------- SAFE ENTRY CONTROL --------
        if not trading_state.can_enter(primary_side):
            return {
                "status": "BLOCKED",
                "message": f"{primary_side} already traded today"
            }

        trading_state.mark_entry(
            primary_side,
            entry_price=0,
            symbol=primary_token
        )

        entryorder_res = place_option_buy(
            api=api,
            symbol=primary_token,
            token=primary_token,
            quantity=qty
        )

        if not entryorder_res["status"]:
            trading_state.active_position = None
            return {
                "status": "ENTRY_FAILED",
                "details": entryorder_res
            }

        return {
            "status": "ENTRY_PLACED",
            "account": acc.name,
            "trade_side": primary_side,
            "token": primary_token,
            "qty": qty,
            "entryorder": entryorder_res
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


if __name__ == "__main__":
    do_live_trade_for_account(
        account_name="MAIN",        # ya dusra naam agar woh use karna ho
        date="2026-02-24",
        expiry="2026-02-24",
    )
