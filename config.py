# config.py

from pathlib import Path
from datetime import time as dtime

# PROJECT ROOT (vixserver folder)
ROOT = Path(__file__).resolve().parent

# ========= GANN EXCEL =========
# NOTE: filename aapke system jaisa hai, zarurat ho to path adjust kar lena
GANN_EXCEL_PATH = ROOT / "GANN ONLY NIFTY BOT.xlsx"

# ========= SMARTAPI CREDENTIALS =========
APIKEY = "RjUByYN1"
CLIENTID = "M173002"
PASSWORD = "7856"
TOTPSECRET = "T3ZSXQE2RXR4UIBVE5FP3COUTQ"

# ========= INDEX TOKENS =========
NIFTYINDEXTOKEN = "99926000"
SENSEXINDEXTOKEN = "99919000"
VIXINDEXTOKEN = "99926017"

# ========= FILE PATHS =========
SCRIPMASTERFILE = ROOT / "OpenAPIScripMaster.json"
BACKTESTDIR = "../backtests"
EXPIRY_STORE_FILE = "../nifty_expiries.json"

# Low premium bot hata rahe hain, isliye ye dirs ki zarurat nahi:
# LOW_PREMIUM_SNAPSHOT_DIR = "../low_premium_snapshots"
# LOW_PREMIUM_BACKTEST_DIR = "../low_premium_backtests"

# ========= TIME CONFIG =========

# Risk start (agar future me zarurat ho)
RISKSTARTTIME = dtime(12, 15)

# BO default start time (agar VixRequest.bostart empty ho)
DEFAULTBOSTART = dtime(10, 14)

# NEW: ORB / marking / EOD config

# Marking candle: 10:00–10:15 (4th 15-min)
MARKING_START = dtime(10, 0)
MARKING_END = dtime(10, 15)

# ORB window end: 12:30 (12:15–12:30 candle close hone tak)
ORB_WINDOW_END = dtime(12, 30)

# EOD exit time: 15:00
EOD_EXIT_TIME = dtime(15, 0)
