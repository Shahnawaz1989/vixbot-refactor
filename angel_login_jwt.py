from SmartApi.smartConnect import SmartConnect
import pyotp

API_KEY = "BW8tznBr"      # naya wala
CLIENT_CODE = "M173002"
PIN = "7856"        # 2FA PIN
# jo tu pyotp ke liye use karta hai
TOTP_SECRET = "T3ZSXQE2RXR4UIBVE5FP3COUTQ"

smartApi = SmartConnect(api_key=API_KEY)

totp = pyotp.TOTP(TOTP_SECRET).now()
print("TOTP:", totp)

data = smartApi.generateSession(CLIENT_CODE, PIN, totp)
print("generateSession resp:", data)

if not data.get("status"):
    raise SystemExit("Login failed, response dekh:")

jwt_token = data["data"]["jwtToken"]
refreshToken = data["data"]["refreshToken"]

print("\n==== COPY THIS JWT_TOKEN FOR ORDER TEST ====\n")
print(jwt_token)
print("\n==== REFRESH TOKEN (optional) ====\n")
print(refreshToken)
