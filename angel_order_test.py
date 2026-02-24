import requests
import json

# ====== YAHAN APNI DETAILS BHARO ======
APIKEY = "hbeV0h3A"  # already sahi hai

JWT_TOKEN = "eyJhbGciOiJIUzUxMiJ9.eyJ1c2VybmFtZSI6Ik0xNzMwMDIiLCJyb2xlcyI6MCwidXNlcnR5cGUiOiJVU0VSIiwidG9rZW4iOiJleUpoYkdjaU9pSlNVekkxTmlJc0luUjVjQ0k2SWtwWFZDSjkuZXlKMWMyVnlYM1I1Y0dVaU9pSmpiR2xsYm5RaUxDSjBiMnRsYmw5MGVYQmxJam9pZEhKaFpHVmZZV05qWlhOelgzUnZhMlZ1SWl3aVoyMWZhV1FpT2pRc0luTnZkWEpqWlNJNklqTWlMQ0prWlhacFkyVmZhV1FpT2lKaFkyRmxNR05qWWkwNU9USXpMVE0wT1dZdE9EaGtaQzA0T0Rrek5HTXhOVGN5T0RNaUxDSnJhV1FpT2lKMGNtRmtaVjlyWlhsZmRqSWlMQ0p2Ylc1bGJXRnVZV2RsY21sa0lqbzBMQ0p3Y205a2RXTjBjeUk2ZXlKa1pXMWhkQ0k2ZXlKemRHRjBkWE1pT2lKaFkzUnBkbVVpZlN3aWJXWWlPbnNpYzNSaGRIVnpJam9pWVdOMGFYWmxJbjE5TENKcGMzTWlPaUowY21Ga1pWOXNiMmRwYmw5elpYSjJhV05sSWl3aWMzVmlJam9pVFRFM016QXdNaUlzSW1WNGNDSTZNVGMzTVRrMU1UazJNU3dpYm1KbUlqb3hOemN4T0RZMU16Z3hMQ0pwWVhRaU9qRTNOekU0TmpVek9ERXNJbXAwYVNJNklqQTNNVE5oWVRkbExUUXpaR1F0TkdRMllTMDVOMk15TFRobU1XTXpNekEwWTJFM1pDSXNJbFJ2YTJWdUlqb2lJbjAuWmpnX2lickxHdHFQRkpNT2pBa1dyaDA4anVMeXdvOHFRUXdMOThyUHlVNTVPZW93YkdrSnpvNm1vTnBwRnA2V0ZSTFpBV01nQkdMdWtOcHZmSFBZdTQzdDY4T3VwVmk4WVptTWd2QXNCY3dSdlBnbmhneWtENXRPTnRYQ2RqYnk0MXc5NmNPQWYtNkROUFJuNk1SUjIzcWtlbU8zaVBmM09RRE1Fd2pFbXZV"

# NFO option jiska token tumne diya hai
TRADINGSYMBOL = "NIFTY24MAR2625700PE"
SYMBOLTOKEN = "62792"

# ======================================

url = "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/placeOrder"

headers = {
    "X-API-Key": APIKEY,
    "Authorization": f"Bearer {JWT_TOKEN}",
    "X-ClientLocalIP": "127.0.0.1",
    "X-ClientPublicIP": "127.0.0.1",
    "X-MACAddress": "00:00:00:00:00:00",
    "X-UserType": "USER",
    "X-SourceID": "WEB",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

payload = {
    "variety": "NORMAL",
    "tradingsymbol": TRADINGSYMBOL,
    "symboltoken": SYMBOLTOKEN,
    "transactiontype": "BUY",
    "exchange": "NFO",
    "ordertype": "MARKET",
    "producttype": "NRML",
    "duration": "DAY",
    "price": "0",
    "triggerprice": "0",
    "quantity": "1"
}

print("=== SENDING HTTP ORDER TEST ===")
print("URL:", url)
print("Headers:", json.dumps(headers, indent=2))
print("Payload:", json.dumps(payload, indent=2))

resp = requests.post(url, json=payload, headers=headers)

print("\n=== RESPONSE ===")
print("Status code:", resp.status_code)
print("Raw body repr:", repr(resp.text))
try:
    print("JSON body:", resp.json())
except Exception:
    print("JSON parse failed (maybe empty body).")
