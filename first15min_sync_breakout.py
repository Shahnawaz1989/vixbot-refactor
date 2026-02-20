"""
First 15-min candle sync breakout filter for NIFTY + SENSEX
15-min timeframe only, 9:15-9:30 first candle high/low
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import pandas as pd

def resample_to_15min(idxdf_1min: pd.DataFrame) -> pd.DataFrame:
    """1-min df ko 15-min OHLCV candles banao"""
    idxdf_1min = idxdf_1min.copy()

    # agar 'time' column hai to use index bana do
    if "time" in idxdf_1min.columns:
        idxdf_1min["time"] = pd.to_datetime(idxdf_1min["time"])
        idxdf_1min = idxdf_1min.set_index("time")
    else:
        idxdf_1min.index = pd.to_datetime(idxdf_1min.index)

    ohlcv_15min = idxdf_1min.resample("15min").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna()

    return ohlcv_15min


def get_first15min_range(idxdf_15min: pd.DataFrame) -> Dict[str, float]:
    """9:15-9:30 first candle ka high/low"""
    first_candle = idxdf_15min.iloc[0]
    return {
        "first_high": float(first_candle['high']),
        "first_low": float(first_candle['low']),
        "first_time": first_candle.name
    }

def find_first_close_breakout(
    idxdf_15min: pd.DataFrame, 
    first_high: float, 
    first_low: float
) -> Optional[Dict[str, Any]]:
    """9:30+ candles mein pehla close breakout dhoondho"""
    # First candle skip karo (index 1 se start)
    for i in range(1, len(idxdf_15min)):
        candle = idxdf_15min.iloc[i]
        ts = candle.name
        
        if candle['close'] > first_high:
            return {
                "boside": "BUYBO",
                "breakout_time": ts,
                "breakout_price": float(candle['close']),
                "breakout_type": "CLOSE_ABOVE_FIRST_HIGH"
            }
        elif candle['close'] < first_low:
            return {
                "boside": "SELLBO", 
                "breakout_time": ts,
                "breakout_price": float(candle['close']),
                "breakout_type": "CLOSE_BELOW_FIRST_LOW"
            }
    
    return None

def apply_first15min_sync_filter(
    req: 'VixRequest',
    nifty_idxdf_1min: pd.DataFrame,
    sensex_idxdf_1min: pd.DataFrame
) -> Dict[str, Any]:
    print("DEBUG FIRST15 NIFTY SHAPE:", nifty_idxdf_1min.shape)
    print("DEBUG FIRST15 SENSEX SHAPE:", sensex_idxdf_1min.shape)
    """NIFTY + SENSEX dono ka sync first15min breakout"""
    
    # 1-min ko 15-min banao
    nifty_15min = resample_to_15min(nifty_idxdf_1min)
    sensex_15min = resample_to_15min(sensex_idxdf_1min)
    print("DEBUG NIFTY_15:", nifty_15min.shape, "SENSEX_15:", sensex_15min.shape)
    
    if len(nifty_15min) < 2 or len(sensex_15min) < 2:
        return {"status": "error", "message": "Insufficient 15-min candles"}
    
    # First 15-min (9:15-9:30) ranges
    nifty_first = get_first15min_range(nifty_15min)
    sensex_first = get_first15min_range(sensex_15min)
    
    # Pehle close breakout dhoondho (9:30+ candles)
    nifty_breakout = find_first_close_breakout(nifty_15min, 
                                              nifty_first['first_high'], 
                                              nifty_first['first_low'])
    sensex_breakout = find_first_close_breakout(sensex_15min, 
                                               sensex_first['first_high'], 
                                               sensex_first['first_low'])
    
    if not nifty_breakout or not sensex_breakout:
        return {
            "status": "no_first15min_breakout",
            "message": "No close breakout in NIFTY or SENSEX"
        }
    
    # Same 15-min candle check
    if nifty_breakout['breakout_time'] == sensex_breakout['breakout_time']:
        # SYNC SUCCESS - same candle mein dono BO
        return {
            "status": "sync_breakout",
            "nifty": nifty_breakout,
            "sensex": sensex_breakout,
            "sync_time": nifty_breakout['breakout_time'],
            "nifty_first_high": nifty_first['first_high'],
            "nifty_first_low": nifty_first['first_low'],
            "sensex_first_high": sensex_first['first_high'],
            "sensex_first_low": sensex_first['first_low']
        }
    else:
        # ALAG ALAG
        return {
            "status": "sensex_nifty_alag_alag",
            "message": "NIFTY and SENSEX breakout in different candles",
            "nifty": nifty_breakout,
            "sensex": sensex_breakout
        }
