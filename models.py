from pydantic import BaseModel
from typing import Optional, List

# =============== ACCOUNT CONFIG (MULTI-ACCOUNT) ===============

class AccountConfig(BaseModel):
    name: str
    apikey: str
    clientid: str
    password: str
    totpsecret: str
    base_lot: int = 1
    max_risk_pct: float = 1.0
    min_cash_buffer: float = 2000


class LiveAccountsPayload(BaseModel):
    accounts: List[AccountConfig]


# =============== CORE VIX STRATEGY MODELS (BACKTEST, INTERNAL) ===============

class SideConfig(BaseModel):
    level: float
    t2: Optional[float] = None
    t3: Optional[float] = None
    t4: Optional[float] = None
    sl: Optional[float] = None


class VixRequest(BaseModel):
    candletype: str
    open: float
    vix: float
    buy: SideConfig
    sell: SideConfig
    date: str
    expiry: str
    boside: Optional[str] = None
    bostart: Optional[str] = None
    lots: int = 1
    gapprevclose: Optional[float] = None
    gapatr: Optional[float] = None
    gapmode: str = "OFF"
    borestrictside: Optional[str] = None
    borestrictuntil: Optional[str] = None


# =============== SIMPLE LIVE TRADE MODELS (APP JSON) ===============

class SimpleVixConfig(BaseModel):
    date: str       # trading date YYYY-MM-DD
    expiry: str     # option expiry YYYY-MM-DD, ya "INDEX"


class LiveTradeSimpleRequest(BaseModel):
    account_name: str       # app yahi field bhej raha hai
    config: SimpleVixConfig # sirf date + expiry
