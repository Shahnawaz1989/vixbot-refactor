import json
import pyotp
from SmartApi.smartConnect import SmartConnect

API_KEY = "BW8tznBr"
CLIENT_CODE = "M173002"
PIN = "7856"
TOTP_SECRET = "T3ZSXQE2RXR4UIBVE5FP3COUTQ"

TRADINGSYMBOL = "NIFTY24FEB2625700PE"
SYMBOLTOKEN = "64861"
LOT_SIZE = 65

smartApi = SmartConnect(api_key=API_KEY)

try:
    # 🔐 Login
    totp = pyotp.TOTP(TOTP_SECRET).now()
    login = smartApi.generateSession(CLIENT_CODE, PIN, totp)

    if not login.get("status"):
        print("❌ Login Failed:", login)
        exit()

    print("✅ Login Success")

    # 📝 Order Params
    orderparams = {
        "variety": "NORMAL",
        "tradingsymbol": TRADINGSYMBOL,
        "symboltoken": SYMBOLTOKEN,
        "transactiontype": "BUY",
        "exchange": "NFO",
        "ordertype": "MARKET",
        "producttype": "NRML",
        "duration": "DAY",
        "quantity": str(LOT_SIZE)
    }

    print("\n📤 Sending Order:")
    print(json.dumps(orderparams, indent=2))

    # 🚀 Place Order
    response = smartApi.placeOrder(orderparams)

    print("\n📥 Order Response:")
    print(json.dumps(response, indent=2))

except Exception as e:
    import traceback
    print("❌ Exception:")
    traceback.print_exc()
