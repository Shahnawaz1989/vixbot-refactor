# webhook_test.py
import requests
import hmac
import hashlib
import time
import json

CLIENT_CODE = "M173002"
SECRET_KEY = "Y7m7YmsZ"  # tumhara actual API key
# agar yeh na chale toh trade wali URL try karenge
WEBHOOK_URL = "https://apiconnect.angelone.in/webhook/order"


def angel_webhook_order(clientcode, secret_key, payload):
    timestamp = str(int(time.time()))
    message = clientcode + timestamp + json.dumps(payload)
    checksum = hmac.new(secret_key.encode(), message.encode(),
                        hashlib.sha256).hexdigest()

    data = {
        "clientcode": clientcode,
        "checksum": checksum,
        "data": payload
    }

    resp = requests.post(
        WEBHOOK_URL,
        headers={"Content-Type": "application/json"},
        json=data
    )
    return resp.status_code, resp.text


# NFO Test Order
payload = {
    "t": "bf",      # buy fresh
    "exchange": "NFO",
    "tsym": "NIFTY24MAR2625700PE",
    "qty": 1,
    "prc": 0,       # market order
    "pp": "NRML"
}

status, response = angel_webhook_order(CLIENT_CODE, SECRET_KEY, payload)
print("Status:", status)
print("Raw repr:", repr(response))
